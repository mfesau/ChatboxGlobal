"""Acceso a datos. Concentra aquí toda sentencia SQL de la capa de dominio.

Cada función recibe una ``AsyncSession`` ya abierta: la transacción la controla
quien orquesta, no el repositorio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Text, cast, delete, func, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.business_hours import ServicePolicy, add_business_minutes
from app.core.envelope import (
    ChannelKind,
    ConversationRef,
    DeliveryStatus,
    Direction,
    InboundMessage,
    OutboundMessage,
    Party,
    utcnow,
)
from app.core.security import hash_password
from app.db.models import (
    ROLE_ADMIN,
    ROLE_SUPERVISOR,
    Agent,
    AgentDepartment,
    AgentSession,
    AIRun,
    Assignment,
    AuditLog,
    CannedResponse,
    ChannelAccount,
    Contact,
    ContactComment,
    ContactIdentity,
    ContactSession,
    Conversation,
    ConversationLabel,
    Department,
    HotelRatePlan,
    HotelReservation,
    HotelRoom,
    HotelRoomType,
    InboundDedupe,
    InternalNote,
    Label,
    Macro,
    Message,
    MessageEvent,
    OutboxItem,
    SavedView,
    Tenant,
)

#: Retroceso exponencial acotado, en segundos, por número de intento.
RETRY_BACKOFF_S = (5, 30, 120, 600, 1_800, 3_600)


def as_utc(value: datetime) -> datetime:
    """Normaliza a UTC consciente de zona una fecha leída de la base.

    PostgreSQL devuelve ``timestamptz`` con zona, pero SQLite —empleado en las
    pruebas— la pierde. Comparar una fecha sin zona con ``utcnow()`` lanzaría
    ``TypeError``, de modo que se asume UTC cuando falta, que es lo que se
    escribió.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _dialect_name(session: AsyncSession) -> str:
    try:
        return session.get_bind().dialect.name
    except Exception:  # pragma: no cover - sesión sin motor asociado
        return ""


def _insert_ignore(
    session: AsyncSession, table: Any, values: dict[str, Any], conflict: list[str]
) -> Any:
    """Construye ``INSERT ... ON CONFLICT DO NOTHING`` para el dialecto activo.

    Se evita a propósito el patrón «insertar y capturar ``IntegrityError``»:
    exige un *savepoint* por intento y, sobre SQLite, deja inservible la
    transacción externa. Con esta variante la carrera entre peticiones
    concurrentes la resuelve el propio motor, sin excepciones y en un solo viaje.
    """
    insert = sqlite.insert if _dialect_name(session) == "sqlite" else postgresql.insert
    return insert(table).values(**values).on_conflict_do_nothing(index_elements=conflict)


# --------------------------------------------------------------------------- #
# Organización
# --------------------------------------------------------------------------- #
async def get_or_create_tenant(
    session: AsyncSession, slug: str, name: str | None = None
) -> Tenant:
    stmt = select(Tenant).where(Tenant.slug == slug)
    tenant = (await session.execute(stmt)).scalar_one_or_none()
    if tenant is not None:
        return tenant

    await session.execute(
        _insert_ignore(
            session,
            Tenant,
            {"slug": slug, "name": name or slug.replace("-", " ").title()},
            ["slug"],
        )
    )
    # Tras la inserción idempotente la fila existe, la haya escrito esta
    # petición o una concurrente.
    return (await session.execute(stmt)).scalar_one()


async def update_tenant_settings(session: AsyncSession, tenant: Tenant, **changes: Any) -> Tenant:
    """Actualiza claves sueltas de ``Tenant.settings`` sin pisar las demás.

    Reasigna el diccionario en vez de mutarlo en el sitio: es lo que hace que
    SQLAlchemy detecte el cambio en una columna ``JSONB``.
    """
    tenant.settings = {**tenant.settings, **changes}
    await session.flush()
    return tenant


async def get_or_create_channel_account(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: ChannelKind,
    external_id: str,
    display_name: str | None = None,
) -> ChannelAccount:
    stmt = select(ChannelAccount).where(
        ChannelAccount.channel == channel, ChannelAccount.external_id == external_id
    )
    account = (await session.execute(stmt)).scalar_one_or_none()
    if account is not None:
        return account

    await session.execute(
        _insert_ignore(
            session,
            ChannelAccount,
            {
                "tenant_id": tenant_id,
                "channel": channel,
                "external_id": external_id,
                "display_name": display_name,
            },
            ["channel", "external_id"],
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def get_channel_account(
    session: AsyncSession, account_id: uuid.UUID
) -> ChannelAccount | None:
    return (
        await session.execute(
            select(ChannelAccount)
            .where(ChannelAccount.id == account_id)
            .options(selectinload(ChannelAccount.department))
        )
    ).scalar_one_or_none()


async def find_channel_account(
    session: AsyncSession, *, channel: ChannelKind, external_id: str
) -> ChannelAccount | None:
    """Lectura simple, a diferencia de :func:`get_or_create_channel_account`:
    no crea la fila si falta. Para resolver credenciales al enviar, donde la
    cuenta ya debería existir desde que llegó el primer mensaje entrante.
    """
    return (
        await session.execute(
            select(ChannelAccount).where(
                ChannelAccount.channel == channel, ChannelAccount.external_id == external_id
            )
        )
    ).scalar_one_or_none()


async def list_channel_accounts(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[ChannelAccount]:
    stmt = (
        select(ChannelAccount)
        .where(ChannelAccount.tenant_id == tenant_id)
        .options(selectinload(ChannelAccount.department))
        .order_by(ChannelAccount.channel, ChannelAccount.display_name, ChannelAccount.external_id)
    )
    return list((await session.execute(stmt)).scalars())


async def list_channel_credential_blobs(
    session: AsyncSession, *, channel: ChannelKind
) -> list[str]:
    """Credenciales cifradas de las cuentas activas de ese canal.

    Sin acotar por inquilino a propósito: un webhook de Meta no dice a qué
    inquilino pertenece —solo trae una firma—, así que para comprobarla hay que
    considerar todas las cuentas dadas de alta. Solo salen las activas: una
    cuenta desactivada no debe seguir autorizando entradas.
    """
    stmt = select(ChannelAccount.credentials_ciphertext).where(
        ChannelAccount.channel == channel,
        ChannelAccount.is_active.is_(True),
        ChannelAccount.credentials_ciphertext.is_not(None),
    )
    return [blob for blob in (await session.execute(stmt)).scalars() if blob]


async def create_channel_account(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: ChannelKind,
    external_id: str,
    display_name: str | None = None,
    department_id: uuid.UUID | None = None,
    credentials_ciphertext: str | None = None,
) -> ChannelAccount:
    """Alta explícita desde la consola, a diferencia de
    :func:`get_or_create_channel_account`, que solo se dispara sola cuando
    llega el primer mensaje de un número/página/equipo nunca visto.
    """
    account = ChannelAccount(
        tenant_id=tenant_id,
        channel=channel,
        external_id=external_id.strip(),
        display_name=display_name,
        department_id=department_id,
        credentials_ciphertext=credentials_ciphertext,
    )
    session.add(account)
    await session.flush()
    return account


async def update_channel_account(
    session: AsyncSession, account: ChannelAccount, **changes: Any
) -> ChannelAccount:
    for key, value in changes.items():
        setattr(account, key, value)
    await session.flush()
    return account


async def count_conversations_of_account(
    session: AsyncSession, account_id: uuid.UUID
) -> int:
    """Cuántas conversaciones entraron por esta cuenta.

    Se consulta antes de borrarla: el número es lo que permite avisar de lo
    que hay detrás en vez de dejar que alguien lo descubra después.
    """
    return (
        await session.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.channel_account_id == account_id)
        )
    ).scalar_one()


async def delete_agent(session: AsyncSession, agent: Agent) -> None:
    """Borra la cuenta y todo rastro de quien la usaba.

    Las claves foraneas hacia ``agents`` son ``SET NULL`` o ``CASCADE``, de modo
    que el historial no desaparece: las derivaciones, las notas y los mensajes
    siguen ahi, pero **sin nombre**. Esa es la diferencia real con desactivar,
    que conserva la fila justamente para que el historial se pueda leer.

    Se borran con la fila las sesiones abiertas, los departamentos adicionales
    y las vistas guardadas propias, que solo tenian sentido para esa persona.
    """
    await session.delete(agent)
    await session.flush()


async def delete_channel_account(session: AsyncSession, account: ChannelAccount) -> None:
    """Borra la cuenta. Sus conversaciones quedan, sin cuenta asociada.

    La clave foránea es ``ON DELETE SET NULL`` a propósito: el historial de lo
    que un cliente escribió no puede desaparecer porque se dé de baja el
    número por el que escribió.
    """
    await session.delete(account)
    await session.flush()


# --------------------------------------------------------------------------- #
# Contactos
# --------------------------------------------------------------------------- #
async def resolve_contact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: ChannelKind,
    party: Party,
) -> Contact:
    """Devuelve el contacto asociado a la identidad de canal, creándolo si falta.

    La unificación entre canales se produce por teléfono o correo electrónico
    cuando el proveedor los facilita; en caso contrario se crea un contacto nuevo.
    """
    identity_stmt = select(ContactIdentity).where(
        ContactIdentity.tenant_id == tenant_id,
        ContactIdentity.channel == channel,
        ContactIdentity.channel_user_id == party.channel_user_id,
    )
    identity = (await session.execute(identity_stmt)).scalar_one_or_none()
    if identity is not None:
        contact = await session.get(Contact, identity.contact_id)
        if contact is not None:
            _enrich_contact(contact, party)
            return contact

    contact = await _find_contact_by_reachability(session, tenant_id=tenant_id, party=party)
    if contact is None:
        contact = Contact(
            tenant_id=tenant_id,
            display_name=party.display_name,
            primary_phone=party.phone,
            primary_email=party.email,
            locale=party.locale,
        )
        session.add(contact)
        await session.flush()

    await session.execute(
        _insert_ignore(
            session,
            ContactIdentity,
            {
                "tenant_id": tenant_id,
                "contact_id": contact.id,
                "channel": channel,
                "channel_user_id": party.channel_user_id,
                "display_name": party.display_name,
                "raw": party.raw,
            },
            ["tenant_id", "channel", "channel_user_id"],
        )
    )

    identity = (await session.execute(identity_stmt)).scalar_one()
    if identity.contact_id == contact.id:
        return contact
    # Otra petición concurrente ganó la carrera: se adopta el contacto vencedor.
    winner = await session.get(Contact, identity.contact_id)
    return winner or contact


