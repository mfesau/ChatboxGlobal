"""Modelo de datos relacional (PostgreSQL).

Convenciones aplicadas:

* Toda clave primaria es ``uuid`` generada en la aplicación, lo que permite
  crear grafos de objetos antes del ``flush``.
* Las columnas semiestructuradas usan ``JSONB``, con variante ``JSON`` para
  SQLite, de modo que la batería de pruebas corra sin un servidor Postgres.
* Los enumerados se materializan como ``VARCHAR`` y no como tipo ``ENUM``
  nativo (``native_enum=False``): incorporar un canal o estado nuevo no exige
  ``ALTER TYPE`` ni migración de datos.
* Multiempresa desde el primer día mediante ``tenant_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.envelope import (
    ChannelKind,
    ContentType,
    DeliveryStatus,
    Direction,
    new_id,
    utcnow,
)

#: ``JSONB`` en Postgres, ``JSON`` en SQLite (pruebas).
JSONBType = JSONB().with_variant(JSON(), "sqlite")


def _enum(python_enum: type, name: str) -> SAEnum:
    return SAEnum(
        python_enum,
        name=name,
        native_enum=False,
        values_callable=lambda e: [member.value for member in e],
        length=32,
    )


class Base(DeclarativeBase):
    """Base declarativa con nomenclatura estable de restricciones."""

    type_annotation_map = {dict[str, Any]: JSONBType}


class TimestampMixin:
    """Marcas de tiempo generadas en la aplicación.

    El valor lo produce Python y no ``now()`` del servidor: en PostgreSQL esa
    función devuelve el instante de inicio de la transacción, con lo que todas
    las filas escritas en un mismo turno compartirían marca y el orden del hilo
    quedaría indeterminado. El ``server_default`` se conserva como red de
    seguridad para inserciones hechas con SQL directo.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        onupdate=utcnow,
        nullable=False,
    )


