"""Acceso a datos. Concentra aquí toda sentencia SQL de la capa de dominio.

Cada función recibe una ``AsyncSession`` ya abierta: la transacción la controla
quien orquesta, no el repositorio.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Text, cast, delete, func, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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
from app.db.models import (
    ROLE_ADMIN,
    SUPERVISOR_ROLES,
    Agent,
    AgentDepartment,
    AgentSession,
    AIRun,
    Assignment,
    AuditLog,
    ChannelAccount,
    Contact,
    ContactComment,
    ContactIdentity,
    ContactSession,
    Conversation,
    Department,
    InboundDedupe,
    InternalNote,
    Message,
    MessageEvent,
    OutboxItem,
    Tenant,
)

#: Retroceso exponencial acotado, en segundos, por número de intento.
RETRY_BACKOFF_S = (5, 30, 120, 600, 1_800, 3_600)


def _as_utc(value: datetime) -> datetime:
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
    if _as_utc(record.expires_at) <= utcnow():
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


def _restrict_to_accessible_departments(stmt: Any, department_ids: set[uuid.UUID] | None) -> Any:
    """Acota la cola —solo ``assignee_id`` nulo— a los departamentos accesibles.

    ``None`` significa sin restricción (supervisión y administración). Una
    conversación sin departamento sigue visible para cualquiera: acotar por
    departamento es una restricción adicional sobre la cola, nunca sobre lo
    que ya tiene dueño.
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
    limit: int = 50,
    offset: int = 0,
) -> list[Conversation]:
    """Devuelve la bandeja según el alcance solicitado.

    ``scope`` acepta:

    * ``unassigned`` — la cola común: el punto único por donde entra todo.
    * ``mine`` — solo lo asignado al agente indicado.
    * ``mine_or_unassigned`` — su carga de trabajo más lo que puede tomar.
    * ``all`` — la totalidad del inquilino; reservado a supervisión.

    ``department_ids`` acota la cola a los departamentos que puede atender
    quien pregunta (``None`` = sin restricción); ``department`` es un filtro
    de interfaz para ver solo un departamento concreto de entre los propios,
    nunca una forma de ver más de lo que ``department_ids`` ya permite.

    El filtro se aplica en SQL y no en memoria: un agente jamás recibe filas
    ajenas que después haya que descartar.
    """
    stmt = select(Conversation).where(Conversation.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Conversation.status == status)
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if department:
        stmt = stmt.where(Conversation.department_id == department)

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

    stmt = (
        stmt.options(
            selectinload(Conversation.contact),
            selectinload(Conversation.assignee),
            selectinload(Conversation.department),
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
    if status:
        stmt = stmt.where(Conversation.status == status)
    if scope == "unassigned":
        stmt = stmt.where(Conversation.assignee_id.is_(None))
        stmt = _restrict_to_accessible_departments(stmt, department_ids)
    elif scope == "mine":
        stmt = stmt.where(Conversation.assignee_id == agent_id)
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

    Supervisión ve todo. Un agente ve lo propio y lo que aún no tiene dueño,
    porque la cola común es precisamente el punto del que ha de poder tomar
    trabajo — salvo que esa conversación sin dueño ya quedó derivada a un
    departamento al que no tiene acceso.
    """
    if agent.role in SUPERVISOR_ROLES:
        return True
    if conversation.assignee_id not in (None, agent.id):
        return False
    if conversation.assignee_id == agent.id:
        return True
    if conversation.department_id is None:
        return True
    accessible = department_ids if department_ids is not None else agent_department_ids(agent)
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


async def channel_stats(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    """Recuento de mensajes por canal, para el panel de la consola."""
    stmt = (
        select(Message.channel, func.count())
        .where(Message.tenant_id == tenant_id)
        .group_by(Message.channel)
    )
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
    if _as_utc(record.expires_at) <= utcnow():
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
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Carga abierta por agente, con la cola común como fila sin responsable."""
    stmt = (
        select(
            Conversation.assignee_id,
            func.count().label("open_conversations"),
            func.sum(Conversation.unread_count).label("unread"),
        )
        .where(Conversation.tenant_id == tenant_id, Conversation.status == "open")
        .group_by(Conversation.assignee_id)
    )
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
    session: AsyncSession, tenant_id: uuid.UUID, limit: int = 50
) -> list[Assignment]:
    """Últimas derivaciones del inquilino, para el panel de supervisión."""
    stmt = (
        select(Assignment)
        .where(Assignment.tenant_id == tenant_id)
        .order_by(Assignment.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())