async def _find_contact_by_reachability(
    session: AsyncSession, *, tenant_id: uuid.UUID, party: Party
) -> Contact | None:
    if party.phone:
        found = (
            await session.execute(
                select(Contact).where(
                    Contact.tenant_id == tenant_id, Contact.primary_phone == party.phone
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            return found
    if party.email:
        return (
            await session.execute(
                select(Contact).where(
                    Contact.tenant_id == tenant_id, Contact.primary_email == party.email
                )
            )
        ).scalar_one_or_none()
    return None


def _enrich_contact(contact: Contact, party: Party) -> None:
    """Completa los huecos del contacto sin sobrescribir datos verificados."""
    if party.display_name and not contact.display_name:
        contact.display_name = party.display_name
    if party.phone and not contact.primary_phone:
        contact.primary_phone = party.phone
    if party.email and not contact.primary_email:
        contact.primary_email = party.email
    if party.locale and not contact.locale:
        contact.locale = party.locale


# --------------------------------------------------------------------------- #
# Cuentas y sesiones del chatbox público
# --------------------------------------------------------------------------- #
async def find_contact_by_email(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str
) -> Contact | None:
    return (
        await session.execute(
            select(Contact).where(
                Contact.tenant_id == tenant_id,
                func.lower(Contact.primary_email) == email.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def create_contact_account(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    display_name: str | None,
    password_hash: str,
) -> Contact:
    contact = Contact(
        tenant_id=tenant_id,
        primary_email=email.strip().lower(),
        display_name=display_name,
        password_hash=password_hash,
    )
    session.add(contact)
    await session.flush()
    return contact


async def open_contact_session(
    session: AsyncSession,
    *,
    contact_id: uuid.UUID,
    token_hash: str,
    ttl: timedelta,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> ContactSession:
    record = ContactSession(
        token_hash=token_hash,
        contact_id=contact_id,
        expires_at=utcnow() + ttl,
        client_ip=client_ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    session.add(record)
    await session.flush()
    return record


async def resolve_contact_session(session: AsyncSession, token_hash: str) -> Contact | None:
    """Traduce el token en contacto activo, descartando sesiones caducadas."""
    record = await session.get(ContactSession, token_hash)
    if record is None:
        return None
    if as_utc(record.expires_at) <= utcnow():
        await session.delete(record)
        return None

    contact = await session.get(Contact, record.contact_id)
    if contact is None or contact.is_blocked:
        await session.delete(record)
        return None

    record.last_used_at = utcnow()
    return contact


async def close_contact_session(session: AsyncSession, token_hash: str) -> None:
    record = await session.get(ContactSession, token_hash)
    if record is not None:
        await session.delete(record)


# --------------------------------------------------------------------------- #
# Ficha del contacto: datos y comentarios de supervisión
# --------------------------------------------------------------------------- #
async def get_contact(session: AsyncSession, contact_id: uuid.UUID) -> Contact | None:
    return await session.get(Contact, contact_id)


#: Marca "no se recibió este campo", distinta de recibirlo como ``None`` para
#: borrarlo deliberadamente.
_UNSET: Any = object()


async def update_contact(
    session: AsyncSession,
    *,
    contact_id: uuid.UUID,
    display_name: str | None = _UNSET,
    primary_phone: str | None = _UNSET,
    primary_email: str | None = _UNSET,
) -> Contact:
    """Sobrescribe solo los campos recibidos; los demás quedan como estaban."""
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise ValueError(f"Contacto {contact_id} no encontrado")
    if display_name is not _UNSET:
        contact.display_name = display_name
    if primary_phone is not _UNSET:
        contact.primary_phone = primary_phone
    if primary_email is not _UNSET:
        contact.primary_email = primary_email.strip().lower() if primary_email else None
    await session.flush()
    return contact


async def add_contact_comment(
    session: AsyncSession,
    *,
    contact_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    body: str,
) -> ContactComment:
    comment = ContactComment(
        tenant_id=tenant_id, contact_id=contact_id, agent_id=agent_id, body=body
    )
    session.add(comment)
    await session.flush()
    return comment


async def list_contact_comments(
    session: AsyncSession, contact_id: uuid.UUID, limit: int = 100
) -> list[ContactComment]:
    stmt = (
        select(ContactComment)
        .where(ContactComment.contact_id == contact_id)
        .order_by(ContactComment.created_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def list_contacts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[tuple[Contact, int, datetime | None]]:
    """Directorio de contactos con su número de conversaciones y última
    actividad, para el listado global de la consola (solo supervisión)."""
    stmt = (
        select(Contact, func.count(Conversation.id), func.max(Conversation.last_message_at))
        .outerjoin(Conversation, Conversation.contact_id == Contact.id)
        .where(Contact.tenant_id == tenant_id)
        .group_by(Contact.id)
        .order_by(func.max(Conversation.last_message_at).desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.display_name).like(pattern),
                func.lower(Contact.primary_email).like(pattern),
                Contact.primary_phone.like(f"%{search.strip()}%"),
            )
        )
    return [(row[0], row[1], row[2]) for row in (await session.execute(stmt)).all()]


async def search_contacts(
    session: AsyncSession, *, tenant_id: uuid.UUID, search: str, limit: int = 20
) -> list[Contact]:
    """Búsqueda simple por nombre, correo o teléfono, sin las estadísticas de
    ``list_contacts``: la usa cualquier agente al vincular un contacto
    conocido a una reserva de hotel, no solo supervisión."""
    pattern = f"%{search.strip().lower()}%"
    stmt = (
        select(Contact)
        .where(
            Contact.tenant_id == tenant_id,
            or_(
                func.lower(Contact.display_name).like(pattern),
                func.lower(Contact.primary_email).like(pattern),
                Contact.primary_phone.like(f"%{search.strip()}%"),
            ),
        )
        .order_by(Contact.display_name)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def list_conversations_for_contact(
    session: AsyncSession, contact_id: uuid.UUID
) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.contact_id == contact_id)
        .options(
            selectinload(Conversation.contact),
            selectinload(Conversation.assignee),
            selectinload(Conversation.department),
            selectinload(Conversation.labels),
        )
        .order_by(Conversation.last_message_at.desc().nulls_last())
    )
    return list((await session.execute(stmt)).scalars())


# --------------------------------------------------------------------------- #
# Conversaciones
# --------------------------------------------------------------------------- #
async def resolve_conversation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    ref: ConversationRef,
    contact_id: uuid.UUID | None,
    channel_account: ChannelAccount | None = None,
) -> Conversation:
    stmt = select(Conversation).where(
        Conversation.tenant_id == tenant_id,
        Conversation.channel == ref.channel,
        Conversation.channel_conversation_id == ref.channel_conversation_id,
    )
    conversation = (await session.execute(stmt)).scalar_one_or_none()
    if conversation is not None:
        # La ``service_url`` del Bot Framework puede rotar: siempre se refresca.
        conversation.conversation_ref = ref.to_dict()
        if contact_id and not conversation.contact_id:
            conversation.contact_id = contact_id
        if conversation.status == "closed":
            conversation.status = "open"
        return conversation

    await session.execute(
        _insert_ignore(
            session,
            Conversation,
            {
                "tenant_id": tenant_id,
                "channel": ref.channel,
                "channel_conversation_id": ref.channel_conversation_id,
                "channel_account_id": channel_account.id if channel_account else None,
                # Enrutado automático: una cuenta de canal sin departamento
                # (el caso de hoy) deja la conversación en la cola común,
                # exactamente como antes de que existiera este campo.
                "department_id": channel_account.department_id if channel_account else None,
                "contact_id": contact_id,
                "conversation_ref": ref.to_dict(),
            },
            ["tenant_id", "channel", "channel_conversation_id"],
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def find_message_by_attachment(
    session: AsyncSession, *, tenant_id: uuid.UUID, stored_name: str
) -> Message | None:
    """Localiza el mensaje que contiene el adjunto con ese nombre de fichero.

    Los adjuntos viven dentro de ``messages.attachments``, de modo que la
    búsqueda se hace sobre el JSON convertido a texto. El nombre es un UUID de
    32 dígitos hexadecimales, así que la coincidencia por subcadena es
    inequívoca. Se ejecuta una vez por descarga, no en el camino caliente del
    turno de conversación.
    """
    stmt = (
        select(Message)
        .where(
            Message.tenant_id == tenant_id,
            cast(Message.attachments, Text).contains(stored_name),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def find_conversation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: ChannelKind,
    channel_conversation_id: str,
) -> Conversation | None:
    """Localiza un hilo por su identificador de canal, sin crearlo."""
    return (
        await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.channel == channel,
                Conversation.channel_conversation_id == channel_conversation_id,
            )
        )
    ).scalar_one_or_none()


async def get_conversation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


def _restrict_to_status(stmt: Any, status: str | None) -> Any:
    """Acota por estado, admitiendo varios separados por comas.

    La bandeja necesita pedir «lo que no está resuelto», que son dos estados a
    la vez: lo pendiente y lo que alguien ya está atendiendo. Sin esto, marcar
    una conversación como «en proceso» la haría desaparecer de la vista de
    quien acababa de tomarla.
    """
    if not status:
        return stmt
    wanted = [part.strip() for part in status.split(",") if part.strip()]
    if not wanted:
        return stmt
    if len(wanted) == 1:
        return stmt.where(Conversation.status == wanted[0])
    return stmt.where(Conversation.status.in_(wanted))


def _restrict_to_accessible_departments(stmt: Any, department_ids: set[uuid.UUID] | None) -> Any:
    """Acota la cola —solo ``assignee_id`` nulo— a los departamentos accesibles.

    ``None`` significa sin restricción (administración). Una conversación sin
    departamento sigue visible para cualquiera: acotar por departamento es
    una restricción adicional sobre la cola, nunca sobre lo que ya tiene
    dueño (lo propio de un agente, o lo ya asignado que ve supervisión).
    """
    if department_ids is None:
        return stmt
    return stmt.where(
        or_(
            Conversation.assignee_id.is_not(None),
            Conversation.department_id.is_(None),
            Conversation.department_id.in_(department_ids),
        )
    )


def _restrict_to_departments(stmt: Any, department_ids: set[uuid.UUID] | None) -> Any:
    """Acota a los departamentos accesibles, sin excepción por dueño.

    A diferencia de ``_restrict_to_accessible_departments`` (pensada para no
    ocultarle a un agente lo que ya es suyo), esta acota la vista ``all`` de
    supervisión: ver «todo» para una supervisión acotada por departamento
    significa todo lo de sus departamentos, tenga o no dueño.
    """
    if department_ids is None:
        return stmt
    return stmt.where(
        or_(
            Conversation.department_id.is_(None),
            Conversation.department_id.in_(department_ids),
        )
    )


async def list_conversations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = "open",
    channel: ChannelKind | None = None,
    scope: str = "all",
    agent_id: uuid.UUID | None = None,
    department_ids: set[uuid.UUID] | None = None,
    department: uuid.UUID | None = None,
    label: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Conversation]:
    """Devuelve la bandeja según el alcance solicitado.

    ``scope`` acepta:

    * ``unassigned`` — la cola común: el punto único por donde entra todo.
    * ``mine`` — solo lo asignado al agente indicado.
    * ``mine_or_unassigned`` — su carga de trabajo más lo que puede tomar.
    * ``all`` — la totalidad del inquilino; reservado a supervisión.

    ``department_ids`` acota la vista a los departamentos que puede atender
    quien pregunta (``None`` = sin restricción); ``department`` es un filtro
    de interfaz para ver solo un departamento concreto de entre los propios,
    nunca una forma de ver más de lo que ``department_ids`` ya permite.

    El filtro se aplica en SQL y no en memoria: un agente jamás recibe filas
    ajenas que después haya que descartar.
    """
    stmt = select(Conversation).where(Conversation.tenant_id == tenant_id)
    stmt = _restrict_to_status(stmt, status)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if department:
        stmt = stmt.where(Conversation.department_id == department)
    if label:
        stmt = stmt.where(Conversation.labels.any(Label.id == label))

    if scope == "unassigned":
        stmt = stmt.where(Conversation.assignee_id.is_(None))
        stmt = _restrict_to_accessible_departments(stmt, department_ids)
    elif scope == "mine":
        stmt = stmt.where(Conversation.assignee_id == agent_id)
    elif scope == "mine_or_unassigned":
        stmt = stmt.where(
            or_(Conversation.assignee_id == agent_id, Conversation.assignee_id.is_(None))
        )
        stmt = _restrict_to_accessible_departments(stmt, department_ids)
    else:
        stmt = _restrict_to_departments(stmt, department_ids)

    stmt = (
        stmt.options(
            selectinload(Conversation.contact),
            selectinload(Conversation.assignee),
            selectinload(Conversation.department),
            selectinload(Conversation.labels),
        )
        .order_by(Conversation.last_message_at.desc().nulls_last())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars())


async def count_conversations(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    scope: str = "all",
    agent_id: uuid.UUID | None = None,
    department_ids: set[uuid.UUID] | None = None,
    status: str | None = "open",
) -> int:
    """Recuento para las pestañas de la consola, sin traer las filas."""
    stmt = select(func.count()).select_from(Conversation).where(
        Conversation.tenant_id == tenant_id
    )
    stmt = _restrict_to_status(stmt, status)
    if scope == "unassigned":
        stmt = stmt.where(Conversation.assignee_id.is_(None))
        stmt = _restrict_to_accessible_departments(stmt, department_ids)
    elif scope == "mine":
        stmt = stmt.where(Conversation.assignee_id == agent_id)
    else:
        stmt = _restrict_to_departments(stmt, department_ids)
    return (await session.execute(stmt)).scalar_one()


def agent_department_ids(agent: Agent) -> set[uuid.UUID]:
    """Departamentos que un agente puede atender: el propio más los otorgados.

    Función pura: usa lo ya cargado en memoria (``granted_departments`` debe
    haberse traído con ``selectinload`` al resolver la sesión), sin consultar
    la base de nuevo.
    """
    ids = {department.id for department in agent.granted_departments}
    if agent.department_id is not None:
        ids.add(agent.department_id)
    return ids


def agent_can_access(
    conversation: Conversation, agent: Agent, department_ids: set[uuid.UUID] | None = None
) -> bool:
    """Regla de visibilidad de una conversación concreta.

    Administración ve todo. Supervisión ve todo lo de sus departamentos,
    tenga o no dueño (a diferencia de un agente, no necesita que la
    conversación esté libre para verla). Un agente ve lo propio y lo que aún
    no tiene dueño, porque la cola común es precisamente el punto del que ha
    de poder tomar trabajo — salvo que esa conversación sin dueño ya quedó
    derivada a un departamento al que no tiene acceso.
    """
    if agent.role == ROLE_ADMIN:
        return True
    accessible = department_ids if department_ids is not None else agent_department_ids(agent)
    if agent.role == ROLE_SUPERVISOR:
        return conversation.department_id is None or conversation.department_id in accessible
    if conversation.assignee_id not in (None, agent.id):
        return False
    if conversation.assignee_id == agent.id:
        return True
    if conversation.department_id is None:
        return True
    return conversation.department_id in accessible


async def set_conversation_control(
    session: AsyncSession, conversation_id: uuid.UUID, control: str
) -> None:
    """Alterna entre respuesta automática (``bot``) y atención humana (``human``)."""
    await session.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(control=control, updated_at=utcnow())
    )


# --------------------------------------------------------------------------- #
# Idempotencia
# --------------------------------------------------------------------------- #
async def claim_dedupe_key(session: AsyncSession, key: str, channel: ChannelKind) -> bool:
    """Reserva la clave. Devuelve ``False`` si el evento ya se procesó."""
    result = await session.execute(
        _insert_ignore(
            session,
            InboundDedupe,
            {"dedupe_key": key, "channel": channel},
            ["dedupe_key"],
        )
    )
    return (result.rowcount or 0) > 0


async def purge_dedupe_keys(session: AsyncSession, older_than: timedelta) -> int:
    cutoff = datetime.now(UTC) - older_than
    result = await session.execute(
        delete(InboundDedupe).where(InboundDedupe.received_at < cutoff)
    )
    return result.rowcount or 0


# --------------------------------------------------------------------------- #
# Mensajes
# --------------------------------------------------------------------------- #
async def record_inbound(
    session: AsyncSession,
    *,
    conversation: Conversation,
    contact_id: uuid.UUID | None,
    inbound: InboundMessage,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        tenant_id=conversation.tenant_id,
        channel=inbound.channel,
        direction=Direction.INBOUND,
        content_type=inbound.content_type,
        status=DeliveryStatus.DELIVERED,
        provider_message_id=inbound.provider_message_id,
        text=inbound.text,
        attachments=[a.to_dict() for a in inbound.attachments],
        action=inbound.action,
        author_type="contact",
        author_contact_id=contact_id,
        sent_at=inbound.timestamp,
        raw=inbound.raw,
    )
    session.add(message)
    conversation.last_message_at = inbound.timestamp
    conversation.unread_count += 1
    await session.flush()
    return message


async def record_outbound(
    session: AsyncSession,
    *,
    conversation: Conversation,
    outbound: OutboundMessage,
    author_type: str = "bot",
    author_agent_id: uuid.UUID | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        tenant_id=conversation.tenant_id,
        channel=conversation.channel,
        direction=Direction.OUTBOUND,
        content_type=outbound.content_type,
        status=DeliveryStatus.PENDING,
        client_message_id=outbound.client_message_id,
        text=outbound.text,
        attachments=[a.to_dict() for a in outbound.attachments],
        action={"quick_replies": outbound.quick_replies} if outbound.quick_replies else None,
        author_type=author_type,
        author_agent_id=author_agent_id,
        raw={"channel_data": outbound.channel_data},
    )
    session.add(message)
    conversation.last_message_at = utcnow()
    await session.flush()
    return message


async def recent_messages(
    session: AsyncSession, conversation_id: uuid.UUID, limit: int = 20
) -> list[Message]:
    """Devuelve los últimos mensajes en orden cronológico ascendente."""
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars())
    return list(reversed(rows))


async def apply_delivery_update(
    session: AsyncSession,
    *,
    channel: ChannelKind,
    provider_message_id: str,
    status: DeliveryStatus,
    provider_status: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Message | None:
    """Aplica un acuse de recibo del proveedor al mensaje correspondiente."""
    message = (
        await session.execute(
            select(Message).where(
                Message.channel == channel,
                Message.provider_message_id == provider_message_id,
            )
        )
    ).scalar_one_or_none()
    if message is None:
        return None

    if _status_rank(status) >= _status_rank(message.status):
        message.status = status
    session.add(
        MessageEvent(
            message_id=message.id,
            status=status,
            provider_status=provider_status,
            error_code=error_code,
            error_detail=error_detail,
            payload=payload or {},
        )
    )
    return message


_STATUS_ORDER = {
    DeliveryStatus.PENDING: 0,
    DeliveryStatus.SENT: 1,
    DeliveryStatus.DELIVERED: 2,
    DeliveryStatus.READ: 3,
    DeliveryStatus.FAILED: 4,
}


def _status_rank(status: DeliveryStatus) -> int:
    return _STATUS_ORDER.get(status, 0)


# --------------------------------------------------------------------------- #
# Cola de salida
# --------------------------------------------------------------------------- #
async def enqueue_outbound(
    session: AsyncSession,
    *,
    conversation: Conversation,
    message: Message,
    ref: ConversationRef,
    outbound: OutboundMessage,
) -> OutboxItem:
    item = OutboxItem(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        message_id=message.id,
        channel=conversation.channel,
        payload={"ref": ref.to_dict(), "message": outbound.to_dict()},
    )
    session.add(item)
    await session.flush()
    return item


async def claim_outbox_batch(
    session: AsyncSession, *, worker_id: str, limit: int = 10
) -> list[OutboxItem]:
    """Toma en exclusiva un lote de envíos pendientes.

    En PostgreSQL usa ``FOR UPDATE SKIP LOCKED``, lo que permite escalar a varios
    trabajadores sin coordinación externa.
    """
    now = utcnow()
    stmt = (
        select(OutboxItem)
        .where(OutboxItem.status == "pending", OutboxItem.next_attempt_at <= now)
        .order_by(OutboxItem.next_attempt_at)
        .limit(limit)
    )
    if _dialect_name(session) == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    items = list((await session.execute(stmt)).scalars())
    for item in items:
        item.status = "in_progress"
        item.locked_by = worker_id
        item.locked_at = now
    await session.flush()
    return items


async def mark_outbox_sent(
    session: AsyncSession, item_id: uuid.UUID, provider_message_id: str | None
) -> None:
    item = await session.get(OutboxItem, item_id)
    if item is None:
        return
    item.status = "sent"
    item.attempts += 1
    item.locked_by = None
    item.locked_at = None
    if item.message_id:
        message = await session.get(Message, item.message_id)
        if message is not None:
            message.status = DeliveryStatus.SENT
            message.provider_message_id = provider_message_id
            message.sent_at = utcnow()
            session.add(
                MessageEvent(
                    message_id=message.id,
                    status=DeliveryStatus.SENT,
                    payload={"outbox_id": str(item.id)},
                )
            )


async def mark_outbox_failed(
    session: AsyncSession,
    item_id: uuid.UUID,
    *,
    error: str,
    retryable: bool,
    max_attempts: int,
) -> None:
    """Reprograma el envío o lo marca como no recuperable."""
    item = await session.get(OutboxItem, item_id)
    if item is None:
        return
    item.attempts += 1
    item.last_error = error[:2000]
    item.locked_by = None
    item.locked_at = None

    if retryable and item.attempts < max_attempts:
        delay = RETRY_BACKOFF_S[min(item.attempts - 1, len(RETRY_BACKOFF_S) - 1)]
        item.status = "pending"
        item.next_attempt_at = utcnow() + timedelta(seconds=delay)
        return

    item.status = "dead" if retryable else "failed"
    if item.message_id:
        message = await session.get(Message, item.message_id)
        if message is not None:
            message.status = DeliveryStatus.FAILED
            session.add(
                MessageEvent(
                    message_id=message.id,
                    status=DeliveryStatus.FAILED,
                    error_detail=error[:2000],
                    payload={"attempts": item.attempts},
                )
            )


async def requeue_stale_outbox(session: AsyncSession, *, older_than: timedelta) -> int:
    """Recupera envíos bloqueados por un trabajador que terminó de forma abrupta."""
    cutoff = utcnow() - older_than
    result = await session.execute(
        update(OutboxItem)
        .where(OutboxItem.status == "in_progress", OutboxItem.locked_at < cutoff)
        .values(status="pending", locked_by=None, locked_at=None)
    )
    return result.rowcount or 0


# --------------------------------------------------------------------------- #
# Observabilidad
# --------------------------------------------------------------------------- #
async def record_ai_run(session: AsyncSession, **fields: Any) -> None:
    session.add(AIRun(**fields))


async def record_audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    actor: str,
    action: str,
    subject_type: str | None = None,
    subject_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            detail=detail or {},
        )
    )


async def channel_stats(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    department_ids: set[uuid.UUID] | None = None,
) -> dict[str, int]:
    """Recuento de mensajes por canal, para el panel de la consola.

    ``department_ids`` acota a una supervisión restringida por departamento
    (``None`` = sin restricción, el caso de administración).
    """
    stmt = (
        select(Message.channel, func.count())
        .where(Message.tenant_id == tenant_id)
        .group_by(Message.channel)
    )
    if department_ids is not None:
        stmt = stmt.join(Conversation, Conversation.id == Message.conversation_id)
        stmt = _restrict_to_departments(stmt, department_ids)
    return {str(channel): total for channel, total in (await session.execute(stmt)).all()}


# --------------------------------------------------------------------------- #
# Agentes y sesiones de consola
# --------------------------------------------------------------------------- #
async def get_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent | None:
    return await session.get(Agent, agent_id)


async def find_agent_by_email(
    session: AsyncSession, *, tenant_id: uuid.UUID, email: str
) -> Agent | None:
    return (
        await session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant_id, func.lower(Agent.email) == email.strip().lower())
            .options(selectinload(Agent.granted_departments))
        )
    ).scalar_one_or_none()


async def list_agents(
    session: AsyncSession, *, tenant_id: uuid.UUID, only_active: bool = True
) -> list[Agent]:
    stmt = select(Agent).where(Agent.tenant_id == tenant_id).options(
        selectinload(Agent.granted_departments)
    )
    if only_active:
        stmt = stmt.where(Agent.is_active.is_(True))
    return list((await session.execute(stmt.order_by(Agent.display_name, Agent.email))).scalars())


async def create_agent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    display_name: str | None,
    role: str,
    password_hash: str | None,
    department_id: uuid.UUID | None = None,
) -> Agent:
    agent = Agent(
        tenant_id=tenant_id,
        email=email.strip().lower(),
        display_name=display_name,
        role=role,
        password_hash=password_hash,
        department_id=department_id,
    )
    session.add(agent)
    await session.flush()
    return agent


#: Cuenta de arranque documentada en el README. Hardcodeada a propósito: no
#: depende de que alguien haya ejecutado ``create_agent.py`` antes, ni de una
#: variable de entorno que pueda faltar en un despliegue nuevo.
DEFAULT_ADMIN_EMAIL = "admin@local"
DEFAULT_ADMIN_PASSWORD = "Admin1234"


async def ensure_default_admin(session: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """Garantiza que el inquilino tenga al menos un administrador activo.

    Se ejecuta en cada arranque. Si ya existe un administrador activo —el de
    por defecto u otro creado a mano— no toca nada: borrar ``admin@local`` una
    vez dado de alta un reemplazo es seguro y definitivo. Solo reaparece si el
    inquilino se queda sin ningún administrador, como red de seguridad.
    """
    has_active_admin = (
        await session.execute(
            select(Agent.id)
            .where(Agent.tenant_id == tenant_id, Agent.role == ROLE_ADMIN, Agent.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_active_admin is not None:
        return

    default_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
    existing = await find_agent_by_email(session, tenant_id=tenant_id, email=DEFAULT_ADMIN_EMAIL)
    if existing is not None:
        existing.role = ROLE_ADMIN
        existing.is_active = True
        existing.password_hash = default_hash
        return

    await create_agent(
        session,
        tenant_id=tenant_id,
        email=DEFAULT_ADMIN_EMAIL,
        display_name="Administrador",
        role=ROLE_ADMIN,
        password_hash=default_hash,
    )


async def touch_agent_presence(
    session: AsyncSession, agent_id: uuid.UUID, presence: str
) -> None:
    await session.execute(
        update(Agent)
        .where(Agent.id == agent_id)
        .values(presence=presence, last_seen_at=utcnow())
    )


async def open_agent_session(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    token_hash: str,
    ttl: timedelta,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> AgentSession:
    record = AgentSession(
        token_hash=token_hash,
        agent_id=agent_id,
        expires_at=utcnow() + ttl,
        client_ip=client_ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    session.add(record)
    await session.flush()
    return record


async def resolve_agent_session(session: AsyncSession, token_hash: str) -> Agent | None:
    """Traduce el token en agente activo, descartando sesiones caducadas."""
    record = await session.get(AgentSession, token_hash)
    if record is None:
        return None
    if as_utc(record.expires_at) <= utcnow():
        await session.delete(record)
        return None

    agent = await session.get(
        Agent, record.agent_id, options=[selectinload(Agent.granted_departments)]
    )
    if agent is None or not agent.is_active:
        await session.delete(record)
        return None

    record.last_used_at = utcnow()
    return agent


async def close_agent_session(session: AsyncSession, token_hash: str) -> None:
    record = await session.get(AgentSession, token_hash)
    if record is not None:
        await session.delete(record)


async def close_agent_sessions(session: AsyncSession, agent_id: uuid.UUID) -> None:
    """Revoca todas las sesiones de un agente, por ejemplo al desactivarlo."""
    await session.execute(delete(AgentSession).where(AgentSession.agent_id == agent_id))


async def count_active_admins(session: AsyncSession, *, tenant_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(Agent)
        .where(
            Agent.tenant_id == tenant_id,
            Agent.role == ROLE_ADMIN,
            Agent.is_active.is_(True),
        )
    )
    return (await session.execute(stmt)).scalar_one()


async def purge_expired_sessions(session: AsyncSession) -> int:
    result = await session.execute(
        delete(AgentSession).where(AgentSession.expires_at <= utcnow())
    )
    return result.rowcount or 0


# --------------------------------------------------------------------------- #
# Departamentos
# --------------------------------------------------------------------------- #
async def create_department(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str
) -> Department:
    department = Department(tenant_id=tenant_id, name=name.strip())
    session.add(department)
    await session.flush()
    return department


async def get_department(session: AsyncSession, department_id: uuid.UUID) -> Department | None:
    return await session.get(Department, department_id)


async def find_department_by_name(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str
) -> Department | None:
    return (
        await session.execute(
            select(Department).where(
                Department.tenant_id == tenant_id,
                func.lower(Department.name) == name.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def list_departments(
    session: AsyncSession, *, tenant_id: uuid.UUID, only_active: bool = True
) -> list[Department]:
    stmt = select(Department).where(Department.tenant_id == tenant_id)
    if only_active:
        stmt = stmt.where(Department.is_active.is_(True))
    return list((await session.execute(stmt.order_by(Department.name))).scalars())


async def set_agent_departments(
    session: AsyncSession, *, agent: Agent, department_ids: list[uuid.UUID]
) -> None:
    """Reemplaza por completo los departamentos adicionales de un agente.

    El principal (``Agent.department_id``) no se toca aquí: se actualiza aparte,
    como cualquier otro campo del agente.
    """
    await session.execute(
        delete(AgentDepartment).where(AgentDepartment.agent_id == agent.id)
    )
    for department_id in set(department_ids):
        session.add(AgentDepartment(agent_id=agent.id, department_id=department_id))
    await session.flush()
    await session.refresh(agent, attribute_names=["granted_departments"])


# --------------------------------------------------------------------------- #
# Módulo Hotel: habitaciones y reservas
# --------------------------------------------------------------------------- #
def hotel_module_enabled(department: Department) -> bool:
    """Si el departamento tiene activo el módulo de reservas de hotel."""
    return bool((department.enabled_modules or {}).get("hotel_booking", {}).get("enabled"))


async def set_hotel_module_enabled(
    session: AsyncSession, *, department: Department, enabled: bool
) -> Department:
    modules = dict(department.enabled_modules or {})
    modules["hotel_booking"] = {**modules.get("hotel_booking", {}), "enabled": enabled}
    department.enabled_modules = modules
    await session.flush()
    return department


# -- Tipos de habitación ------------------------------------------------------
async def create_hotel_room_type(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    name: str,
    description: str | None,
    capacity: int,
) -> HotelRoomType:
    room_type = HotelRoomType(
        tenant_id=tenant_id,
        department_id=department_id,
        name=name.strip(),
        description=description,
        capacity=capacity,
    )
    session.add(room_type)
    await session.flush()
    return room_type


async def get_hotel_room_type(
    session: AsyncSession, room_type_id: uuid.UUID
) -> HotelRoomType | None:
    return await session.get(HotelRoomType, room_type_id)


async def find_hotel_room_type_by_name(
    session: AsyncSession, *, department_id: uuid.UUID, name: str
) -> HotelRoomType | None:
    return (
        await session.execute(
            select(HotelRoomType).where(
                HotelRoomType.department_id == department_id,
                func.lower(HotelRoomType.name) == name.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def list_hotel_room_types(
    session: AsyncSession, *, department_id: uuid.UUID, only_active: bool = False
) -> list[HotelRoomType]:
    stmt = select(HotelRoomType).where(HotelRoomType.department_id == department_id)
    if only_active:
        stmt = stmt.where(HotelRoomType.is_active.is_(True))
    return list((await session.execute(stmt.order_by(HotelRoomType.name))).scalars())


# -- Habitaciones ---------------------------------------------------------------
async def create_hotel_room(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    room_type_id: uuid.UUID,
    code: str,
    notes: str | None = None,
) -> HotelRoom:
    room = HotelRoom(
        tenant_id=tenant_id,
        department_id=department_id,
        room_type_id=room_type_id,
        code=code.strip(),
        notes=notes,
    )
    session.add(room)
    await session.flush()
    return room


async def get_hotel_room(session: AsyncSession, room_id: uuid.UUID) -> HotelRoom | None:
    return (
        await session.execute(
            select(HotelRoom)
            .where(HotelRoom.id == room_id)
            .options(selectinload(HotelRoom.room_type))
        )
    ).scalar_one_or_none()


async def find_hotel_room_by_code(
    session: AsyncSession, *, department_id: uuid.UUID, code: str
) -> HotelRoom | None:
    return (
        await session.execute(
            select(HotelRoom).where(
                HotelRoom.department_id == department_id,
                func.lower(HotelRoom.code) == code.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def list_hotel_rooms(
    session: AsyncSession, *, department_id: uuid.UUID, room_type_id: uuid.UUID | None = None
) -> list[HotelRoom]:
    stmt = (
        select(HotelRoom)
        .where(HotelRoom.department_id == department_id)
        .options(selectinload(HotelRoom.room_type))
    )
    if room_type_id is not None:
        stmt = stmt.where(HotelRoom.room_type_id == room_type_id)
    return list((await session.execute(stmt.order_by(HotelRoom.code))).scalars())


# -- Tarifas ----------------------------------------------------------------------
async def create_hotel_rate_plan(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    room_type_id: uuid.UUID,
    name: str,
    starts_on: date | None,
    ends_on: date | None,
    nightly_price_cents: int,
    currency: str,
) -> HotelRatePlan:
    rate_plan = HotelRatePlan(
        tenant_id=tenant_id,
        department_id=department_id,
        room_type_id=room_type_id,
        name=name.strip(),
        starts_on=starts_on,
        ends_on=ends_on,
        nightly_price_cents=nightly_price_cents,
        currency=currency.upper(),
    )
    session.add(rate_plan)
    await session.flush()
    return rate_plan


async def get_hotel_rate_plan(
    session: AsyncSession, rate_plan_id: uuid.UUID
) -> HotelRatePlan | None:
    return await session.get(HotelRatePlan, rate_plan_id)


async def list_hotel_rate_plans(
    session: AsyncSession, *, department_id: uuid.UUID, room_type_id: uuid.UUID | None = None
) -> list[HotelRatePlan]:
    stmt = select(HotelRatePlan).where(HotelRatePlan.department_id == department_id)
    if room_type_id is not None:
        stmt = stmt.where(HotelRatePlan.room_type_id == room_type_id)
    return list((await session.execute(stmt.order_by(HotelRatePlan.name))).scalars())


async def delete_hotel_rate_plan(session: AsyncSession, rate_plan: HotelRatePlan) -> None:
    await session.delete(rate_plan)
    await session.flush()


async def update_hotel_rate_plan(
    session: AsyncSession,
    *,
    rate_plan: HotelRatePlan,
    name: str | None = None,
    starts_on: date | None = None,
    ends_on: date | None = None,
    nightly_price_cents: int | None = None,
    currency: str | None = None,
) -> HotelRatePlan:
    """Cambia solo los campos recibidos; el resto queda igual."""
    if name is not None:
        rate_plan.name = name.strip()
    if starts_on is not None:
        rate_plan.starts_on = starts_on
    if ends_on is not None:
        rate_plan.ends_on = ends_on
    if nightly_price_cents is not None:
        rate_plan.nightly_price_cents = nightly_price_cents
    if currency is not None:
        rate_plan.currency = currency.upper()
    await session.flush()
    return rate_plan


async def rate_plan_for_stay(
    session: AsyncSession, *, room_type_id: uuid.UUID, check_in: date
) -> HotelRatePlan | None:
    """La tarifa vigente el día de entrada: la de temporada si aplica, si no
    la que no tiene fechas fijadas (la tarifa por omisión de la categoría).

    Si hay más de una candidata para el mismo día —dos tarifas de temporada
    solapadas, o dos «por omisión» cargadas por error— se elige la creada más
    recientemente: un empate no puede quedar librado al orden que devuelva la
    base de datos, porque entonces el precio aplicado cambiaría de una
    llamada a otra sin que nada en los datos lo explique.
    """
    stmt = (
        select(HotelRatePlan)
        .where(
            HotelRatePlan.room_type_id == room_type_id,
            or_(HotelRatePlan.starts_on.is_(None), HotelRatePlan.starts_on <= check_in),
            or_(HotelRatePlan.ends_on.is_(None), HotelRatePlan.ends_on > check_in),
        )
        # Una tarifa de temporada (con fechas) manda sobre la de omisión.
        .order_by(
            HotelRatePlan.starts_on.is_(None),
            HotelRatePlan.starts_on.desc(),
            HotelRatePlan.created_at.desc(),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


# -- Reservas -----------------------------------------------------------------
def _overlapping_reservations_clause(check_in: date, check_out: date) -> list[Any]:
    """Condiciones comunes de solapamiento: reserva activa con fechas cruzadas.

    Las canceladas o "no show" no cuentan como ocupación. Compartida entre
    ``hotel_room_has_overlap`` y ``list_available_hotel_rooms`` para que ambas
    definan «solapado» exactamente igual — si una cambiara sin la otra, la
    disponibilidad mostrada dejaría de coincidir con lo que de verdad se puede
    reservar.
    """
    return [
        HotelReservation.status.notin_(("cancelled", "no_show")),
        HotelReservation.check_in < check_out,
        HotelReservation.check_out > check_in,
    ]


async def hotel_room_has_overlap(
    session: AsyncSession,
    *,
    room_id: uuid.UUID,
    check_in: date,
    check_out: date,
    exclude_reservation_id: uuid.UUID | None = None,
) -> bool:
    """Si ya hay una reserva activa que se solapa con ese rango en esa habitación.

    Es la comprobación que da un error legible en el caso común. En PostgreSQL,
    la restricción de exclusión de la migración es la que de verdad impide la
    carrera entre dos peticiones simultáneas — ver
    ``db/migrations/0013_hotel_module.sql``; esta consulta no la sustituye.

    ``exclude_reservation_id`` es para editar una reserva ya existente: no
    tendría que chocar consigo misma al revalidar sus propias fechas.
    """
    stmt = select(HotelReservation.id).where(
        HotelReservation.room_id == room_id,
        *_overlapping_reservations_clause(check_in, check_out),
    )
    if exclude_reservation_id is not None:
        stmt = stmt.where(HotelReservation.id != exclude_reservation_id)
    return (await session.execute(stmt.limit(1))).first() is not None


async def list_available_hotel_rooms(
    session: AsyncSession,
    *,
    department_id: uuid.UUID,
    check_in: date,
    check_out: date,
    room_type_id: uuid.UUID | None = None,
    exclude_reservation_id: uuid.UUID | None = None,
) -> list[HotelRoom]:
    """Habitaciones sin ninguna reserva activa que se solape con el rango pedido.

    ``exclude_reservation_id`` es para editar una reserva ya existente: sin
    él, la habitación que esa misma reserva ya ocupa no aparecería libre ni
    para sus propias fechas, porque chocaría contra sí misma.
    """
    overlapping = select(HotelReservation.room_id).where(
        HotelReservation.department_id == department_id,
        *_overlapping_reservations_clause(check_in, check_out),
    )
    if exclude_reservation_id is not None:
        overlapping = overlapping.where(HotelReservation.id != exclude_reservation_id)
    stmt = (
        select(HotelRoom)
        .where(
            HotelRoom.department_id == department_id,
            HotelRoom.status == "available",
            HotelRoom.id.notin_(overlapping),
        )
        .options(selectinload(HotelRoom.room_type))
    )
    if room_type_id is not None:
        stmt = stmt.where(HotelRoom.room_type_id == room_type_id)
    return list((await session.execute(stmt.order_by(HotelRoom.code))).scalars())


async def create_hotel_reservation(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    department_id: uuid.UUID,
    room_id: uuid.UUID,
    guest_name: str,
    guest_phone: str | None,
    guest_email: str | None,
    check_in: date,
    check_out: date,
    guests: int,
    contact_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    created_by_agent_id: uuid.UUID | None = None,
    nightly_price_cents: int | None = None,
    currency: str = "USD",
    notes: str | None = None,
    status: str = "confirmed",
) -> HotelReservation:
    reservation = HotelReservation(
        tenant_id=tenant_id,
        department_id=department_id,
        room_id=room_id,
        guest_name=guest_name.strip(),
        guest_phone=guest_phone,
        guest_email=guest_email,
        check_in=check_in,
        check_out=check_out,
        guests=guests,
        contact_id=contact_id,
        conversation_id=conversation_id,
        created_by_agent_id=created_by_agent_id,
        nightly_price_cents=nightly_price_cents,
        currency=currency.upper(),
        notes=notes,
        status=status,
    )
    session.add(reservation)
    await session.flush()
    return reservation


async def update_hotel_reservation(
    session: AsyncSession,
    *,
    reservation: HotelReservation,
    room_id: uuid.UUID,
    check_in: date,
    check_out: date,
    guest_name: str | None = None,
    guest_phone: str | None = None,
    guest_email: str | None = None,
    contact_id: uuid.UUID | None = None,
    guests: int | None = None,
    nightly_price_cents: int | None = None,
    currency: str | None = None,
    notes: str | None = None,
) -> HotelReservation:
    """Corrige los datos de una reserva ya cargada. ``room_id``/fechas siempre
    se fijan —quien llama ya resolvió a qué valor quedan, sean nuevos o los
    que ya tenía—; el resto solo cambia si se recibió un valor.
    """
    reservation.room_id = room_id
    reservation.check_in = check_in
    reservation.check_out = check_out
    if guest_name is not None:
        reservation.guest_name = guest_name.strip()
    if guest_phone is not None:
        reservation.guest_phone = guest_phone
    if guest_email is not None:
        reservation.guest_email = guest_email
    if contact_id is not None:
        reservation.contact_id = contact_id
    if guests is not None:
        reservation.guests = guests
    if nightly_price_cents is not None:
        reservation.nightly_price_cents = nightly_price_cents
    if currency is not None:
        reservation.currency = currency.upper()
    if notes is not None:
        reservation.notes = notes
    await session.flush()
    return reservation


async def get_hotel_reservation(
    session: AsyncSession, reservation_id: uuid.UUID
) -> HotelReservation | None:
    return (
        await session.execute(
            select(HotelReservation)
            .where(HotelReservation.id == reservation_id)
            .options(selectinload(HotelReservation.room).selectinload(HotelRoom.room_type))
        )
    ).scalar_one_or_none()


async def list_hotel_reservations(
    session: AsyncSession,
    *,
    department_id: uuid.UUID,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[HotelReservation]:
    stmt = (
        select(HotelReservation)
        .where(HotelReservation.department_id == department_id)
        .options(selectinload(HotelReservation.room).selectinload(HotelRoom.room_type))
    )
    if status is not None:
        stmt = stmt.where(HotelReservation.status == status)
    # Cualquier reserva cuyo rango toque la ventana pedida, no solo la que
    # empieza dentro de ella.
    if from_date is not None:
        stmt = stmt.where(HotelReservation.check_out > from_date)
    if to_date is not None:
        stmt = stmt.where(HotelReservation.check_in < to_date)
    return list((await session.execute(stmt.order_by(HotelReservation.check_in))).scalars())


async def set_hotel_reservation_status(
    session: AsyncSession, *, reservation: HotelReservation, status: str
) -> HotelReservation:
    reservation.status = status
    await session.flush()
    return reservation


#: Cuántos días adelante cubre el resumen de ingresos del reporte.
HOTEL_REPORT_HORIZON_DAYS = 30


async def hotel_department_report(
    session: AsyncSession, *, department_id: uuid.UUID, today: date
) -> dict[str, Any]:
    """Resumen operativo del departamento: llegadas y salidas de hoy, cuántas
    habitaciones están ocupadas ahora mismo, reservas pendientes de confirmar
    e ingresos de los próximos ``HOTEL_REPORT_HORIZON_DAYS`` días.

    Se calcula en Python sobre las filas ya traídas, y no con ``SUM``/``COUNT``
    en SQL: son pocas reservas por departamento, y así el prorrateo de
    ingresos —una reserva que empezó antes de la ventana pero sigue dentro—
    queda legible en vez de repartido entre varias subconsultas.
    """
    total_rooms = await session.scalar(
        select(func.count())
        .select_from(HotelRoom)
        .where(HotelRoom.department_id == department_id)
    )
    pending_count = await session.scalar(
        select(func.count())
        .select_from(HotelReservation)
        .where(
            HotelReservation.department_id == department_id,
            HotelReservation.status == "pending",
        )
    )

    horizon = today + timedelta(days=HOTEL_REPORT_HORIZON_DAYS)
    stmt = select(HotelReservation).where(
        HotelReservation.department_id == department_id,
        HotelReservation.status.notin_(("cancelled", "no_show")),
        # >= y no > : una salida justo hoy debe seguir contando para
        # ``departures_today``, aunque ya no sume a ``occupied_rooms`` ni a
        # los ingresos (eso lo filtran sus propios cálculos, más abajo).
        HotelReservation.check_out >= today,
        HotelReservation.check_in < horizon,
    )
    reservations = list((await session.execute(stmt)).scalars())

    arrivals_today = sum(1 for row in reservations if row.check_in == today)
    departures_today = sum(1 for row in reservations if row.check_out == today)
    occupied_rooms = len(
        {row.room_id for row in reservations if row.check_in <= today < row.check_out}
    )

    revenue: dict[str, int] = {}
    for row in reservations:
        if row.nightly_price_cents is None:
            continue
        nights = (min(row.check_out, horizon) - max(row.check_in, today)).days
        if nights <= 0:
            continue
        revenue[row.currency] = revenue.get(row.currency, 0) + row.nightly_price_cents * nights

    return {
        "reference_date": today,
        "arrivals_today": arrivals_today,
        "departures_today": departures_today,
        "occupied_rooms": occupied_rooms,
        "total_rooms": total_rooms or 0,
        "pending_count": pending_count or 0,
        "revenue_by_currency": [
            {"currency": currency, "total_cents": total_cents}
            for currency, total_cents in sorted(revenue.items())
        ],
    }


# --------------------------------------------------------------------------- #
# Respuestas guardadas
# --------------------------------------------------------------------------- #
async def create_canned_response(
    session: AsyncSession, *, tenant_id: uuid.UUID, shortcode: str, title: str, body: str
) -> CannedResponse:
    canned = CannedResponse(
        tenant_id=tenant_id, shortcode=shortcode.strip(), title=title.strip(), body=body
    )
    session.add(canned)
    await session.flush()
    return canned


async def get_canned_response(
    session: AsyncSession, canned_response_id: uuid.UUID
) -> CannedResponse | None:
    return await session.get(CannedResponse, canned_response_id)


async def find_canned_response_by_shortcode(
    session: AsyncSession, *, tenant_id: uuid.UUID, shortcode: str
) -> CannedResponse | None:
    return (
        await session.execute(
            select(CannedResponse).where(
                CannedResponse.tenant_id == tenant_id,
                func.lower(CannedResponse.shortcode) == shortcode.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def list_canned_responses(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[CannedResponse]:
    stmt = select(CannedResponse).where(CannedResponse.tenant_id == tenant_id)
    return list((await session.execute(stmt.order_by(CannedResponse.shortcode))).scalars())


async def delete_canned_response(session: AsyncSession, canned_response: CannedResponse) -> None:
    await session.delete(canned_response)
    await session.flush()


# --------------------------------------------------------------------------- #
# Etiquetas
# --------------------------------------------------------------------------- #
async def create_label(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str, color: str
) -> Label:
    label = Label(tenant_id=tenant_id, name=name.strip(), color=color.strip())
    session.add(label)
    await session.flush()
    return label


async def get_label(session: AsyncSession, label_id: uuid.UUID) -> Label | None:
    return await session.get(Label, label_id)


async def find_label_by_name(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str
) -> Label | None:
    return (
        await session.execute(
            select(Label).where(
                Label.tenant_id == tenant_id,
                func.lower(Label.name) == name.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def list_labels(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Label]:
    stmt = select(Label).where(Label.tenant_id == tenant_id)
    return list((await session.execute(stmt.order_by(Label.name))).scalars())


async def delete_label(session: AsyncSession, label: Label) -> None:
    await session.delete(label)
    await session.flush()


async def set_conversation_labels(
    session: AsyncSession, *, conversation: Conversation, label_ids: list[uuid.UUID]
) -> None:
    """Reemplaza por completo las etiquetas de una conversación."""
    await session.execute(
        delete(ConversationLabel).where(ConversationLabel.conversation_id == conversation.id)
    )
    for label_id in set(label_ids):
        session.add(ConversationLabel(conversation_id=conversation.id, label_id=label_id))
    await session.flush()
    await session.refresh(conversation, attribute_names=["labels"])


# --------------------------------------------------------------------------- #
# Objetivo de primera respuesta
# --------------------------------------------------------------------------- #
async def set_first_response_deadline(
    session: AsyncSession, *, conversation: Conversation, policy: ServicePolicy
) -> datetime | None:
    """Fija el vencimiento de la primera respuesta, si corresponde uno.

    No se toca una conversación ya respondida ni una que ya tiene vencimiento:
    el objetivo se cuenta desde que el cliente quedó esperando, y una segunda
    consulta suya no le da al equipo un plazo nuevo.
    """
    if conversation.first_response_at is not None or conversation.first_response_due_at:
        return conversation.first_response_due_at
    target = policy.first_response_target_minutes
    if not target:
        return None

    conversation.first_response_due_at = add_business_minutes(
        policy.business_hours, policy.timezone, utcnow(), target
    )
    return conversation.first_response_due_at


async def mark_first_human_response(
    session: AsyncSession, *, conversation: Conversation
) -> None:
    """Anota que una persona contestó. La respuesta del asistente no llega aquí."""
    if conversation.first_response_at is None:
        conversation.first_response_at = utcnow()


async def breach_overdue_first_responses(session: AsyncSession, limit: int = 200) -> int:
    """Marca como incumplidas las que vencieron sin respuesta humana."""
    now = utcnow()
    stmt = (
        select(Conversation)
        .where(
            Conversation.first_response_due_at.is_not(None),
            Conversation.first_response_due_at <= now,
            Conversation.first_response_at.is_(None),
            Conversation.sla_breached_at.is_(None),
            Conversation.status == "open",
        )
        .limit(limit)
    )
    overdue = list((await session.execute(stmt)).scalars())
    for conversation in overdue:
        conversation.sla_breached_at = now
    if overdue:
        await session.flush()
    return len(overdue)


# --------------------------------------------------------------------------- #
# Macros
# --------------------------------------------------------------------------- #
async def create_macro(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str, steps: list[Any]
) -> Macro:
    macro = Macro(tenant_id=tenant_id, name=name.strip(), steps=steps)
    session.add(macro)
    await session.flush()
    return macro


async def get_macro(session: AsyncSession, macro_id: uuid.UUID) -> Macro | None:
    return await session.get(Macro, macro_id)


async def find_macro_by_name(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str
) -> Macro | None:
    return (
        await session.execute(
            select(Macro).where(
                Macro.tenant_id == tenant_id,
                func.lower(Macro.name) == name.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def list_macros(session: AsyncSession, *, tenant_id: uuid.UUID) -> list[Macro]:
    stmt = select(Macro).where(Macro.tenant_id == tenant_id).order_by(Macro.name)
    return list((await session.execute(stmt)).scalars())


async def delete_macro(session: AsyncSession, macro: Macro) -> None:
    await session.delete(macro)
    await session.flush()


# --------------------------------------------------------------------------- #
# Vistas guardadas
# --------------------------------------------------------------------------- #
async def create_saved_view(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    owner_agent_id: uuid.UUID | None,
    name: str,
    filters: dict[str, Any],
) -> SavedView:
    view = SavedView(
        tenant_id=tenant_id,
        owner_agent_id=owner_agent_id,
        name=name.strip(),
        filters=filters,
    )
    session.add(view)
    await session.flush()
    return view


async def get_saved_view(session: AsyncSession, view_id: uuid.UUID) -> SavedView | None:
    return await session.get(SavedView, view_id)


async def find_saved_view_by_name(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    owner_agent_id: uuid.UUID | None,
    name: str,
) -> SavedView | None:
    """Busca dentro del mismo alcance: lo personal no choca con lo del equipo."""
    owner_match = (
        SavedView.owner_agent_id.is_(None)
        if owner_agent_id is None
        else SavedView.owner_agent_id == owner_agent_id
    )
    return (
        await session.execute(
            select(SavedView).where(
                SavedView.tenant_id == tenant_id,
                owner_match,
                func.lower(SavedView.name) == name.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def list_saved_views(
    session: AsyncSession, *, tenant_id: uuid.UUID, agent_id: uuid.UUID | None
) -> list[SavedView]:
    """Las del equipo más las propias; nunca las personales de un compañero."""
    visible = SavedView.owner_agent_id.is_(None)
    if agent_id is not None:
        visible = or_(visible, SavedView.owner_agent_id == agent_id)
    stmt = (
        select(SavedView)
        .where(SavedView.tenant_id == tenant_id, visible)
        .order_by(SavedView.owner_agent_id.is_(None).desc(), SavedView.name)
    )
    return list((await session.execute(stmt)).scalars())


async def delete_saved_view(session: AsyncSession, view: SavedView) -> None:
    await session.delete(view)
    await session.flush()


# --------------------------------------------------------------------------- #
# Derivaciones
# --------------------------------------------------------------------------- #
async def record_assignment(
    session: AsyncSession,
    *,
    conversation: Conversation,
    action: str,
    to_agent_id: uuid.UUID | None,
    by_agent_id: uuid.UUID | None,
    note: str | None = None,
) -> Assignment:
    """Aplica el cambio de responsable y lo deja anotado.

    La conversación no se duplica ni se archiva: cambia el campo ``assignee_id``
    de la misma fila, con lo que el historial de mensajes permanece intacto y
    accesible para quien la reciba.
    """
    entry = Assignment(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        action=action,
        from_agent_id=conversation.assignee_id,
        to_agent_id=to_agent_id,
        by_agent_id=by_agent_id,
        note=note,
    )
    session.add(entry)
    conversation.assignee_id = to_agent_id
    # Con responsable humano se silencia la respuesta automática; al devolver la
    # conversación a la cola, el asistente vuelve a atenderla.
    conversation.control = "human" if to_agent_id is not None else "bot"
    await session.flush()
    return entry


async def transfer_to_department(
    session: AsyncSession,
    *,
    conversation: Conversation,
    department_id: uuid.UUID,
    by_agent_id: uuid.UUID | None,
    note: str | None = None,
) -> Assignment:
    """Deriva la conversación a la cola de un departamento, sin responsable.

    A diferencia de ``record_assignment``, no hay un agente destinatario: la
    conversación queda en la cola de ese departamento y el asistente vuelve a
    atenderla, igual que al liberarla a la cola común.
    """
    entry = Assignment(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        action="transfer_department",
        from_agent_id=conversation.assignee_id,
        to_department_id=department_id,
        by_agent_id=by_agent_id,
        note=note,
    )
    session.add(entry)
    conversation.assignee_id = None
    conversation.department_id = department_id
    conversation.control = "bot"
    await session.flush()
    return entry


async def assignment_history(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[Assignment]:
    stmt = (
        select(Assignment)
        .where(Assignment.conversation_id == conversation_id)
        .order_by(Assignment.created_at)
    )
    return list((await session.execute(stmt)).scalars())


# --------------------------------------------------------------------------- #
# Notas internas
# --------------------------------------------------------------------------- #
async def add_internal_note(
    session: AsyncSession,
    *,
    conversation: Conversation,
    agent_id: uuid.UUID | None,
    body: str,
    mentions: list[str] | None = None,
) -> InternalNote:
    note = InternalNote(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        agent_id=agent_id,
        body=body,
        mentions=mentions or [],
    )
    session.add(note)
    await session.flush()
    return note


async def list_internal_notes(
    session: AsyncSession, conversation_id: uuid.UUID, limit: int = 100
) -> list[InternalNote]:
    stmt = (
        select(InternalNote)
        .where(InternalNote.conversation_id == conversation_id)
        .order_by(InternalNote.created_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


# --------------------------------------------------------------------------- #
# Supervisión
# --------------------------------------------------------------------------- #
async def workload_by_agent(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    department_ids: set[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    """Carga abierta por agente, con la cola común como fila sin responsable.

    ``department_ids`` acota a una supervisión restringida por departamento
    (``None`` = sin restricción, el caso de administración).
    """
    stmt = (
        select(
            Conversation.assignee_id,
            func.count().label("open_conversations"),
            func.sum(Conversation.unread_count).label("unread"),
        )
        .where(Conversation.tenant_id == tenant_id, Conversation.status == "open")
        .group_by(Conversation.assignee_id)
    )
    stmt = _restrict_to_departments(stmt, department_ids)
    rows = (await session.execute(stmt)).all()
    agents = {agent.id: agent for agent in await list_agents(session, tenant_id=tenant_id)}
    workload: list[dict[str, Any]] = []
    for assignee_id, open_conversations, unread in rows:
        agent = agents.get(assignee_id) if assignee_id else None
        workload.append(
            {
                "agent_id": str(assignee_id) if assignee_id else None,
                "agent": agent.label if agent else "Cola común",
                "role": agent.role if agent else None,
                "presence": agent.presence if agent else None,
                "open_conversations": open_conversations,
                "unread": int(unread or 0),
            }
        )
    workload.sort(key=lambda row: (row["agent_id"] is not None, -row["open_conversations"]))
    return workload


async def transfer_activity(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    limit: int = 50,
    department_ids: set[uuid.UUID] | None = None,
) -> list[Assignment]:
    """Últimas derivaciones del inquilino, para el panel de supervisión.

    ``department_ids`` acota a una supervisión restringida por departamento
    (``None`` = sin restricción, el caso de administración).
    """
    stmt = select(Assignment).where(Assignment.tenant_id == tenant_id)
    if department_ids is not None:
        stmt = stmt.join(Conversation, Conversation.id == Assignment.conversation_id)
        stmt = _restrict_to_departments(stmt, department_ids)
    stmt = stmt.order_by(Assignment.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars())