# --------------------------------------------------------------------------- #
# Organización
# --------------------------------------------------------------------------- #
class Tenant(Base, TimestampMixin):
    """Unidad de aislamiento: empresa, marca o unidad de negocio."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)

    channel_accounts: Mapped[list[ChannelAccount]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class ChannelAccount(Base, TimestampMixin):
    """Identidad del bot en un canal concreto.

    Ejemplos de ``external_id``: ``phone_number_id`` de WhatsApp Cloud API,
    ``MicrosoftAppId`` del Bot Framework, o el identificador del widget web.
    """

    __tablename__ = "channel_accounts"
    __table_args__ = (
        UniqueConstraint("channel", "external_id", name="uq_channel_account_external"),
        Index("ix_channel_accounts_tenant", "tenant_id", "channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[ChannelKind] = mapped_column(_enum(ChannelKind, "channel_kind"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: A qué departamento cae, sin derivación manual, una conversación nueva
    #: de esta cuenta. Nulo = cola común, igual que hoy (compatibilidad).
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), index=True
    )
    #: Configuración no sensible.
    config: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)
    #: Credenciales propias de esta cuenta (p. ej. el token de acceso de un
    #: número de WhatsApp o de una página de Facebook), cifradas con
    #: ``app/core/secrets.py``. Nulo cuando la cuenta usa la credencial global
    #: de ``.env`` (WhatsApp) o no necesita ninguna (Teams).
    credentials_ciphertext: Mapped[str | None] = mapped_column(Text)

    tenant: Mapped[Tenant] = relationship(back_populates="channel_accounts")
    department: Mapped[Department | None] = relationship()


# --------------------------------------------------------------------------- #
# Personas
# --------------------------------------------------------------------------- #
class Contact(Base, TimestampMixin):
    """Persona externa, unificada entre canales."""

    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_tenant_phone", "tenant_id", "primary_phone"),
        UniqueConstraint("tenant_id", "primary_email", name="uq_contact_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(160))
    primary_phone: Mapped[str | None] = mapped_column(String(32))
    primary_email: Mapped[str | None] = mapped_column(String(254))
    locale: Mapped[str | None] = mapped_column(String(16))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)
    #: Derivación ``scrypt$n$r$p$salt$hash`` del chatbox público. Nulo para los
    #: contactos que solo existen por WhatsApp o Teams, sin cuenta propia.
    password_hash: Mapped[str | None] = mapped_column(String(255))

    identities: Mapped[list[ContactIdentity]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )


class ContactIdentity(Base, TimestampMixin):
    """Vínculo entre un contacto y su identificador en un canal."""

    __tablename__ = "contact_identities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "channel", "channel_user_id", name="uq_identity_channel_user"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[ChannelKind] = mapped_column(_enum(ChannelKind, "channel_kind"), nullable=False)
    channel_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)

    contact: Mapped[Contact] = relationship(back_populates="identities")


#: Roles reconocidos, de menor a mayor alcance.
ROLE_AGENT = "agent"
ROLE_SUPERVISOR = "supervisor"
ROLE_ADMIN = "admin"
#: Los roles que ven la totalidad de las conversaciones del inquilino.
SUPERVISOR_ROLES = frozenset({ROLE_SUPERVISOR, ROLE_ADMIN})


class Department(Base, TimestampMixin):
    """Rama del equipo que acota una parte de la cola común.

    Una conversación nace sin departamento, igual que hoy, y solo queda
    acotada a uno cuando alguien la deriva explícitamente (ver
    ``Assignment.to_department_id``), o bien lo hereda al nacer de la cuenta de
    canal por la que entró, cuando esa cuenta tiene departamento asignado.
    """

    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_department_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: Horario de atención por día de la semana; ver ``app/core/business_hours.py``.
    #: Vacío = se atiende siempre, que es como funcionaba antes de este campo.
    business_hours: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
    #: Zona con la que se interpreta ese horario (IANA). Nulo = UTC.
    timezone: Mapped[str | None] = mapped_column(String(64))
    #: Aviso que recibe el cliente que escribe fuera de horario, una sola vez
    #: por conversación. Nulo = no se le avisa nada.
    out_of_hours_message: Mapped[str | None] = mapped_column(Text)
    #: Minutos **hábiles** para la primera respuesta humana. Nulo = sin
    #: objetivo, que es como venía funcionando antes de este campo.
    first_response_target_minutes: Mapped[int | None] = mapped_column(Integer)


class CannedResponse(Base, TimestampMixin):
    """Plantilla de texto que un agente inserta en el composer por su atajo."""

    __tablename__ = "canned_responses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "shortcode", name="uq_canned_response_shortcode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    shortcode: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Label(Base, TimestampMixin):
    """Etiqueta reutilizable del tenant, aplicable a conversaciones."""

    __tablename__ = "labels"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_label_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="#6b7280", nullable=False)


class SavedView(Base, TimestampMixin):
    """Combinación de filtros de la bandeja, guardada para volver a ella.

    ``owner_agent_id`` decide su alcance: con agente es una vista personal, que
    esa persona ve desde cualquier equipo; en nulo es una vista del equipo,
    visible para todo el inquilino. El nombre se comprueba en la aplicación y
    no con una restricción única, porque en PostgreSQL dos nulos no colisionan
    y las vistas del equipo quedarían sin proteger.
    """

    __tablename__ = "saved_views"
    __table_args__ = (Index("ix_saved_views_tenant_owner", "tenant_id", "owner_agent_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: Nulo = vista del equipo; con agente = personal de esa persona.
    owner_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    #: ``scope``, ``status``, ``channel``, ``department`` y ``label``.
    filters: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)


class Macro(Base, TimestampMixin):
    """Secuencia de acciones sobre una conversación, ejecutable de un clic.

    Los pasos se guardan como JSON —``[{"action": "label", "label_id": …}]``—
    porque son una lista ordenada y heterogénea que solo se lee entera: nunca
    hace falta consultarla desde SQL, y una tabla de pasos obligaría a un join
    y a un campo distinto por cada tipo de acción.
    """

    __tablename__ = "macros"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_macro_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    steps: Mapped[list[Any]] = mapped_column(JSONBType, default=list, nullable=False)


class ConversationLabel(Base):
    """Etiquetas aplicadas a una conversación."""

    __tablename__ = "conversation_labels"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True
    )


class Agent(Base, TimestampMixin):
    """Operador humano de la bandeja de entrada.

    Las credenciales locales (correo y contraseña) son el mecanismo de por
    defecto: el servicio funciona sin depender de un proveedor de identidad
    externo. La contraseña se guarda derivada con ``scrypt``, nunca en claro,
    y ``password_hash`` queda ``None`` en las cuentas que solo entran por el
    inicio de sesión único (ver ``app/core/saml.py``) — no es un estado de
    error, sino la representación de "esta cuenta no tiene clave propia".
    """

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_agent_email"),
        CheckConstraint(
            "role IN ('agent','supervisor','admin')", name="ck_agent_role"
        ),
        Index("ix_agents_tenant_role", "tenant_id", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(32), default=ROLE_AGENT, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Derivación ``scrypt$n$r$p$salt$hash``. Nulo mientras no se fije contraseña.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    #: ``available`` | ``away`` | ``offline``; orienta el reparto de la cola.
    presence: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Departamento principal, fijado al crear la cuenta. El administrador
    #: puede otorgar acceso a otros mediante ``granted_departments``.
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )

    #: Departamentos adicionales, más allá del principal.
    granted_departments: Mapped[list[Department]] = relationship(secondary="agent_departments")

    @property
    def is_supervisor(self) -> bool:
        return self.role in SUPERVISOR_ROLES

    @property
    def label(self) -> str:
        return self.display_name or self.email


class AgentDepartment(Base):
    """Departamentos adicionales que un agente puede atender, además del suyo."""

    __tablename__ = "agent_departments"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True
    )


class AgentSession(Base):
    """Sesión de consola. Se guarda el resumen del token, no el token.

    Mantener las sesiones en la base de datos permite revocarlas de inmediato,
    algo que un token firmado sin estado no ofrece.
    """

    __tablename__ = "agent_sessions"
    __table_args__ = (Index("ix_agent_sessions_agent", "agent_id", "expires_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))


class ContactSession(Base):
    """Sesión del chatbox público. Se guarda el resumen del token, no el token.

    Calcada de ``AgentSession``: vive en la base para poder revocarse de
    inmediato y el token en sí nunca se persiste, solo su resumen.
    """

    __tablename__ = "contact_sessions"
    __table_args__ = (Index("ix_contact_sessions_contact", "contact_id", "expires_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))


# --------------------------------------------------------------------------- #
# Conversaciones y mensajes
# --------------------------------------------------------------------------- #
class Conversation(Base, TimestampMixin):
    """Hilo de conversación por canal."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "channel",
            "channel_conversation_id",
            name="uq_conversation_channel_thread",
        ),
        Index("ix_conversations_inbox", "tenant_id", "status", "last_message_at"),
        # ``open`` es lo pendiente, ``in_progress`` lo que alguien ya está
        # resolviendo y ``closed`` lo solucionado. Se conservan los nombres
        # originales de los dos extremos para no reescribir lo ya guardado.
        CheckConstraint(
            "status IN ('open','in_progress','snoozed','closed')",
            name="ck_conversation_status",
        ),
        CheckConstraint(
            "control IN ('bot','human')", name="ck_conversation_control"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[ChannelKind] = mapped_column(_enum(ChannelKind, "channel_kind"), nullable=False)
    channel_conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="SET NULL")
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    #: Nulo mientras no se derivó a un departamento concreto.
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), index=True
    )

    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    #: ``bot`` = respuesta automática activa; ``human`` = agente al mando.
    control: Mapped[str] = mapped_column(String(16), default="bot", nullable=False)

    #: Objetivo de primera respuesta. ``first_response_due_at`` se calcula en
    #: minutos hábiles del departamento, de modo que el reloj no corre de
    #: noche; nulo mientras no haya objetivo que cumplir. La respuesta del
    #: asistente no cuenta: solo se anota cuando contesta una persona, porque
    #: lo que se mide es cuánto tarda el equipo en atender.
    first_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Momento en que se dio por incumplido. Se anota una sola vez.
    sla_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subject: Mapped[str | None] = mapped_column(String(255))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unread_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Referencia serializada para responder o iniciar de forma proactiva.
    conversation_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
    #: Estado del flujo conversacional: paso actual, variables recogidas, etc.
    state: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)
    tags: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    contact: Mapped[Contact | None] = relationship()
    #: Responsable actual. Nulo mientras la conversación está en la cola común.
    assignee: Mapped[Agent | None] = relationship(foreign_keys=[assignee_id])
    department: Mapped[Department | None] = relationship()
    labels: Mapped[list[Label]] = relationship(secondary="conversation_labels")


class Message(Base, TimestampMixin):
    """Mensaje individual, en formato canónico."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "channel", "provider_message_id", name="uq_message_provider_id"
        ),
        Index("ix_messages_thread", "conversation_id", "created_at"),
        Index("ix_messages_client_id", "client_message_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[ChannelKind] = mapped_column(_enum(ChannelKind, "channel_kind"), nullable=False)
    direction: Mapped[Direction] = mapped_column(_enum(Direction, "direction"), nullable=False)
    content_type: Mapped[ContentType] = mapped_column(
        _enum(ContentType, "content_type"), default=ContentType.TEXT, nullable=False
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        _enum(DeliveryStatus, "delivery_status"), default=DeliveryStatus.PENDING, nullable=False
    )

    #: Identificador asignado por el proveedor. Nulo mientras el envío está en cola.
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    #: Identificador propio; permite conciliar el acuse de recibo del proveedor.
    client_message_id: Mapped[uuid.UUID | None] = mapped_column()

    text: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=list, nullable=False)
    action: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    #: ``contact`` | ``bot`` | ``agent`` | ``system``
    author_type: Mapped[str] = mapped_column(String(16), default="contact", nullable=False)
    author_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    author_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Carga original del proveedor, conservada para auditoría y reproceso.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    events: Mapped[list[MessageEvent]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class MessageEvent(Base):
    """Traza del ciclo de vida de entrega de un mensaje."""

    __tablename__ = "message_events"
    __table_args__ = (Index("ix_message_events_message", "message_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        _enum(DeliveryStatus, "delivery_status"), nullable=False
    )
    provider_status: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)

    message: Mapped[Message] = relationship(back_populates="events")


# --------------------------------------------------------------------------- #
# Fiabilidad: idempotencia y cola de salida
# --------------------------------------------------------------------------- #
class InboundDedupe(Base):
    """Registro de claves ya procesadas.

    Los webhooks de WhatsApp y del Bot Framework reintentan la entrega; sin esta
    tabla, un reintento generaría respuestas duplicadas.
    """

    __tablename__ = "inbound_dedupe"

    dedupe_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    channel: Mapped[ChannelKind] = mapped_column(_enum(ChannelKind, "channel_kind"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
        index=True,
    )


class OutboxItem(Base, TimestampMixin):
    """Cola transaccional de salida (patrón *transactional outbox*).

    La respuesta se confirma en la misma transacción que persiste el mensaje
    entrante; el envío efectivo lo realiza un trabajador con reintentos y
    retroceso exponencial.
    """

    __tablename__ = "outbox"
    __table_args__ = (
        Index("ix_outbox_ready", "status", "next_attempt_at"),
        CheckConstraint(
            "status IN ('pending','in_progress','sent','failed','dead')",
            name="ck_outbox_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    channel: Mapped[ChannelKind] = mapped_column(_enum(ChannelKind, "channel_kind"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    #: ``ConversationRef`` serializada más ``OutboundMessage`` serializado.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- #
# Observabilidad de la capa de IA
# --------------------------------------------------------------------------- #
class AIRun(Base):
    """Registro de cada invocación al modelo, para coste y depuración."""

    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_runs_conversation", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    handler: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_read_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    """Traza de acciones administrativas y transiciones de control."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_tenant_time", "tenant_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(64))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Trabajo en equipo: derivaciones y notas internas
# --------------------------------------------------------------------------- #
class Assignment(Base):
    """Registro inmutable de cada cambio de responsable de una conversación.

    La derivación no mueve ni copia mensajes: la conversación es la misma fila y
    conserva su historial completo. Aquí solo se anota quién pasó a atenderla,
    quién la traspasó y por qué, de modo que la trazabilidad sobreviva a
    cualquier número de traspasos.
    """

    __tablename__ = "assignments"
    __table_args__ = (
        Index("ix_assignments_conversation", "conversation_id", "created_at"),
        Index("ix_assignments_to_agent", "to_agent_id", "created_at"),
        CheckConstraint(
            "action IN ('claim','transfer','release','close','reopen','transfer_department')",
            name="ck_assignment_action",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    #: ``claim`` | ``transfer`` | ``release`` | ``close`` | ``reopen``
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Responsable anterior. Nulo si la conversación estaba en la cola común.
    from_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    #: Nuevo responsable. Nulo cuando la conversación vuelve a la cola común.
    to_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    #: Quien ejecuta la acción; puede ser un supervisor que reasigna a un tercero.
    by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    #: Solo en ``transfer_department``: a qué departamento quedó derivada.
    to_department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class InternalNote(Base):
    """Anotación visible solo para el equipo.

    Se guarda en una tabla propia y no en ``messages`` de forma deliberada: así
    ningún adaptador de canal puede enviarla al cliente por descuido.
    """

    __tablename__ = "internal_notes"
    __table_args__ = (Index("ix_internal_notes_conversation", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    #: Agentes mencionados, para avisarles: ``["uuid", ...]``.
    mentions: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )


class ContactComment(Base):
    """Comentario de supervisión sobre un contacto, con autor y fecha.

    Distinto de ``InternalNote``: esa tabla anota una conversación puntual, esta
    anota al contacto en sí, de modo que el comentario se conserva aunque el
    contacto derive en varias conversaciones distintas.
    """

    __tablename__ = "contact_comments"
    __table_args__ = (Index("ix_contact_comments_contact", "contact_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
