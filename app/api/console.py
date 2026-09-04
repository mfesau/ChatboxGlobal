"""Consola de equipo: cola común, cartera propia, derivaciones y supervisión.

Reglas de visibilidad, aplicadas en SQL y no en memoria:

* La **cola común** —conversaciones sin responsable— es el punto único por el que
  entra todo, sea de WhatsApp, de Microsoft Teams o del chatbox web. Cualquier
  agente la ve y puede tomar trabajo de ella.
* Un **agente** ve además su propia cartera. No ve la de sus compañeros.
* **Supervisión** ve la totalidad de las conversaciones del inquilino.

Derivar no mueve ni copia nada: la conversación es la misma fila y conserva su
historial íntegro. Solo cambia el responsable, y el cambio queda anotado en
``assignments`` con autor, destinatario y motivo.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import date
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, field_validator

from app.api.auth import generate_temporary_password
from app.api.deps import (
    AdminDep,
    OrchestratorDep,
    Principal,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    SupervisorDep,
    authorized_conversation,
)
from app.channels.whatsapp import TemplatesUnavailable
from app.core.branding import (
    BRANDING_SETTINGS_KEY,
    InvalidColor,
    brand_css,
    derive_palette,
    normalize_hex,
    read_accent,
)
from app.core.business_hours import SERVICE_SETTINGS_KEY, parse_clock, resolve_timezone
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    Direction,
    OutboundMessage,
    Party,
    utcnow,
)
from app.core.hub import agent_topic, conversation_topic, hub, inbox_topic
from app.core.localized import SUPPORTED_LOCALES
from app.core.mailer import invitation_email_body, send_email
from app.core.secrets import (
    DecryptionError,
    EncryptionNotConfiguredError,
    decrypt_json,
    encrypt_json,
)
from app.core.security import WeakPasswordError, hash_password
from app.core.storage import save_upload
from app.db import repositories as repo
from app.db.models import (
    ROLE_ADMIN,
    ROLE_AGENT,
    Agent,
    CannedResponse,
    ChannelAccount,
    Conversation,
    Department,
    HotelRatePlan,
    HotelReservation,
    HotelRoom,
    HotelRoomType,
    Tenant,
)
from app.handlers.builtin import FallbackHandler
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["consola"])

ConversationDep = Annotated[Conversation, Depends(authorized_conversation)]
Scope = Literal["unassigned", "mine", "mine_or_unassigned", "all"]


# --------------------------------------------------------------------------- #
# Esquemas
# --------------------------------------------------------------------------- #
class LabelOut(BaseModel):
    id: uuid.UUID
    name: str
    color: str


class ConversationOut(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    control: str
    subject: str | None
    contact_name: str | None
    assignee_id: uuid.UUID | None
    assignee_name: str | None
    department_id: uuid.UUID | None
    department_name: str | None
    unread_count: int
    last_message_at: str | None
    #: El mismo estado con el nombre que usa el equipo: ``pending``,
    #: ``in_progress`` o ``solved``. Evita repartir el mapeo por la interfaz.
    work_state: str
    #: ``None`` sin objetivo; ``pending`` esperando; ``met`` respondida a
    #: tiempo; ``breached`` vencida sin respuesta humana.
    sla_status: str | None = None
    sla_due_at: str | None = None
    labels: list[LabelOut] = Field(default_factory=list)


class MessageOut(BaseModel):
    id: uuid.UUID
    direction: str
    author_type: str
    content_type: str
    status: str
    text: str | None
    attachments: list[dict[str, Any]]
    created_at: str | None


class AssignmentOut(BaseModel):
    id: uuid.UUID
    action: str
    from_agent: str | None
    to_agent: str | None
    to_department: str | None
    by_agent: str | None
    note: str | None
    created_at: str


class DepartmentOut(BaseModel):
    id: uuid.UUID
    name: str
    is_active: bool
    business_hours: dict[str, Any] = Field(default_factory=dict)
    timezone: str | None = None
    out_of_hours_message: str | dict[str, str] | None = None
    first_response_target_minutes: int | None = None


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class BusinessHoursIn(BaseModel):
    """Horario semanal: ``{"1": [["09:00", "18:00"]]}``, 1 = lunes … 7 = domingo."""

    business_hours: dict[str, list[list[str]]] = Field(default_factory=dict)
    timezone: str | None = Field(default=None, max_length=64)
    #: Texto suelto —igual para todos— o uno por idioma, ``{"es": …}``. El
    #: cliente recibe el de su idioma; ver ``app/core/localized.py``.
    out_of_hours_message: str | dict[str, str] | None = None

    @field_validator("out_of_hours_message")
    @classmethod
    def _check_locales(cls, value):
        return _validated_localized(value)
    #: Minutos hábiles para la primera respuesta humana. Nulo = sin objetivo.
    first_response_target_minutes: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("business_hours")
    @classmethod
    def _check_schedule(
        cls, value: dict[str, list[list[str]]]
    ) -> dict[str, list[list[str]]]:
        """Se valida al entrar y no al usarlo: un horario mal escrito debe
        fallar cuando alguien lo configura, no de madrugada ante un cliente."""
        for day, spans in value.items():
            if day not in {"1", "2", "3", "4", "5", "6", "7"}:
                raise ValueError(f"Día no válido: {day!r}; use «1» (lunes) a «7» (domingo)")
            for span in spans:
                if len(span) != 2:
                    raise ValueError(f"El día {day} tiene un tramo que no es «desde, hasta»")
                for clock in span:
                    if parse_clock(clock) is None:
                        raise ValueError(f"Hora no válida: {clock!r}; use «HH:MM»")
        return value


class HotelModuleOut(BaseModel):
    enabled: bool


class HotelModuleIn(BaseModel):
    enabled: bool


class HotelRoomTypeOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    capacity: int
    is_active: bool


class HotelRoomTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    capacity: int = Field(default=2, ge=1, le=20)


class HotelRoomTypePatchIn(BaseModel):
    description: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=20)
    is_active: bool | None = None


class HotelRoomOut(BaseModel):
    id: uuid.UUID
    room_type_id: uuid.UUID
    room_type_name: str
    code: str
    status: str
    notes: str | None


class HotelRoomIn(BaseModel):
    room_type_id: uuid.UUID
    code: str = Field(min_length=1, max_length=20)
    notes: str | None = None


class HotelRoomPatchIn(BaseModel):
    status: Literal["available", "maintenance", "out_of_service"] | None = None
    notes: str | None = None


class HotelRatePlanOut(BaseModel):
    id: uuid.UUID
    room_type_id: uuid.UUID
    name: str
    starts_on: str | None
    ends_on: str | None
    nightly_price_cents: int
    currency: str


class HotelRatePlanIn(BaseModel):
    room_type_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    starts_on: date | None = None
    ends_on: date | None = None
    nightly_price_cents: int = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)


class HotelReservationOut(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    room_code: str
    room_type_name: str
    guest_name: str
    guest_phone: str | None
    guest_email: str | None
    check_in: str
    check_out: str
    guests: int
    status: str
    nightly_price_cents: int | None
    currency: str
    notes: str | None
    contact_id: uuid.UUID | None
    conversation_id: uuid.UUID | None


class HotelReservationIn(BaseModel):
    room_id: uuid.UUID
    guest_name: str = Field(min_length=1, max_length=160)
    guest_phone: str | None = None
    guest_email: str | None = None
    #: Vincula la reserva a un contacto ya conocido —encontrado por búsqueda—,
    #: además del nombre y los datos de contacto sueltos.
    contact_id: uuid.UUID | None = None
    check_in: date
    check_out: date
    guests: int = Field(default=1, ge=1, le=20)
    #: Nulo = se toma la tarifa vigente de la categoría, si hay alguna cargada.
    nightly_price_cents: int | None = Field(default=None, gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    notes: str | None = None

    @field_validator("check_out")
    @classmethod
    def _check_dates(cls, value: date, info: Any) -> date:
        check_in = info.data.get("check_in")
        if check_in is not None and value <= check_in:
            raise ValueError("La salida debe ser posterior a la entrada")
        return value


class HotelReservationPatchIn(BaseModel):
    """Corrige una reserva ya cargada. Solo se aplican los campos recibidos."""

    room_id: uuid.UUID | None = None
    check_in: date | None = None
    check_out: date | None = None
    guest_name: str | None = Field(default=None, min_length=1, max_length=160)
    guest_phone: str | None = None
    guest_email: str | None = None
    contact_id: uuid.UUID | None = None
    guests: int | None = Field(default=None, ge=1, le=20)
    nightly_price_cents: int | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    notes: str | None = None


class HotelReservationStatusIn(BaseModel):
    status: Literal["confirmed", "checked_in", "checked_out", "cancelled", "no_show"]


class HotelRatePlanPatchIn(BaseModel):
    """Edita una tarifa ya cargada. Solo se aplican los campos recibidos."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    starts_on: date | None = None
    ends_on: date | None = None
    nightly_price_cents: int | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class HotelContactOut(BaseModel):
    id: uuid.UUID
    display_name: str | None
    primary_phone: str | None
    primary_email: str | None


class HotelRevenueOut(BaseModel):
    currency: str
    total_cents: int


class HotelReportOut(BaseModel):
    reference_date: str
    arrivals_today: int
    departures_today: int
    occupied_rooms: int
    total_rooms: int
    pending_count: int
    revenue_next_30_days: list[HotelRevenueOut]


SHORTCODE_PATTERN = r"^[a-z0-9_-]+$"


class CannedResponseOut(BaseModel):
    id: uuid.UUID
    shortcode: str
    title: str
    body: str


class CannedResponseIn(BaseModel):
    shortcode: str = Field(min_length=1, max_length=40, pattern=SHORTCODE_PATTERN)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4_000)


class CannedResponsePatchIn(BaseModel):
    shortcode: str | None = Field(
        default=None, min_length=1, max_length=40, pattern=SHORTCODE_PATTERN
    )
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, min_length=1, max_length=4_000)


class LabelIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#6b7280", max_length=20)


class ConversationLabelsIn(BaseModel):
    label_ids: list[uuid.UUID] = Field(default_factory=list)


class ConversationStateIn(BaseModel):
    """En qué punto de su resolución está la conversación."""

    state: Literal["pending", "in_progress", "solved"]


#: Acciones que una macro puede encadenar. Son las mismas que el equipo hace
#: a mano desde la consola; la macro solo las ejecuta juntas y en orden.
MacroAction = Literal["label", "note", "reply", "transfer_department", "close"]


class MacroStep(BaseModel):
    """Un paso de la macro. Cada acción usa solo el campo que le corresponde."""

    action: MacroAction
    label_id: uuid.UUID | None = None
    department_id: uuid.UUID | None = None
    canned_response_id: uuid.UUID | None = None
    body: str | None = Field(default=None, max_length=2_000)


class MacroOut(BaseModel):
    id: uuid.UUID
    name: str
    steps: list[MacroStep]


class MacroIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    steps: list[MacroStep] = Field(min_length=1, max_length=20)

    @field_validator("steps")
    @classmethod
    def _check_steps(cls, value: list[MacroStep]) -> list[MacroStep]:
        """Cada acción exige lo suyo, y se comprueba al guardar la macro.

        Un paso incompleto descubierto al ejecutarla dejaría la conversación a
        medio procesar delante de quien la usó.
        """
        required = {
            "label": "label_id",
            "transfer_department": "department_id",
            "reply": "canned_response_id",
            "note": "body",
        }
        for index, step in enumerate(value, start=1):
            field = required.get(step.action)
            if field and getattr(step, field) is None:
                raise ValueError(f"El paso {index} ({step.action}) necesita «{field}»")
        return value


class SavedViewFilters(BaseModel):
    """Los mismos criterios que acepta ``GET /api/conversations``."""

    scope: Scope | None = None
    status: str | None = Field(default=None, max_length=16)
    channel: ChannelKind | None = None
    department: uuid.UUID | None = None
    label: uuid.UUID | None = None


class SavedViewOut(BaseModel):
    id: uuid.UUID
    name: str
    filters: dict[str, Any]
    #: ``False`` = personal de quien pregunta; ``True`` = de todo el equipo.
    shared: bool


class SavedViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    filters: SavedViewFilters = Field(default_factory=SavedViewFilters)
    shared: bool = False


class AgentDepartmentsIn(BaseModel):
    department_id: uuid.UUID | None = None
    extra_department_ids: list[uuid.UUID] = Field(default_factory=list)


class NoteOut(BaseModel):
    id: uuid.UUID
    agent: str | None
    body: str
    created_at: str


class ContactCommentOut(BaseModel):
    id: uuid.UUID
    agent: str | None
    body: str
    created_at: str


class ContactDetailOut(BaseModel):
    id: uuid.UUID
    display_name: str | None
    primary_phone: str | None
    primary_email: str | None
    comments: list[ContactCommentOut]


class ContactUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=160)
    primary_phone: str | None = Field(default=None, max_length=32)
    primary_email: str | None = Field(default=None, max_length=254)


class ContactCommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2_000)


class ContactSummaryOut(BaseModel):
    id: uuid.UUID
    display_name: str | None
    primary_phone: str | None
    primary_email: str | None
    is_blocked: bool
    conversation_count: int
    last_message_at: str | None


class ContactProfileOut(BaseModel):
    id: uuid.UUID
    display_name: str | None
    primary_phone: str | None
    primary_email: str | None
    is_blocked: bool
    comments: list[ContactCommentOut]
    conversations: list[ConversationOut]


class AgentOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    role: str
    presence: str
    is_active: bool
    department_id: uuid.UUID | None
    extra_department_ids: list[uuid.UUID]


class AttachmentIn(BaseModel):
    content_type: str = "document"
    url: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    caption: str | None = None


class ReplyIn(BaseModel):
    text: str = Field(default="", max_length=8_000)
    quick_replies: list[dict[str, str]] = Field(default_factory=list)
    attachments: list[AttachmentIn] = Field(default_factory=list)


class TransferIn(BaseModel):
    #: Exactamente uno de los dos: a un compañero, o a la cola de un departamento.
    to_agent_id: uuid.UUID | None = None
    to_department_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=2_000)


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4_000)
    mentions: list[uuid.UUID] = Field(default_factory=list)


class ReleaseIn(BaseModel):
    note: str | None = Field(default=None, max_length=2_000)


class ControlIn(BaseModel):
    control: Literal["bot", "human"]


class NewAgentIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str | None = Field(default=None, max_length=160)
    role: Literal["agent", "supervisor", "admin"] = ROLE_AGENT
    #: Obligatoria para el rol "agent". Se ignora para "supervisor" y "admin":
    #: esas cuentas reciben una contraseña generada por el sistema, entregada
    #: por correo de invitación (ver create_agent), no una elegida a mano.
    password: str | None = Field(default=None, min_length=8, max_length=256)
    #: Departamento principal. Ninguno equivale a "generalista": atiende la
    #: cola sin departamento, no la de uno concreto salvo que se le otorgue.
    department_id: uuid.UUID | None = None


class PasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class AgentUpdateIn(BaseModel):
    #: Solo se aplican los campos recibidos; ``None`` significa "no tocar".
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    #: Permite reactivar una cuenta desactivada; ``DELETE`` sigue siendo el
    #: camino corto para desactivarla.
    is_active: bool | None = None


class StartConversationIn(BaseModel):
    """Inicio de conversacion saliente por WhatsApp."""

    #: Numero en formato internacional. Se acepta con ``+``, espacios o
    #: guiones y se normaliza: quien lo copia de una ficha no deberia tener
    #: que limpiarlo a mano.
    to: str
    template: str
    language: str = "en_US"
    #: Un valor por cada hueco ``{{n}}`` del cuerpo de la plantilla.
    variables: list[str] = Field(default_factory=list)

    @field_validator("to")
    @classmethod
    def _check_phone(cls, value: str) -> str:
        digits = re.sub(r"\D", "", value or "")
        # WhatsApp identifica al destinatario por el numero sin ``+``. El rango
        # es el de la norma E.164: prefijo de pais mas abonado.
        if not 8 <= len(digits) <= 15:
            raise ValueError("Escriba el numero con prefijo de pais, por ejemplo +595981234567")
        return digits


class BrandingIn(BaseModel):
    """Color de marca. ``None`` devuelve la consola a la paleta de partida."""

    accent: str | None = None

    @field_validator("accent")
    @classmethod
    def _check_color(cls, value):
        if value is None or not value.strip():
            return None
        try:
            return normalize_hex(value)
        except InvalidColor as exc:
            raise ValueError(str(exc)) from exc


class AdminSettingsIn(BaseModel):
    #: Como el aviso fuera de horario: una cadena, o un texto por idioma.
    fallback_message: str | dict[str, str]

    @field_validator("fallback_message")
    @classmethod
    def _check_locales(cls, value):
        checked = _validated_localized(value)
        if checked is None:
            raise ValueError("Escriba el texto en al menos un idioma")
        return checked


# --------------------------------------------------------------------------- #
# Bandeja
# --------------------------------------------------------------------------- #
@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    scope: Scope | None = None,
    conversation_status: str | None = Query(default="open", alias="status"),
    channel: ChannelKind | None = None,
    department: uuid.UUID | None = None,
    label: uuid.UUID | None = None,
    tenant: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ConversationOut]:
    """Devuelve la bandeja acotada al alcance permitido.

    Sin ``scope`` explícito, supervisión recibe todo y un agente recibe su
    cartera más la cola común. Si un agente pide ``all``, se degrada su petición
    en lugar de rechazarla: la consola es la misma para todos los roles.
    ``department`` acota la vista a uno de los departamentos ya accesibles;
    no amplía lo que ``department_ids`` permite.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    effective = _effective_scope(scope, principal)

    conversations = await repo.list_conversations(
        session,
        tenant_id=tenant_row.id,
        status=conversation_status,
        channel=channel,
        scope=effective,
        agent_id=principal.id,
        department_ids=principal.department_ids,
        department=department,
        label=label,
        limit=limit,
        offset=offset,
    )
    return [_conversation_out(row) for row in conversations]


@router.get("/inbox/summary")
async def inbox_summary(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> dict[str, Any]:
    """Contadores de las pestañas: cola común, cartera propia y total."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    summary: dict[str, Any] = {
        "unassigned": await repo.count_conversations(
            session,
            tenant_id=tenant_row.id,
            scope="unassigned",
            department_ids=principal.department_ids,
        ),
        "mine": await repo.count_conversations(
            session, tenant_id=tenant_row.id, scope="mine", agent_id=principal.id
        )
        if principal.id
        else 0,
        "is_supervisor": principal.is_supervisor,
    }
    if principal.is_supervisor:
        summary["all"] = await repo.count_conversations(session, tenant_id=tenant_row.id)
    return summary


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation: ConversationDep,
    session: SessionDep,
    limit: int = Query(default=200, ge=1, le=1_000),
) -> list[MessageOut]:
    """Historial completo del hilo, con independencia de cuántas veces se derivó."""
    messages = await repo.recent_messages(session, conversation.id, limit=limit)
    conversation.unread_count = 0
    return [
        MessageOut(
            id=row.id,
            direction="inbound" if row.direction is Direction.INBOUND else "outbound",
            author_type=row.author_type,
            content_type=str(row.content_type),
            status=str(row.status),
            text=row.text,
            attachments=list(row.attachments or []),
            created_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in messages
    ]


@router.post("/conversations/{conversation_id}/reply", status_code=status.HTTP_202_ACCEPTED)
async def reply_as_agent(
    conversation: ConversationDep,
    body: ReplyIn,
    session: SessionDep,
    principal: PrincipalDep,
    orchestrator: OrchestratorDep,
) -> dict[str, str]:
    """Responde al cliente por el canal de origen del hilo.

    Quien responde una conversación de la cola común la asume de forma
    implícita: evita que dos personas contesten a la vez y ahorra un paso en la
    interfaz.
    """
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id not in (None, principal.agent.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La conversación la atiende otro compañero",
        )
    if not body.text and not body.attachments:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La respuesta necesita texto o al menos un adjunto",
        )

    attachments = [Attachment.from_dict(a.model_dump()) for a in body.attachments]
    content_type = (
        attachments[0].content_type if attachments and not body.text else ContentType.TEXT
    )
    outbox_id = await orchestrator.send_from_agent(
        conversation_id=conversation.id,
        outbound=OutboundMessage(
            text=body.text or None,
            quick_replies=body.quick_replies,
            attachments=attachments,
            content_type=content_type,
        ),
        agent_id=principal.id,
        # La misma transacción de la petición: ni una segunda conexión ni riesgo
        # de interbloqueo, y la respuesta se confirma junto al resto del turno.
        session=session,
    )
    if outbox_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada"
        )
    # Contestó una persona: se detiene aquí el reloj del objetivo. Se anota al
    # encolar y no al entregar, porque lo que se mide es cuánto tardó el
    # equipo en atender, no cuánto tardó el canal en despachar el mensaje.
    await repo.mark_first_human_response(session, conversation=conversation)
    return {"status": "queued", "outbox_id": str(outbox_id)}


# --------------------------------------------------------------------------- #
# Conversaciones salientes
# --------------------------------------------------------------------------- #
def _render_template_body(body: str, variables: list[str]) -> str:
    """Sustituye los huecos ``{{n}}`` por los valores, para verlo en el hilo.

    Lo que viaja a WhatsApp es la plantilla con sus parametros aparte; esto es
    solo lo que se guarda y se muestra en la consola. Sin ello, el hilo abriria
    con una burbuja vacia y nadie sabria que se envio.
    """
    rendered = body or ""
    for index, value in enumerate(variables, start=1):
        rendered = rendered.replace(f"{{{{{index}}}}}", value)
    return rendered


@router.get("/whatsapp/templates")
async def list_whatsapp_templates(
    principal: PrincipalDep, orchestrator: OrchestratorDep
) -> list[dict[str, Any]]:
    """Plantillas aprobadas, para elegir al iniciar una conversacion."""
    adapter = orchestrator.registry.get(ChannelKind.WHATSAPP)
    try:
        return await adapter.list_templates()
    except TemplatesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/conversations/start", status_code=status.HTTP_201_CREATED)
async def start_conversation(
    body: StartConversationIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    orchestrator: OrchestratorDep,
    tenant: str | None = None,
) -> dict[str, str]:
    """Abre una conversacion de WhatsApp escribiendo primero.

    WhatsApp no deja mandar texto libre a quien no ha escrito en las ultimas 24
    horas; fuera de esa ventana solo admite plantillas aprobadas. Por eso aqui
    no hay campo de texto: se elige una plantilla y se rellenan sus huecos. En
    cuanto la persona conteste, el hilo queda abierto y la consola responde con
    texto normal como en cualquier otra conversacion.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    accounts = await repo.list_channel_accounts(session, tenant_id=tenant_row.id)
    account = next(
        (a for a in accounts if a.channel == ChannelKind.WHATSAPP and a.is_active), None
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No hay ninguna cuenta de WhatsApp activa",
        )

    adapter = orchestrator.registry.get(ChannelKind.WHATSAPP)
    try:
        plantillas = await adapter.list_templates()
    except TemplatesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # Se comprueba contra la lista real de Meta en vez de confiar en lo que
    # llega: enviar una plantilla inexistente, en otro idioma o con un numero
    # de parametros distinto lo rechaza la Graph API con un error opaco, y el
    # mensaje se perderia en la cola sin que nadie entienda por que.
    elegida = next(
        (
            t
            for t in plantillas
            if t["name"] == body.template and t["language"] == body.language
        ),
        None,
    )
    if elegida is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay plantilla aprobada «{body.template}» en {body.language}",
        )
    if len(body.variables) != elegida["variables"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"La plantilla necesita {elegida['variables']} dato(s) "
                f"y llegaron {len(body.variables)}"
            ),
        )

    # Se replica lo que hace el adaptador con un mensaje entrante, para que la
    # conversacion sea indistinguible de una que empezo el cliente: el
    # identificador del hilo es el propio numero, y el contacto se unifica por
    # telefono con el que ya exista.
    ref = ConversationRef(
        channel=ChannelKind.WHATSAPP,
        channel_conversation_id=body.to,
        channel_account_id=account.external_id,
        extra={"wa_id": body.to},
    )
    contact = await repo.resolve_contact(
        session,
        tenant_id=tenant_row.id,
        channel=ChannelKind.WHATSAPP,
        party=Party(channel_user_id=body.to, phone=f"+{body.to}", raw={"wa_id": body.to}),
    )
    conversation = await repo.resolve_conversation(
        session,
        tenant_id=tenant_row.id,
        ref=ref,
        contact_id=contact.id,
        channel_account=account,
    )

    plantilla: dict[str, Any] = {
        "name": body.template,
        "language": {"code": body.language},
    }
    if body.variables:
        plantilla["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": v} for v in body.variables],
            }
        ]

    outbox_id = await orchestrator.send_from_agent(
        conversation_id=conversation.id,
        outbound=OutboundMessage(
            text=_render_template_body(elegida["body"], body.variables),
            content_type=ContentType.TEXT,
            channel_data={"template": plantilla},
        ),
        agent_id=principal.id,
        session=session,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="conversation_started",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"to": body.to, "template": body.template},
    )
    return {
        "status": "queued",
        "conversation_id": str(conversation.id),
        "outbox_id": str(outbox_id),
    }


# --------------------------------------------------------------------------- #
# Adjuntos
# --------------------------------------------------------------------------- #
@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile,
    principal: PrincipalDep,
    settings: SettingsDep,
    session: SessionDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Sube una imagen para adjuntarla a la próxima respuesta de un agente."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    attachment = await save_upload(file, namespace=str(tenant_row.id), settings=settings)
    return attachment.to_dict()


@router.post("/conversations/{conversation_id}/reopen")
async def reopen_conversation(
    conversation: ConversationDep, session: SessionDep, principal: PrincipalDep
) -> dict[str, str]:
    """Reabre un hilo cerrado sin perder nada de lo conversado."""
    conversation.status = "open"
    await repo.record_assignment(
        session,
        conversation=conversation,
        action="reopen",
        to_agent_id=conversation.assignee_id,
        by_agent_id=principal.id,
    )
    return {"status": "ok", "conversation_status": "open"}


# --------------------------------------------------------------------------- #
# Derivación entre compañeros
# --------------------------------------------------------------------------- #
@router.post("/conversations/{conversation_id}/claim")
async def claim_conversation(
    conversation: ConversationDep, session: SessionDep, principal: PrincipalDep
) -> dict[str, Any]:
    """Toma una conversación de la cola común."""
    if principal.agent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La clave de servicio no puede atender conversaciones; inicie sesión",
        )
    if conversation.assignee_id not in (None, principal.agent.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Otro compañero ya la está atendiendo",
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="claim",
        to_agent_id=principal.agent.id,
        by_agent_id=principal.agent.id,
    )
    await _notify_assignment(session, conversation, entry, principal, target=principal.agent)
    return {
        "status": "ok",
        "assignee_id": str(principal.agent.id),
        "assignee_name": principal.agent.label,
        "control": conversation.control,
    }


@router.post("/conversations/{conversation_id}/transfer")
async def transfer_conversation(
    conversation: ConversationDep,
    body: TransferIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Deriva la conversación a un compañero, o a la cola de un departamento.

    Un agente solo puede derivar lo que atiende; supervisión puede reasignar
    cualquier conversación. El motivo, si se indica, queda además como nota
    interna, de modo que quien la recibe entiende el contexto sin preguntar.
    """
    if (body.to_agent_id is None) == (body.to_department_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Elija un compañero o un departamento, no ambos ni ninguno",
        )
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id not in (None, principal.agent.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puede derivar las conversaciones que atiende",
        )

    if body.to_department_id is not None:
        return await _transfer_to_department(conversation, body, session, principal)

    target = await repo.get_agent(session, body.to_agent_id)
    if target is None or target.tenant_id != conversation.tenant_id or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El destinatario no existe o está inactivo",
        )
    if target.id == conversation.assignee_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="La conversación ya es suya"
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="transfer",
        to_agent_id=target.id,
        by_agent_id=principal.id,
        note=body.note,
    )
    if body.note:
        await repo.add_internal_note(
            session,
            conversation=conversation,
            agent_id=principal.id,
            body=f"Derivada a {target.label}: {body.note}",
            mentions=[str(target.id)],
        )
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="conversation_transferred",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"to": target.email, "note": body.note},
    )
    await _notify_assignment(session, conversation, entry, principal, target=target)
    log.info(
        "conversation_transferred",
        conversation_id=str(conversation.id),
        by=principal.label,
        to=target.email,
    )
    return {
        "status": "ok",
        "assignee_id": str(target.id),
        "assignee_name": target.label,
        "control": conversation.control,
    }


async def _transfer_to_department(
    conversation: Conversation, body: TransferIn, session: Any, principal: Principal
) -> dict[str, Any]:
    department = await repo.get_department(session, body.to_department_id)
    if (
        department is None
        or department.tenant_id != conversation.tenant_id
        or not department.is_active
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El departamento no existe o está inactivo",
        )
    if department.id == conversation.department_id and conversation.assignee_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La conversación ya está en la cola de ese departamento",
        )

    entry = await repo.transfer_to_department(
        session,
        conversation=conversation,
        department_id=department.id,
        by_agent_id=principal.id,
        note=body.note,
    )
    if body.note:
        await repo.add_internal_note(
            session,
            conversation=conversation,
            agent_id=principal.id,
            body=f"Derivada al departamento {department.name}: {body.note}",
        )
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="conversation_transferred_to_department",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"department": department.name, "note": body.note},
    )
    await _notify_assignment(session, conversation, entry, principal, department=department)
    log.info(
        "conversation_transferred_to_department",
        conversation_id=str(conversation.id),
        by=principal.label,
        department=department.name,
    )
    return {
        "status": "ok",
        "assignee_id": None,
        "assignee_name": None,
        "department_id": str(department.id),
        "department_name": department.name,
        "control": conversation.control,
    }


@router.post("/conversations/{conversation_id}/release")
async def release_conversation(
    conversation: ConversationDep,
    body: ReleaseIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Devuelve la conversación a la cola común y reactiva al asistente."""
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id != principal.agent.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puede liberar las conversaciones que atiende",
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="release",
        to_agent_id=None,
        by_agent_id=principal.id,
        note=body.note,
    )
    await _notify_assignment(session, conversation, entry, principal)
    return {
        "status": "ok",
        "assignee_id": None,
        "assignee_name": None,
        "control": conversation.control,
    }


@router.get("/conversations/{conversation_id}/assignments", response_model=list[AssignmentOut])
async def assignment_history(
    conversation: ConversationDep, session: SessionDep
) -> list[AssignmentOut]:
    """Traza de derivaciones: quién la atendió, quién la pasó y por qué."""
    entries = await repo.assignment_history(session, conversation.id)
    names = await _agent_names(session, conversation.tenant_id)
    department_names = await _department_names(session, conversation.tenant_id)
    return [
        AssignmentOut(
            id=entry.id,
            action=entry.action,
            from_agent=names.get(entry.from_agent_id),
            to_agent=names.get(entry.to_agent_id),
            to_department=department_names.get(entry.to_department_id),
            by_agent=names.get(entry.by_agent_id),
            note=entry.note,
            created_at=entry.created_at.isoformat(),
        )
        for entry in entries
    ]


# --------------------------------------------------------------------------- #
# Notas internas
# --------------------------------------------------------------------------- #
@router.get("/conversations/{conversation_id}/notes", response_model=list[NoteOut])
async def list_notes(conversation: ConversationDep, session: SessionDep) -> list[NoteOut]:
    notes = await repo.list_internal_notes(session, conversation.id)
    names = await _agent_names(session, conversation.tenant_id)
    return [
        NoteOut(
            id=note.id,
            agent=names.get(note.agent_id),
            body=note.body,
            created_at=note.created_at.isoformat(),
        )
        for note in notes
    ]


@router.post("/conversations/{conversation_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(
    conversation: ConversationDep,
    body: NoteIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, str]:
    """Añade una anotación visible solo para el equipo; el cliente no la recibe."""
    note = await repo.add_internal_note(
        session,
        conversation=conversation,
        agent_id=principal.id,
        body=body.body,
        mentions=[str(mention) for mention in body.mentions],
    )
    for mentioned in body.mentions:
        await hub.publish(
            agent_topic(str(mentioned)),
            {
                "type": "mentioned",
                "conversation_id": str(conversation.id),
                "by": principal.label,
                "body": body.body[:200],
            },
        )
    return {"status": "ok", "note_id": str(note.id)}


# --------------------------------------------------------------------------- #
# Datos del contacto
# --------------------------------------------------------------------------- #
@router.get("/conversations/{conversation_id}/contact", response_model=ContactDetailOut)
async def get_contact_detail(
    conversation: ConversationDep, session: SessionDep
) -> ContactDetailOut:
    """Ficha del contacto: nombre, teléfono, correo e historial de comentarios.

    Cualquier agente con acceso a la conversación puede consultarla; editarla
    queda reservado a supervisión y administración (ver ``update_contact_detail``).
    """
    if conversation.contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta conversación no tiene contacto asociado",
        )
    contact = await repo.get_contact(session, conversation.contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    comments = await repo.list_contact_comments(session, conversation.contact_id)
    names = await _agent_names(session, conversation.tenant_id)
    return ContactDetailOut(
        id=contact.id,
        display_name=contact.display_name,
        primary_phone=contact.primary_phone,
        primary_email=contact.primary_email,
        comments=[
            ContactCommentOut(
                id=comment.id,
                agent=names.get(comment.agent_id),
                body=comment.body,
                created_at=comment.created_at.isoformat(),
            )
            for comment in comments
        ],
    )


@router.patch("/conversations/{conversation_id}/contact")
async def update_contact_detail(
    conversation: ConversationDep,
    body: ContactUpdateIn,
    session: SessionDep,
    principal: SupervisorDep,
) -> dict[str, str]:
    """Edita nombre, teléfono o correo del contacto. Reservado a supervisión."""
    if conversation.contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta conversación no tiene contacto asociado",
        )
    changes = body.model_dump(exclude_unset=True)
    if "primary_email" in changes and changes["primary_email"]:
        existing = await repo.find_contact_by_email(
            session, tenant_id=conversation.tenant_id, email=changes["primary_email"]
        )
        if existing is not None and existing.id != conversation.contact_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe otro contacto con ese correo",
            )

    await repo.update_contact(session, contact_id=conversation.contact_id, **changes)
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="contact_updated",
        subject_type="contact",
        subject_id=str(conversation.contact_id),
        detail=changes,
    )
    return {"status": "ok"}


@router.post(
    "/conversations/{conversation_id}/contact/comments", status_code=status.HTTP_201_CREATED
)
async def add_contact_comment(
    conversation: ConversationDep,
    body: ContactCommentIn,
    session: SessionDep,
    principal: SupervisorDep,
) -> dict[str, str]:
    """Añade un comentario al historial del contacto. Reservado a supervisión."""
    if conversation.contact_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta conversación no tiene contacto asociado",
        )
    comment = await repo.add_contact_comment(
        session,
        contact_id=conversation.contact_id,
        tenant_id=conversation.tenant_id,
        agent_id=principal.id,
        body=body.body,
    )
    return {"status": "ok", "comment_id": str(comment.id)}


# --------------------------------------------------------------------------- #
# Directorio de contactos
#
# A diferencia de "Datos del contacto" —accesible a cualquier agente, pero
# solo a través de una conversación propia—, este directorio muestra TODOS
# los contactos del inquilino de una sola vez, con su historial completo de
# conversaciones en cualquier canal. Por eso queda reservado a supervisión y
# administración, que de todos modos ya ven la totalidad de la bandeja.
# --------------------------------------------------------------------------- #
@router.get("/contacts", response_model=list[ContactSummaryOut])
async def list_contacts(
    session: SessionDep,
    settings: SettingsDep,
    principal: SupervisorDep,
    search: str | None = None,
    tenant: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ContactSummaryOut]:
    """Directorio completo de clientes, con cuántas conversaciones tiene cada
    uno y cuándo fue la última."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    rows = await repo.list_contacts(
        session, tenant_id=tenant_row.id, search=search, limit=limit, offset=offset
    )
    return [
        ContactSummaryOut(
            id=contact.id,
            display_name=contact.display_name,
            primary_phone=contact.primary_phone,
            primary_email=contact.primary_email,
            is_blocked=contact.is_blocked,
            conversation_count=count,
            last_message_at=last_message_at.isoformat() if last_message_at else None,
        )
        for contact, count, last_message_at in rows
    ]


@router.get("/contacts/{contact_id}", response_model=ContactProfileOut)
async def get_contact_profile(
    contact_id: uuid.UUID, session: SessionDep, principal: SupervisorDep
) -> ContactProfileOut:
    """Ficha completa: datos, comentarios y todas sus conversaciones."""
    contact = await repo.get_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    comments = await repo.list_contact_comments(session, contact_id)
    conversations = await repo.list_conversations_for_contact(session, contact_id)
    names = await _agent_names(session, contact.tenant_id)
    return ContactProfileOut(
        id=contact.id,
        display_name=contact.display_name,
        primary_phone=contact.primary_phone,
        primary_email=contact.primary_email,
        is_blocked=contact.is_blocked,
        comments=[
            ContactCommentOut(
                id=comment.id,
                agent=names.get(comment.agent_id),
                body=comment.body,
                created_at=comment.created_at.isoformat(),
            )
            for comment in comments
        ],
        conversations=[_conversation_out(row) for row in conversations],
    )


@router.patch("/contacts/{contact_id}")
async def update_contact_profile(
    contact_id: uuid.UUID, body: ContactUpdateIn, session: SessionDep, principal: SupervisorDep
) -> dict[str, str]:
    """Edita nombre, teléfono o correo desde el directorio. Reservado a
    supervisión."""
    contact = await repo.get_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    changes = body.model_dump(exclude_unset=True)
    if "primary_email" in changes and changes["primary_email"]:
        existing = await repo.find_contact_by_email(
            session, tenant_id=contact.tenant_id, email=changes["primary_email"]
        )
        if existing is not None and existing.id != contact_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe otro contacto con ese correo",
            )

    await repo.update_contact(session, contact_id=contact_id, **changes)
    await repo.record_audit(
        session,
        tenant_id=contact.tenant_id,
        actor=principal.audit_actor,
        action="contact_updated",
        subject_type="contact",
        subject_id=str(contact_id),
        detail=changes,
    )
    return {"status": "ok"}


@router.post("/contacts/{contact_id}/comments", status_code=status.HTTP_201_CREATED)
async def add_contact_profile_comment(
    contact_id: uuid.UUID, body: ContactCommentIn, session: SessionDep, principal: SupervisorDep
) -> dict[str, str]:
    """Añade un comentario desde el directorio. Reservado a supervisión."""
    contact = await repo.get_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    comment = await repo.add_contact_comment(
        session,
        contact_id=contact_id,
        tenant_id=contact.tenant_id,
        agent_id=principal.id,
        body=body.body,
    )
    return {"status": "ok", "comment_id": str(comment.id)}


# --------------------------------------------------------------------------- #
# Estado del hilo
# --------------------------------------------------------------------------- #
@router.post("/conversations/{conversation_id}/control")
async def set_control(
    conversation: ConversationDep,
    body: ControlIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, str]:
    """Alterna entre atención automática y atención humana."""
    await repo.set_conversation_control(session, conversation.id, body.control)
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="control_changed",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"control": body.control},
    )
    assignee = (
        await repo.get_agent(session, conversation.assignee_id)
        if conversation.assignee_id
        else None
    )
    await _publish_control_change(
        conversation, control=body.control, assignee_name=assignee.label if assignee else None
    )
    return {"status": "ok", "control": body.control}


@router.post("/conversations/{conversation_id}/close")
async def close_conversation(
    conversation: ConversationDep, session: SessionDep, principal: PrincipalDep
) -> dict[str, str]:
    """Cierra el hilo. El historial permanece y se recupera al reabrirse."""
    conversation.status = "closed"
    await repo.record_assignment(
        session,
        conversation=conversation,
        action="close",
        to_agent_id=conversation.assignee_id,
        by_agent_id=principal.id,
    )
    return {"status": "ok", "conversation_status": "closed"}


#: Lo que el equipo marca a mano, y su estado en la base. Los nombres de los
#: dos extremos vienen de antes de que existiera el intermedio.
WORK_STATES: dict[str, str] = {
    "pending": "open",
    "in_progress": "in_progress",
    "solved": "closed",
}


@router.post("/conversations/{conversation_id}/state")
async def set_conversation_state(
    conversation: ConversationDep,
    body: ConversationStateIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, str]:
    """Marca en qué punto está la conversación: pendiente, en curso o resuelta.

    Sustituye al botón único de cerrar: entre «sin tocar» y «terminada» hay un
    estado intermedio que antes no se podía expresar, y sin él una conversación
    que alguien ya estaba resolviendo era indistinguible de otra que nadie
    había mirado.
    """
    target = WORK_STATES[body.state]
    if conversation.status == target:
        return {"status": "ok", "conversation_status": target}

    was_closed = conversation.status == "closed"
    conversation.status = target
    # La traza reutiliza las acciones que ya existían para los dos extremos;
    # el paso a «en proceso» no es una derivación y no se anota como tal.
    if target == "closed":
        action = "close"
    elif was_closed:
        action = "reopen"
    else:
        action = None

    if action:
        await repo.record_assignment(
            session,
            conversation=conversation,
            action=action,
            to_agent_id=conversation.assignee_id,
            by_agent_id=principal.id,
        )
    log.info(
        "conversation_state_set",
        conversation=str(conversation.id),
        state=body.state,
        by=principal.label,
    )
    return {"status": "ok", "conversation_status": target}


# --------------------------------------------------------------------------- #
# Equipo
# --------------------------------------------------------------------------- #
@router.get("/agents", response_model=list[AgentOut])
async def list_agents(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[AgentOut]:
    """El equipo, activo e inactivo. Alimenta el desplegable de derivación y
    la tabla de administración; cada cliente filtra lo que no le sirve —por
    ejemplo, no se deriva a una cuenta desactivada—."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return [
        AgentOut(
            id=agent.id,
            email=agent.email,
            display_name=agent.display_name,
            role=agent.role,
            presence=agent.presence,
            is_active=agent.is_active,
            department_id=agent.department_id,
            extra_department_ids=[d.id for d in agent.granted_departments],
        )
        for agent in await repo.list_agents(session, tenant_id=tenant_row.id, only_active=False)
    ]


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: NewAgentIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Da de alta un compañero. Reservado a supervisión.

    Un agente elige su propia contraseña inicial en el formulario. Un
    supervisor o administrador, en cambio, recibe una generada por el
    sistema: la persona que crea la cuenta no necesita inventar ni comunicar
    una contraseña, y quien se incorpora la recibe por correo de invitación
    junto con el enlace de acceso.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_agent_by_email(session, tenant_id=tenant_row.id, email=body.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe un agente con ese correo"
        )
    if body.department_id is not None:
        department = await repo.get_department(session, body.department_id)
        if department is None or department.tenant_id != tenant_row.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )

    generated_password: str | None = None
    if body.role == ROLE_AGENT:
        if not body.password:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La contraseña es obligatoria para el rol de agente",
            )
        plain_password = body.password
    else:
        generated_password = generate_temporary_password()
        plain_password = generated_password

    try:
        # La derivación de la contraseña es intencionadamente costosa: se ejecuta
        # en un hilo para no bloquear el bucle de eventos.
        password_hash = await asyncio.to_thread(hash_password, plain_password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    agent = await repo.create_agent(
        session,
        tenant_id=tenant_row.id,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
        password_hash=password_hash,
        department_id=body.department_id,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="agent_created",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={"email": agent.email, "role": agent.role},
    )

    email_sent = False
    if generated_password is not None:
        role_label = "administrador" if body.role == ROLE_ADMIN else "supervisor"
        email_sent = await send_email(
            settings=settings,
            to=agent.email,
            subject=f"Su cuenta de {role_label} en {settings.app_name}",
            body=invitation_email_body(
                display_name=agent.display_name,
                role_label=role_label,
                email=agent.email,
                temporary_password=generated_password,
                app_name=settings.app_name,
                base_url=settings.public_base_url,
            ),
        )
        if not email_sent:
            log.warning("invitation_email_not_sent", agent=agent.email, role=agent.role)

    return {
        "status": "ok",
        "agent_id": str(agent.id),
        "email": agent.email,
        # Presente solo para supervisor/admin: quien administra la ve una vez
        # en la consola, por si el correo de invitación no llegó a enviarse.
        "temporary_password": generated_password,
        "invitation_email_sent": email_sent,
    }


@router.post("/agents/{agent_id}/password")
async def set_password(
    agent_id: uuid.UUID, body: PasswordIn, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Establece o restablece una contraseña. Reservado a administración."""
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    try:
        agent.password_hash = await asyncio.to_thread(hash_password, body.password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"status": "ok"}


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdateIn, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Cambia el nombre visible de un agente, o reactiva una cuenta. Reservado
    a administración."""
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nada para actualizar"
        )
    if "display_name" in changes:
        agent.display_name = changes["display_name"].strip()
    if "is_active" in changes:
        agent.is_active = changes["is_active"]

    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_updated",
        subject_type="agent",
        subject_id=str(agent.id),
        detail=changes,
    )
    return {"status": "ok"}


@router.delete("/agents/{agent_id}")
async def deactivate_agent(
    agent_id: uuid.UUID, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Desactiva una cuenta: no puede volver a iniciar sesión.

    No borra la fila: el nombre de la persona debe seguir apareciendo en el
    historial de derivaciones, notas y auditoría de antes de darla de baja.
    Reservado a administración.
    """
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")
    if agent.id == principal.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No puede desactivar su propia cuenta"
        )
    if agent.role == ROLE_ADMIN and agent.is_active:
        remaining = await repo.count_active_admins(session, tenant_id=agent.tenant_id)
        if remaining <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No puede desactivar al único administrador activo",
            )

    agent.is_active = False
    agent.presence = "offline"
    await repo.close_agent_sessions(session, agent.id)
    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_deactivated",
        subject_type="agent",
        subject_id=str(agent.id),
    )
    return {"status": "ok"}


@router.delete("/agents/{agent_id}/permanently")
async def delete_agent_permanently(
    agent_id: uuid.UUID, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Borra la cuenta de verdad. Reservado a administración.

    Frente a desactivar, esto no tiene vuelta atrás y **el historial pierde el
    nombre**: las derivaciones, notas y mensajes de esa persona siguen ahí,
    pero sin autor. Existe para lo que desactivar no resuelve —una cuenta
    creada por error, o alguien que ejerce su derecho a que le borren los
    datos—, no para dar de baja a quien deja el equipo; para eso es
    «Desactivar», que conserva quién hizo qué.

    Las salvaguardas son las mismas que al desactivar: ni la propia cuenta ni
    el último administrador activo, porque cualquiera de las dos dejaría la
    instalación sin quien la administre.
    """
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")
    if agent.id == principal.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No puede eliminar su propia cuenta"
        )
    if agent.role == ROLE_ADMIN and agent.is_active:
        remaining = await repo.count_active_admins(session, tenant_id=agent.tenant_id)
        if remaining <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No puede eliminar al único administrador activo",
            )

    # La auditoría se anota antes de borrar y se queda con el correo y el
    # nombre: es lo único que quedará para saber a quién se dio de baja, ya que
    # la fila desaparece y el resto de tablas pierde la referencia.
    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_deleted",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={"email": agent.email, "display_name": agent.display_name, "role": agent.role},
    )
    await repo.delete_agent(session, agent)
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Departamentos
# --------------------------------------------------------------------------- #
@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[DepartmentOut]:
    """Lista de departamentos. Cualquier persona autenticada la necesita para derivar."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    departments = await repo.list_departments(session, tenant_id=tenant_row.id)
    return [_department_out(row) for row in departments]


@router.post("/departments", status_code=status.HTTP_201_CREATED, response_model=DepartmentOut)
async def create_department(
    body: DepartmentIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> DepartmentOut:
    """Crea un departamento. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_department_by_name(session, tenant_id=tenant_row.id, name=body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe un departamento con ese nombre"
        )
    department = await repo.create_department(session, tenant_id=tenant_row.id, name=body.name)
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="department_created",
        subject_type="department",
        subject_id=str(department.id),
        detail={"name": department.name},
    )
    return _department_out(department)


@router.put("/departments/{department_id}/business-hours", response_model=DepartmentOut)
async def set_business_hours(
    department_id: uuid.UUID,
    body: BusinessHoursIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> DepartmentOut:
    """Fija el horario de atención de un departamento. Reservado a administración.

    Fuera de ese horario el asistente deja de responder y, si hay texto de
    aviso, el cliente lo recibe una sola vez. Un horario vacío vuelve a la
    atención permanente.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )
    _require_known_timezone(body.timezone)

    department.business_hours = body.business_hours
    department.timezone = body.timezone
    department.out_of_hours_message = body.out_of_hours_message
    department.first_response_target_minutes = body.first_response_target_minutes
    await session.flush()
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="department_business_hours_updated",
        subject_type="department",
        subject_id=str(department.id),
        detail={"timezone": body.timezone, "days": sorted(body.business_hours)},
    )
    return _department_out(department)


# --------------------------------------------------------------------------- #
# Módulo Hotel: habitaciones y reservas, acotado al departamento que lo activa
# --------------------------------------------------------------------------- #
async def _load_hotel_department(
    session: SessionDep, settings: SettingsDep, *, department_id: uuid.UUID, tenant: str | None
) -> Department:
    """Departamento del inquilino activo, sin comprobar acceso ni módulo.

    Es el paso común a los tres puntos de entrada del módulo: los dos que
    configuran su activación (que deben funcionar aunque el módulo todavía no
    esté encendido) y ``_require_hotel_department``, que suma las dos
    comprobaciones que sí dependen de para qué se use.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_row.id or not department.is_active:
        # Mismo criterio que ``_transfer_to_department``: un departamento
        # inactivo no admite operaciones nuevas, sean conversaciones u hotel.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El departamento no existe o está inactivo",
        )
    return department


async def _require_hotel_department(
    session: SessionDep,
    settings: SettingsDep,
    principal: Principal,
    *,
    department_id: uuid.UUID,
    tenant: str | None,
) -> Department:
    """Departamento válido, accesible para quien pregunta y con el módulo activo.

    Un departamento inexistente y uno sin acceso responden igual —404—, para no
    filtrar por el código de error qué departamentos existen en el inquilino.
    """
    department = await _load_hotel_department(
        session, settings, department_id=department_id, tenant=tenant
    )
    allowed = principal.department_ids
    if allowed is not None and department.id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )
    if not repo.hotel_module_enabled(department):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El módulo de hotel no está activo para este departamento",
        )
    return department


@router.get("/departments/{department_id}/hotel/module", response_model=HotelModuleOut)
async def get_hotel_module(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelModuleOut:
    """Si el módulo de hotel está activo para este departamento. Reservado a administración."""
    department = await _load_hotel_department(
        session, settings, department_id=department_id, tenant=tenant
    )
    return HotelModuleOut(enabled=repo.hotel_module_enabled(department))


@router.put("/departments/{department_id}/hotel/module", response_model=HotelModuleOut)
async def set_hotel_module(
    department_id: uuid.UUID,
    body: HotelModuleIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelModuleOut:
    """Activa o desactiva el módulo de hotel de un departamento. Reservado a administración.

    El resto de ramas de negocio del inquilino no se ven afectadas: cada
    departamento activa el módulo por su cuenta.
    """
    department = await _load_hotel_department(
        session, settings, department_id=department_id, tenant=tenant
    )
    await repo.set_hotel_module_enabled(session, department=department, enabled=body.enabled)
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_module_toggled",
        subject_type="department",
        subject_id=str(department.id),
        detail={"enabled": body.enabled},
    )
    return HotelModuleOut(enabled=body.enabled)


def _hotel_room_type_out(row: HotelRoomType) -> HotelRoomTypeOut:
    return HotelRoomTypeOut(
        id=row.id,
        name=row.name,
        description=row.description,
        capacity=row.capacity,
        is_active=row.is_active,
    )


@router.get(
    "/departments/{department_id}/hotel/room-types", response_model=list[HotelRoomTypeOut]
)
async def list_hotel_room_types(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> list[HotelRoomTypeOut]:
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rows = await repo.list_hotel_room_types(session, department_id=department.id)
    return [_hotel_room_type_out(row) for row in rows]


@router.post(
    "/departments/{department_id}/hotel/room-types",
    status_code=status.HTTP_201_CREATED,
    response_model=HotelRoomTypeOut,
)
async def create_hotel_room_type(
    department_id: uuid.UUID,
    body: HotelRoomTypeIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRoomTypeOut:
    """Crea una categoría de habitación (Individual, Doble, Suite…). Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    if await repo.find_hotel_room_type_by_name(
        session, department_id=department.id, name=body.name
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una categoría con ese nombre"
        )
    room_type = await repo.create_hotel_room_type(
        session,
        tenant_id=department.tenant_id,
        department_id=department.id,
        name=body.name,
        description=body.description,
        capacity=body.capacity,
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_room_type_created",
        subject_type="hotel_room_type",
        subject_id=str(room_type.id),
        detail={"name": room_type.name},
    )
    return _hotel_room_type_out(room_type)


@router.patch(
    "/departments/{department_id}/hotel/room-types/{room_type_id}",
    response_model=HotelRoomTypeOut,
)
async def update_hotel_room_type(
    department_id: uuid.UUID,
    room_type_id: uuid.UUID,
    body: HotelRoomTypePatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRoomTypeOut:
    """Edita una categoría de habitación. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room_type = await repo.get_hotel_room_type(session, room_type_id)
    if room_type is None or room_type.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La categoría no existe")
    if body.description is not None:
        room_type.description = body.description
    if body.capacity is not None:
        room_type.capacity = body.capacity
    if body.is_active is not None:
        room_type.is_active = body.is_active
    await session.flush()
    return _hotel_room_type_out(room_type)


def _hotel_room_out(row: HotelRoom) -> HotelRoomOut:
    return HotelRoomOut(
        id=row.id,
        room_type_id=row.room_type_id,
        room_type_name=row.room_type.name,
        code=row.code,
        status=row.status,
        notes=row.notes,
    )


@router.get("/departments/{department_id}/hotel/rooms", response_model=list[HotelRoomOut])
async def list_hotel_rooms(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
    room_type_id: uuid.UUID | None = None,
) -> list[HotelRoomOut]:
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rows = await repo.list_hotel_rooms(
        session, department_id=department.id, room_type_id=room_type_id
    )
    return [_hotel_room_out(row) for row in rows]


@router.post(
    "/departments/{department_id}/hotel/rooms",
    status_code=status.HTTP_201_CREATED,
    response_model=HotelRoomOut,
)
async def create_hotel_room(
    department_id: uuid.UUID,
    body: HotelRoomIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRoomOut:
    """Da de alta una habitación física. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room_type = await repo.get_hotel_room_type(session, body.room_type_id)
    if room_type is None or room_type.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La categoría no existe")
    if not room_type.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La categoría está retirada; no admite habitaciones nuevas",
        )
    if await repo.find_hotel_room_by_code(session, department_id=department.id, code=body.code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una habitación con ese número"
        )
    room = await repo.create_hotel_room(
        session,
        tenant_id=department.tenant_id,
        department_id=department.id,
        room_type_id=room_type.id,
        code=body.code,
        notes=body.notes,
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_room_created",
        subject_type="hotel_room",
        subject_id=str(room.id),
        detail={"code": room.code},
    )
    # Se asigna en Python y no se vuelve a consultar: en una sesión async una
    # carga perezosa de la relación fallaría fuera del ``await`` que la trajo.
    room.room_type = room_type
    return _hotel_room_out(room)


@router.patch("/departments/{department_id}/hotel/rooms/{room_id}", response_model=HotelRoomOut)
async def update_hotel_room(
    department_id: uuid.UUID,
    room_id: uuid.UUID,
    body: HotelRoomPatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRoomOut:
    """Cambia el estado o las notas de una habitación. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room = await repo.get_hotel_room(session, room_id)
    if room is None or room.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La habitación no existe")
    if body.status is not None:
        room.status = body.status
    if body.notes is not None:
        room.notes = body.notes
    await session.flush()
    return _hotel_room_out(room)


def _hotel_rate_plan_out(row: HotelRatePlan) -> HotelRatePlanOut:
    return HotelRatePlanOut(
        id=row.id,
        room_type_id=row.room_type_id,
        name=row.name,
        starts_on=row.starts_on.isoformat() if row.starts_on else None,
        ends_on=row.ends_on.isoformat() if row.ends_on else None,
        nightly_price_cents=row.nightly_price_cents,
        currency=row.currency,
    )


@router.get(
    "/departments/{department_id}/hotel/rate-plans", response_model=list[HotelRatePlanOut]
)
async def list_hotel_rate_plans(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
    room_type_id: uuid.UUID | None = None,
) -> list[HotelRatePlanOut]:
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rows = await repo.list_hotel_rate_plans(
        session, department_id=department.id, room_type_id=room_type_id
    )
    return [_hotel_rate_plan_out(row) for row in rows]


@router.post(
    "/departments/{department_id}/hotel/rate-plans",
    status_code=status.HTTP_201_CREATED,
    response_model=HotelRatePlanOut,
)
async def create_hotel_rate_plan(
    department_id: uuid.UUID,
    body: HotelRatePlanIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRatePlanOut:
    """Crea una tarifa por noche para una categoría. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room_type = await repo.get_hotel_room_type(session, body.room_type_id)
    if room_type is None or room_type.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La categoría no existe")
    if (
        body.starts_on is not None
        and body.ends_on is not None
        and body.ends_on <= body.starts_on
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha de fin debe ser posterior a la de inicio",
        )
    rate_plan = await repo.create_hotel_rate_plan(
        session,
        tenant_id=department.tenant_id,
        department_id=department.id,
        room_type_id=room_type.id,
        name=body.name,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        nightly_price_cents=body.nightly_price_cents,
        currency=body.currency,
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_rate_plan_created",
        subject_type="hotel_rate_plan",
        subject_id=str(rate_plan.id),
        detail={"name": rate_plan.name, "room_type": room_type.name},
    )
    return _hotel_rate_plan_out(rate_plan)


@router.delete(
    "/departments/{department_id}/hotel/rate-plans/{rate_plan_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_hotel_rate_plan(
    department_id: uuid.UUID,
    rate_plan_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> None:
    """Borra una tarifa. Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rate_plan = await repo.get_hotel_rate_plan(session, rate_plan_id)
    if rate_plan is None or rate_plan.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La tarifa no existe")
    await repo.delete_hotel_rate_plan(session, rate_plan)
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_rate_plan_deleted",
        subject_type="hotel_rate_plan",
        subject_id=str(rate_plan.id),
        detail={"name": rate_plan.name},
    )


@router.patch(
    "/departments/{department_id}/hotel/rate-plans/{rate_plan_id}",
    response_model=HotelRatePlanOut,
)
async def update_hotel_rate_plan(
    department_id: uuid.UUID,
    rate_plan_id: uuid.UUID,
    body: HotelRatePlanPatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> HotelRatePlanOut:
    """Edita una tarifa ya cargada —por ejemplo, para corregir el precio.
    Reservado a administración."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rate_plan = await repo.get_hotel_rate_plan(session, rate_plan_id)
    if rate_plan is None or rate_plan.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La tarifa no existe")

    starts_on = body.starts_on if body.starts_on is not None else rate_plan.starts_on
    ends_on = body.ends_on if body.ends_on is not None else rate_plan.ends_on
    if starts_on is not None and ends_on is not None and ends_on <= starts_on:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha de fin debe ser posterior a la de inicio",
        )

    await repo.update_hotel_rate_plan(
        session,
        rate_plan=rate_plan,
        name=body.name,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        nightly_price_cents=body.nightly_price_cents,
        currency=body.currency,
    )
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_rate_plan_updated",
        subject_type="hotel_rate_plan",
        subject_id=str(rate_plan.id),
        detail={"name": rate_plan.name},
    )
    return _hotel_rate_plan_out(rate_plan)


def _hotel_reservation_out(row: HotelReservation) -> HotelReservationOut:
    return HotelReservationOut(
        id=row.id,
        room_id=row.room_id,
        room_code=row.room.code,
        room_type_name=row.room.room_type.name,
        guest_name=row.guest_name,
        guest_phone=row.guest_phone,
        guest_email=row.guest_email,
        check_in=row.check_in.isoformat(),
        check_out=row.check_out.isoformat(),
        guests=row.guests,
        status=row.status,
        nightly_price_cents=row.nightly_price_cents,
        currency=row.currency,
        notes=row.notes,
        contact_id=row.contact_id,
        conversation_id=row.conversation_id,
    )


@router.get(
    "/departments/{department_id}/hotel/availability", response_model=list[HotelRoomOut]
)
async def hotel_availability(
    department_id: uuid.UUID,
    check_in: date,
    check_out: date,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
    room_type_id: uuid.UUID | None = None,
    #: Al editar una reserva, para que su propia habitación no quede excluida
    #: por chocar contra sí misma en sus propias fechas.
    exclude_reservation_id: uuid.UUID | None = None,
) -> list[HotelRoomOut]:
    """Habitaciones libres —sin ninguna reserva activa que se solape— para un rango de fechas."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    if check_out <= check_in:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La salida debe ser posterior a la entrada",
        )
    rows = await repo.list_available_hotel_rooms(
        session,
        department_id=department.id,
        check_in=check_in,
        check_out=check_out,
        room_type_id=room_type_id,
        exclude_reservation_id=exclude_reservation_id,
    )
    return [_hotel_room_out(row) for row in rows]


@router.get(
    "/departments/{department_id}/hotel/reservations",
    response_model=list[HotelReservationOut],
)
async def list_hotel_reservations(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[HotelReservationOut]:
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    rows = await repo.list_hotel_reservations(
        session,
        department_id=department.id,
        status=status_filter,
        from_date=from_date,
        to_date=to_date,
    )
    return [_hotel_reservation_out(row) for row in rows]


@router.post(
    "/departments/{department_id}/hotel/reservations",
    status_code=status.HTTP_201_CREATED,
    response_model=HotelReservationOut,
)
async def create_hotel_reservation(
    department_id: uuid.UUID,
    body: HotelReservationIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> HotelReservationOut:
    """Crea una reserva. Cualquier persona con acceso al departamento puede cargarla."""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    room = await repo.get_hotel_room(session, body.room_id)
    if room is None or room.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La habitación no existe")
    if not room.room_type.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La categoría de esta habitación está retirada",
        )
    if room.status != "available":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La habitación no está disponible (en mantenimiento o fuera de servicio)",
        )
    if await repo.hotel_room_has_overlap(
        session, room_id=room.id, check_in=body.check_in, check_out=body.check_out
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La habitación ya está reservada en esas fechas",
        )
    if body.contact_id is not None:
        contact = await repo.get_contact(session, body.contact_id)
        if contact is None or contact.tenant_id != department.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El contacto no existe"
            )

    price = body.nightly_price_cents
    currency = body.currency
    if price is None:
        rate_plan = await repo.rate_plan_for_stay(
            session, room_type_id=room.room_type_id, check_in=body.check_in
        )
        if rate_plan is not None:
            price = rate_plan.nightly_price_cents
            currency = rate_plan.currency

    reservation = await repo.create_hotel_reservation(
        session,
        tenant_id=department.tenant_id,
        department_id=department.id,
        room_id=room.id,
        guest_name=body.guest_name,
        guest_phone=body.guest_phone,
        guest_email=body.guest_email,
        contact_id=body.contact_id,
        check_in=body.check_in,
        check_out=body.check_out,
        guests=body.guests,
        created_by_agent_id=principal.id,
        nightly_price_cents=price,
        currency=currency,
        notes=body.notes,
    )
    # Ya se tenía cargada con su categoría; se evita una carga perezosa, que en
    # una sesión async fallaría al serializar la respuesta.
    reservation.room = room
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_reservation_created",
        subject_type="hotel_reservation",
        subject_id=str(reservation.id),
        detail={
            "room": room.code,
            "check_in": body.check_in.isoformat(),
            "check_out": body.check_out.isoformat(),
        },
    )
    return _hotel_reservation_out(reservation)


@router.patch(
    "/departments/{department_id}/hotel/reservations/{reservation_id}",
    response_model=HotelReservationOut,
)
async def update_hotel_reservation(
    department_id: uuid.UUID,
    reservation_id: uuid.UUID,
    body: HotelReservationPatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> HotelReservationOut:
    """Corrige fechas, habitación o datos del huésped de una reserva que
    todavía no llegó. Cualquier persona con acceso al departamento puede
    hacerlo, igual que crearla.
    """
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    reservation = await repo.get_hotel_reservation(session, reservation_id)
    if reservation is None or reservation.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La reserva no existe")
    if reservation.status not in {"pending", "confirmed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Solo se puede editar una reserva pendiente o confirmada",
        )

    check_in = body.check_in if body.check_in is not None else reservation.check_in
    check_out = body.check_out if body.check_out is not None else reservation.check_out
    if check_out <= check_in:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La salida debe ser posterior a la entrada",
        )

    room = reservation.room
    if body.room_id is not None and body.room_id != reservation.room_id:
        room = await repo.get_hotel_room(session, body.room_id)
        if room is None or room.department_id != department.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="La habitación no existe"
            )
        if not room.room_type.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La categoría de esta habitación está retirada",
            )
        if room.status != "available":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La habitación no está disponible (en mantenimiento o fuera de servicio)",
            )

    if await repo.hotel_room_has_overlap(
        session,
        room_id=room.id,
        check_in=check_in,
        check_out=check_out,
        exclude_reservation_id=reservation.id,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La habitación ya está reservada en esas fechas",
        )
    if body.contact_id is not None:
        contact = await repo.get_contact(session, body.contact_id)
        if contact is None or contact.tenant_id != department.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El contacto no existe"
            )

    await repo.update_hotel_reservation(
        session,
        reservation=reservation,
        room_id=room.id,
        check_in=check_in,
        check_out=check_out,
        guest_name=body.guest_name,
        guest_phone=body.guest_phone,
        guest_email=body.guest_email,
        contact_id=body.contact_id,
        guests=body.guests,
        nightly_price_cents=body.nightly_price_cents,
        currency=body.currency,
        notes=body.notes,
    )
    # Por si cambió de habitación: se evita una carga perezosa, que en una
    # sesión async fallaría al serializar la respuesta.
    reservation.room = room
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_reservation_updated",
        subject_type="hotel_reservation",
        subject_id=str(reservation.id),
        detail={
            "room": room.code,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
        },
    )
    return _hotel_reservation_out(reservation)


#: A qué estados puede pasar cada estado de una reserva. Un intento de salto no
#: contemplado —de ``pending`` a ``checked_out`` sin pasar por ``confirmed``,
#: por ejemplo— recibe un error claro en vez de dejar un dato inconsistente.
HOTEL_RESERVATION_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "cancelled"},
    "confirmed": {"checked_in", "cancelled", "no_show"},
    "checked_in": {"checked_out"},
    "checked_out": set(),
    "cancelled": set(),
    "no_show": set(),
}


@router.put(
    "/departments/{department_id}/hotel/reservations/{reservation_id}/status",
    response_model=HotelReservationOut,
)
async def set_hotel_reservation_status(
    department_id: uuid.UUID,
    reservation_id: uuid.UUID,
    body: HotelReservationStatusIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> HotelReservationOut:
    """Cambia el estado de una reserva: confirmar, check-in, check-out, cancelar…"""
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    reservation = await repo.get_hotel_reservation(session, reservation_id)
    if reservation is None or reservation.department_id != department.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="La reserva no existe")
    previous_status = reservation.status
    allowed = HOTEL_RESERVATION_TRANSITIONS.get(previous_status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede pasar de «{previous_status}» a «{body.status}»",
        )
    await repo.set_hotel_reservation_status(session, reservation=reservation, status=body.status)
    await repo.record_audit(
        session,
        tenant_id=department.tenant_id,
        actor=principal.audit_actor,
        action="hotel_reservation_status_changed",
        subject_type="hotel_reservation",
        subject_id=str(reservation.id),
        detail={"from": previous_status, "to": body.status},
    )
    return _hotel_reservation_out(reservation)


@router.get("/departments/{department_id}/hotel/contacts", response_model=list[HotelContactOut])
async def search_hotel_contacts(
    department_id: uuid.UUID,
    q: str,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> list[HotelContactOut]:
    """Busca un contacto ya conocido por nombre, teléfono o correo, para
    vincularlo a una reserva sin volver a escribir sus datos a mano.
    """
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    if not q.strip():
        return []
    contacts = await repo.search_contacts(session, tenant_id=department.tenant_id, search=q)
    return [
        HotelContactOut(
            id=c.id,
            display_name=c.display_name,
            primary_phone=c.primary_phone,
            primary_email=c.primary_email,
        )
        for c in contacts
    ]


@router.get("/departments/{department_id}/hotel/report", response_model=HotelReportOut)
async def hotel_report(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> HotelReportOut:
    """Resumen operativo: llegadas y salidas de hoy, ocupación e ingresos de
    los próximos 30 días, en el huso horario del departamento.
    """
    department = await _require_hotel_department(
        session, settings, principal, department_id=department_id, tenant=tenant
    )
    today = utcnow().astimezone(resolve_timezone(department.timezone)).date()
    report = await repo.hotel_department_report(session, department_id=department.id, today=today)
    return HotelReportOut(
        reference_date=report["reference_date"].isoformat(),
        arrivals_today=report["arrivals_today"],
        departures_today=report["departures_today"],
        occupied_rooms=report["occupied_rooms"],
        total_rooms=report["total_rooms"],
        pending_count=report["pending_count"],
        revenue_next_30_days=[HotelRevenueOut(**row) for row in report["revenue_by_currency"]],
    )


# --------------------------------------------------------------------------- #
# Respuestas guardadas
# --------------------------------------------------------------------------- #
def _canned_response_out(row: CannedResponse) -> CannedResponseOut:
    return CannedResponseOut(id=row.id, shortcode=row.shortcode, title=row.title, body=row.body)


@router.get("/canned-responses", response_model=list[CannedResponseOut])
async def list_canned_responses(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[CannedResponseOut]:
    """Lista de respuestas guardadas. Cualquier agente las usa al responder."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    canned_responses = await repo.list_canned_responses(session, tenant_id=tenant_row.id)
    return [_canned_response_out(row) for row in canned_responses]


@router.post(
    "/canned-responses", status_code=status.HTTP_201_CREATED, response_model=CannedResponseOut
)
async def create_canned_response(
    body: CannedResponseIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> CannedResponseOut:
    """Crea una respuesta guardada. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_canned_response_by_shortcode(
        session, tenant_id=tenant_row.id, shortcode=body.shortcode
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una respuesta con ese atajo"
        )
    canned_response = await repo.create_canned_response(
        session, tenant_id=tenant_row.id, shortcode=body.shortcode, title=body.title, body=body.body
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="canned_response_created",
        subject_type="canned_response",
        subject_id=str(canned_response.id),
        detail={"shortcode": canned_response.shortcode},
    )
    return _canned_response_out(canned_response)


@router.patch("/canned-responses/{canned_response_id}", response_model=CannedResponseOut)
async def update_canned_response(
    canned_response_id: uuid.UUID,
    body: CannedResponsePatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> CannedResponseOut:
    """Edita una respuesta guardada. Solo se aplican los campos recibidos."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    canned_response = await repo.get_canned_response(session, canned_response_id)
    if canned_response is None or canned_response.tenant_id != tenant_row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Respuesta guardada no encontrada"
        )
    if body.shortcode is not None and body.shortcode != canned_response.shortcode:
        existing = await repo.find_canned_response_by_shortcode(
            session, tenant_id=canned_response.tenant_id, shortcode=body.shortcode
        )
        if existing is not None and existing.id != canned_response.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe una respuesta con ese atajo",
            )
        canned_response.shortcode = body.shortcode
    if body.title is not None:
        canned_response.title = body.title
    if body.body is not None:
        canned_response.body = body.body
    await session.flush()
    return _canned_response_out(canned_response)


@router.delete("/canned-responses/{canned_response_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canned_response(
    canned_response_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> None:
    """Borra una respuesta guardada. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    canned_response = await repo.get_canned_response(session, canned_response_id)
    if canned_response is None or canned_response.tenant_id != tenant_row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Respuesta guardada no encontrada"
        )
    await repo.delete_canned_response(session, canned_response)
    await repo.record_audit(
        session,
        tenant_id=canned_response.tenant_id,
        actor=principal.audit_actor,
        action="canned_response_deleted",
        subject_type="canned_response",
        subject_id=str(canned_response.id),
        detail={"shortcode": canned_response.shortcode},
    )


# --------------------------------------------------------------------------- #
# Etiquetas
# --------------------------------------------------------------------------- #
@router.get("/labels", response_model=list[LabelOut])
async def list_labels(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[LabelOut]:
    """Lista de etiquetas del inquilino."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    labels = await repo.list_labels(session, tenant_id=tenant_row.id)
    return [LabelOut(id=label.id, name=label.name, color=label.color) for label in labels]


@router.post("/labels", status_code=status.HTTP_201_CREATED, response_model=LabelOut)
async def create_label(
    body: LabelIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> LabelOut:
    """Crea una etiqueta. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_label_by_name(session, tenant_id=tenant_row.id, name=body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una etiqueta con ese nombre"
        )
    label = await repo.create_label(
        session, tenant_id=tenant_row.id, name=body.name, color=body.color
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="label_created",
        subject_type="label",
        subject_id=str(label.id),
        detail={"name": label.name},
    )
    return LabelOut(id=label.id, name=label.name, color=label.color)


@router.delete("/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_label(
    label_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> None:
    """Borra una etiqueta. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    label = await repo.get_label(session, label_id)
    if label is None or label.tenant_id != tenant_row.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etiqueta no encontrada")
    await repo.delete_label(session, label)
    await repo.record_audit(
        session,
        tenant_id=label.tenant_id,
        actor=principal.audit_actor,
        action="label_deleted",
        subject_type="label",
        subject_id=str(label.id),
        detail={"name": label.name},
    )


@router.put("/conversations/{conversation_id}/labels", response_model=list[LabelOut])
async def set_conversation_labels(
    conversation: ConversationDep,
    body: ConversationLabelsIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> list[LabelOut]:
    """Fija las etiquetas de una conversación. Cualquiera con acceso a ella puede hacerlo."""
    for label_id in body.label_ids:
        label = await repo.get_label(session, label_id)
        if label is None or label.tenant_id != conversation.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Una de las etiquetas indicadas no existe",
            )
    await repo.set_conversation_labels(session, conversation=conversation, label_ids=body.label_ids)
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="conversation_labels_updated",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"label_ids": [str(i) for i in body.label_ids]},
    )
    return [
        LabelOut(id=label.id, name=label.name, color=label.color)
        for label in conversation.labels
    ]


# --------------------------------------------------------------------------- #
# Macros
# --------------------------------------------------------------------------- #
def _macro_out(row: Any) -> MacroOut:
    return MacroOut(id=row.id, name=row.name, steps=[MacroStep(**s) for s in row.steps or []])


@router.get("/macros", response_model=list[MacroOut])
async def list_macros(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[MacroOut]:
    """Macros del inquilino. Cualquier agente las ejecuta sobre sus hilos."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return [_macro_out(row) for row in await repo.list_macros(session, tenant_id=tenant_row.id)]


@router.post("/macros", status_code=status.HTTP_201_CREATED, response_model=MacroOut)
async def create_macro(
    body: MacroIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> MacroOut:
    """Crea una macro. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_macro_by_name(session, tenant_id=tenant_row.id, name=body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una macro con ese nombre"
        )
    # Lo que la macro apunta debe existir ahora: guardar una macro rota sería
    # descubrirlo recién el día que alguien la use.
    for step in body.steps:
        await _resolve_macro_step(session, step, tenant_id=tenant_row.id)

    macro = await repo.create_macro(
        session,
        tenant_id=tenant_row.id,
        name=body.name,
        steps=[s.model_dump(mode="json", exclude_none=True) for s in body.steps],
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="macro_created",
        subject_type="macro",
        subject_id=str(macro.id),
        detail={"name": macro.name, "pasos": len(body.steps)},
    )
    return _macro_out(macro)


@router.delete("/macros/{macro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_macro(
    macro_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> None:
    """Borra una macro. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    macro = await repo.get_macro(session, macro_id)
    if macro is None or macro.tenant_id != tenant_row.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Macro no encontrada")
    await repo.delete_macro(session, macro)
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="macro_deleted",
        subject_type="macro",
        subject_id=str(macro.id),
        detail={"name": macro.name},
    )


async def _resolve_macro_step(
    session: SessionDep, step: MacroStep, *, tenant_id: uuid.UUID
) -> Any:
    """Comprueba que lo que el paso apunta existe y es de este inquilino."""
    if step.action == "label":
        label = await repo.get_label(session, step.label_id)
        if label is None or label.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="La etiqueta del paso no existe"
            )
        return label
    if step.action == "transfer_department":
        department = await repo.get_department(session, step.department_id)
        if department is None or department.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento del paso no existe"
            )
        return department
    if step.action == "reply":
        canned = await repo.get_canned_response(session, step.canned_response_id)
        if canned is None or canned.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="La respuesta guardada del paso no existe",
            )
        return canned
    return None


@router.post("/conversations/{conversation_id}/macros/{macro_id}", status_code=status.HTTP_200_OK)
async def run_macro(
    macro_id: uuid.UUID,
    conversation: ConversationDep,
    session: SessionDep,
    principal: PrincipalDep,
    orchestrator: OrchestratorDep,
) -> dict[str, Any]:
    """Ejecuta la macro sobre la conversación, paso a paso y en orden.

    Todo ocurre en la misma transacción de la petición: si un paso falla, no
    queda la conversación a medio procesar —etiquetada y derivada, pero sin la
    respuesta que la macro prometía—.
    """
    macro = await repo.get_macro(session, macro_id)
    if macro is None or macro.tenant_id != conversation.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Macro no encontrada")

    aplicados: list[str] = []
    for raw in macro.steps or []:
        step = MacroStep(**raw)
        target = await _resolve_macro_step(session, step, tenant_id=conversation.tenant_id)

        if step.action == "label":
            # ``authorized_conversation`` trae la fila sin las etiquetas, y
            # leerlas sin más dispararía una carga diferida en pleno async.
            await session.refresh(conversation, attribute_names=["labels"])
            actuales = [row.id for row in conversation.labels]
            # Repetir la macro no debe duplicar lo que ya está puesto.
            if target.id not in actuales:
                await repo.set_conversation_labels(
                    session, conversation=conversation, label_ids=[*actuales, target.id]
                )
        elif step.action == "note":
            await repo.add_internal_note(
                session, conversation=conversation, agent_id=principal.id, body=step.body
            )
        elif step.action == "reply":
            await orchestrator.send_from_agent(
                conversation_id=conversation.id,
                outbound=OutboundMessage(text=target.body, content_type=ContentType.TEXT),
                agent_id=principal.id,
                session=session,
            )
            await repo.mark_first_human_response(session, conversation=conversation)
        elif step.action == "transfer_department":
            await _transfer_to_department(
                conversation,
                TransferIn(to_department_id=target.id, note=step.body),
                session,
                principal,
            )
        elif step.action == "close":
            conversation.status = "closed"
            await repo.record_assignment(
                session,
                conversation=conversation,
                action="close",
                to_agent_id=conversation.assignee_id,
                by_agent_id=principal.id,
            )
        aplicados.append(step.action)

    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="macro_run",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"macro": macro.name, "pasos": aplicados},
    )
    log.info("macro_ejecutada", macro=macro.name, conversation=str(conversation.id))
    return {"status": "ok", "applied": aplicados}


# --------------------------------------------------------------------------- #
# Vistas guardadas
# --------------------------------------------------------------------------- #
def _saved_view_out(row: Any) -> SavedViewOut:
    return SavedViewOut(
        id=row.id,
        name=row.name,
        filters=dict(row.filters or {}),
        shared=row.owner_agent_id is None,
    )


@router.get("/saved-views", response_model=list[SavedViewOut])
async def list_saved_views(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[SavedViewOut]:
    """Las vistas del equipo más las propias de quien pregunta."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    views = await repo.list_saved_views(session, tenant_id=tenant_row.id, agent_id=principal.id)
    return [_saved_view_out(row) for row in views]


@router.post("/saved-views", status_code=status.HTTP_201_CREATED, response_model=SavedViewOut)
async def create_saved_view(
    body: SavedViewIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> SavedViewOut:
    """Guarda una vista. Las del equipo quedan reservadas a supervisión."""
    if body.shared and not principal.is_supervisor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compartir una vista con el equipo requiere permisos de supervisión",
        )
    if not body.shared and principal.id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La clave de servicio solo puede crear vistas del equipo",
        )

    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    owner_agent_id = None if body.shared else principal.id
    if await repo.find_saved_view_by_name(
        session, tenant_id=tenant_row.id, owner_agent_id=owner_agent_id, name=body.name
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una vista con ese nombre"
        )

    # Se guarda solo lo que trae valor: una vista no arrastra claves vacías.
    filters = body.filters.model_dump(exclude_none=True, mode="json")
    view = await repo.create_saved_view(
        session,
        tenant_id=tenant_row.id,
        owner_agent_id=owner_agent_id,
        name=body.name,
        filters=filters,
    )
    return _saved_view_out(view)


@router.delete("/saved-views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_view(
    view_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> None:
    """Borra una vista propia; las del equipo, solo supervisión."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    view = await repo.get_saved_view(session, view_id)
    # 404 y no 403 cuando la vista es de otra persona: su existencia tampoco
    # es asunto de quien pregunta.
    if (
        view is None
        or view.tenant_id != tenant_row.id
        or (view.owner_agent_id is not None and view.owner_agent_id != principal.id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vista no encontrada")
    if view.owner_agent_id is None and not principal.is_supervisor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Borrar una vista del equipo requiere permisos de supervisión",
        )
    await repo.delete_saved_view(session, view)


@router.put("/agents/{agent_id}/departments")
async def set_agent_departments(
    agent_id: uuid.UUID, body: AgentDepartmentsIn, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    """Fija el departamento principal y los adicionales de un agente.

    Reservado a administración: es quien "está por encima de todo" y decide
    qué colas puede atender cada persona, más allá de la suya.
    """
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    for department_id in filter(None, [body.department_id, *body.extra_department_ids]):
        department = await repo.get_department(session, department_id)
        if department is None or department.tenant_id != agent.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Uno de los departamentos indicados no existe",
            )

    agent.department_id = body.department_id
    await repo.set_agent_departments(
        session, agent=agent, department_ids=body.extra_department_ids
    )
    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_departments_updated",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={
            "department_id": str(body.department_id) if body.department_id else None,
            "extra_department_ids": [str(d) for d in body.extra_department_ids],
        },
    )
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Cuentas de canal
#
# Da de alta, con departamento propio, tantos números de WhatsApp, páginas de
# Facebook o equipos de Teams como se quiera — sin esto, todos comparten la
# cola común, exactamente como antes de que existiera esta pantalla.
# --------------------------------------------------------------------------- #
class ChannelAccountOut(BaseModel):
    id: uuid.UUID
    channel: str
    external_id: str
    display_name: str | None
    is_active: bool
    department_id: uuid.UUID | None
    department_name: str | None
    #: Nunca se expone el token en sí, ni cifrado ni en claro.
    has_own_credentials: bool


#: Las tres credenciales que una cuenta de Meta puede tener propias. Se guardan
#: juntas y cifradas en ``ChannelAccount.credentials_ciphertext``; cualquiera
#: que falte se resuelve con el valor global de ``.env``, de modo que una
#: instalación con un solo número sigue funcionando sin tocar la consola.
CHANNEL_CREDENTIAL_FIELDS = ("access_token", "verify_token", "app_secret")


class ChannelAccountIn(BaseModel):
    channel: Literal["whatsapp", "facebook", "msbot"]
    external_id: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=160)
    department_id: uuid.UUID | None = None
    #: Obligatorio en Facebook (cada página tiene el suyo); opcional en
    #: WhatsApp (si falta, se usa el token global de .env); ignorado en Teams.
    access_token: str | None = Field(default=None, max_length=4096)
    #: El que se escribe en el panel de Meta al dar de alta el webhook. Propio
    #: por cuenta cuando cada número vive en una aplicación de Meta distinta.
    verify_token: str | None = Field(default=None, max_length=512)
    #: Clave con la que Meta firma el cuerpo del webhook. Ídem: una por
    #: aplicación de Meta, no una por instalación.
    app_secret: str | None = Field(default=None, max_length=512)


class ChannelAccountUpdateIn(BaseModel):
    #: Solo se aplican los campos recibidos; ausente significa "no tocar".
    display_name: str | None = Field(default=None, max_length=160)
    department_id: uuid.UUID | None = None
    is_active: bool | None = None
    #: Cadena vacía = quitar ese valor propio y volver al global de ``.env``;
    #: ausente = no tocarlo. Los tres se editan por separado: cambiar el token
    #: de acceso no debe obligar a volver a escribir el secreto de la app.
    access_token: str | None = Field(default=None, max_length=4096)
    verify_token: str | None = Field(default=None, max_length=512)
    app_secret: str | None = Field(default=None, max_length=512)


def _channel_account_out(account: ChannelAccount) -> ChannelAccountOut:
    return ChannelAccountOut(
        id=account.id,
        channel=str(account.channel),
        external_id=account.external_id,
        display_name=account.display_name,
        is_active=account.is_active,
        department_id=account.department_id,
        department_name=account.department.name if account.department else None,
        has_own_credentials=bool(account.credentials_ciphertext),
    )


@router.get("/channel-accounts", response_model=list[ChannelAccountOut])
async def list_channel_accounts(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> list[ChannelAccountOut]:
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    accounts = await repo.list_channel_accounts(session, tenant_id=tenant_row.id)
    return [_channel_account_out(account) for account in accounts]


@router.post(
    "/channel-accounts", status_code=status.HTTP_201_CREATED, response_model=ChannelAccountOut
)
async def create_channel_account(
    body: ChannelAccountIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> ChannelAccountOut:
    """Alta manual de una cuenta de canal. Reservado a administración."""
    channel = ChannelKind(body.channel)
    if channel is ChannelKind.FACEBOOK and not body.access_token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Facebook exige el token de acceso de la página",
        )

    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if body.department_id is not None:
        department = await repo.get_department(session, body.department_id)
        if department is None or department.tenant_id != tenant_row.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )

    credentials = {
        campo: valor
        for campo in CHANNEL_CREDENTIAL_FIELDS
        if (valor := getattr(body, campo))
    }
    credentials_ciphertext = None
    if credentials and channel is not ChannelKind.MSBOT:
        try:
            credentials_ciphertext = encrypt_json(credentials, settings=settings)
        except EncryptionNotConfiguredError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    account = await repo.create_channel_account(
        session,
        tenant_id=tenant_row.id,
        channel=channel,
        external_id=body.external_id,
        display_name=body.display_name,
        department_id=body.department_id,
        credentials_ciphertext=credentials_ciphertext,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="channel_account_created",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={"channel": body.channel, "external_id": body.external_id},
    )
    account = await repo.get_channel_account(session, account.id)
    return _channel_account_out(account)


@router.patch("/channel-accounts/{account_id}", response_model=ChannelAccountOut)
async def update_channel_account(
    account_id: uuid.UUID,
    body: ChannelAccountUpdateIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> ChannelAccountOut:
    """Edita nombre, departamento, estado o token propio. Reservado a administración."""
    account = await repo.get_channel_account(session, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")

    changes = body.model_dump(exclude_unset=True)
    if changes.get("department_id") is not None:
        department = await repo.get_department(session, changes["department_id"])
        if department is None or department.tenant_id != account.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )

    tocadas = [campo for campo in CHANNEL_CREDENTIAL_FIELDS if campo in changes]
    if tocadas:
        # Se parte de lo que ya había y se aplica solo lo que llegó: así,
        # cambiar el token de acceso no borra el secreto de la app guardado
        # antes. Una cadena vacía sí quita ese valor concreto.
        try:
            actuales = (
                decrypt_json(account.credentials_ciphertext, settings=settings)
                if account.credentials_ciphertext
                else {}
            )
            for campo in tocadas:
                valor = changes.pop(campo)
                if valor:
                    actuales[campo] = valor
                else:
                    actuales.pop(campo, None)
            changes["credentials_ciphertext"] = (
                encrypt_json(actuales, settings=settings) if actuales else None
            )
        except EncryptionNotConfiguredError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except DecryptionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await repo.update_channel_account(session, account, **changes)
    await repo.record_audit(
        session,
        tenant_id=account.tenant_id,
        actor=principal.audit_actor,
        action="channel_account_updated",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={
            key: (str(value) if isinstance(value, uuid.UUID) else value)
            for key, value in changes.items()
            if key != "credentials_ciphertext"
        },
    )
    account = await repo.get_channel_account(session, account.id)
    return _channel_account_out(account)


@router.delete("/channel-accounts/{account_id}")
async def delete_channel_account(
    account_id: uuid.UUID,
    session: SessionDep,
    principal: AdminDep,
    confirm: bool = False,
) -> dict[str, Any]:
    """Borra una cuenta de canal. Reservado a administración.

    Con conversaciones detrás no se borra a la primera: se responde 409 con
    cuántas hay, y solo se procede con ``?confirm=true``. Es una operación que
    no se deshace, y quien la pide merece saber qué había antes de perderla.

    El historial no se va con la cuenta —la clave foránea es ``SET NULL``—,
    pero sí lo hacen sus credenciales y el enrutado automático al departamento.
    Para dejar de recibir sin perder nada, está desactivarla.
    """
    account = await repo.get_channel_account(session, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")

    conversations = await repo.count_conversations_of_account(session, account.id)
    if conversations and not confirm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"La cuenta tiene {conversations} conversaciones. Se conservarán, "
                "pero quedarán sin cuenta asociada. Confirme para continuar."
            ),
        )

    await repo.delete_channel_account(session, account)
    await repo.record_audit(
        session,
        tenant_id=account.tenant_id,
        actor=principal.audit_actor,
        action="channel_account_deleted",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={
            "channel": str(account.channel),
            "external_id": account.external_id,
            "conversaciones": conversations,
        },
    )
    log.info(
        "channel_account_deleted",
        channel=str(account.channel),
        external_id=account.external_id,
        conversations=conversations,
    )
    return {"status": "ok", "conversations_kept": conversations}


# --------------------------------------------------------------------------- #
# Configuración del inquilino
# --------------------------------------------------------------------------- #
@router.get("/admin/settings")
async def get_admin_settings(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> dict[str, Any]:
    """Ajustes editables desde la consola. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    fallback_message = tenant_row.settings.get("fallback_message") or FallbackHandler.MESSAGE
    return {"fallback_message": fallback_message}


@router.put("/admin/settings")
async def update_admin_settings(
    body: AdminSettingsIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Actualiza la respuesta automática del asistente. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    await repo.update_tenant_settings(
        session, tenant_row, fallback_message=body.fallback_message
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="fallback_message_updated",
        subject_type="tenant",
        subject_id=str(tenant_row.id),
    )
    return {"status": "ok", "fallback_message": tenant_row.settings["fallback_message"]}


def _branding_payload(accent: str | None) -> dict[str, Any]:
    """Lo que necesita la consola: el color guardado y el CSS ya derivado.

    Se devuelve el CSS hecho, y no solo los colores, para que la consola pueda
    repintarse al guardar sin repetir en JavaScript el calculo de contraste que
    ya vive en ``app/core/branding.py``. Una segunda copia de esa aritmetica
    acabaria desviandose de la primera.
    """
    return {
        "accent": accent,
        "css": brand_css(accent),
        "palette": derive_palette(accent) if accent else None,
    }


@router.get("/admin/branding")
async def get_branding(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> dict[str, Any]:
    """Color de marca vigente. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return _branding_payload(read_accent(tenant_row.settings))


@router.get("/admin/branding/preview")
async def preview_branding(
    principal: AdminDep, accent: str = Query(min_length=4, max_length=7)
) -> dict[str, Any]:
    """Deriva la paleta de un color sin guardarlo.

    Permite que la consola enseñe como quedaria el color antes de fijarlo, sin
    que haya que guardar para verlo y sin repetir en el navegador el calculo de
    contraste. No toca la base: solo hace cuentas.
    """
    try:
        return _branding_payload(normalize_hex(accent))
    except InvalidColor as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/admin/branding")
async def set_branding(
    body: BrandingIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Fija el color de marca de toda la instalación. Reservado a administración.

    Se guarda un solo color: el resto de la paleta —el texto que va encima, la
    variante que se lee sobre el fondo, la de tema oscuro— se deriva de él con
    los umbrales de contraste de la WCAG. Es lo que impide que una elección
    desafortunada deje botones o enlaces ilegibles.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    await repo.update_tenant_settings(
        session,
        tenant_row,
        **{BRANDING_SETTINGS_KEY: {"accent": body.accent} if body.accent else {}},
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="branding_updated",
        subject_type="tenant",
        subject_id=str(tenant_row.id),
        detail={"accent": body.accent},
    )
    return _branding_payload(body.accent)


@router.get("/admin/service-defaults", response_model=BusinessHoursIn)
async def get_service_defaults(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> BusinessHoursIn:
    """Horario y objetivo que rigen la cola común y lo que no fija su propio valor."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    stored = tenant_row.settings.get(SERVICE_SETTINGS_KEY) or {}
    return BusinessHoursIn(**stored)


@router.put("/admin/service-defaults", response_model=BusinessHoursIn)
async def set_service_defaults(
    body: BusinessHoursIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> BusinessHoursIn:
    """Fija lo que rige por omisión. Reservado a administración.

    Es lo que permite medir también la cola común: una conversación que aún no
    se derivó a nadie no tiene departamento del que heredar un objetivo.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    _require_known_timezone(body.timezone)
    await repo.update_tenant_settings(
        session,
        tenant_row,
        **{SERVICE_SETTINGS_KEY: body.model_dump(mode="json", exclude_none=True)},
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="service_defaults_updated",
        subject_type="tenant",
        subject_id=str(tenant_row.id),
        detail={
            "timezone": body.timezone,
            "objetivo_minutos": body.first_response_target_minutes,
        },
    )
    return body


# --------------------------------------------------------------------------- #
# Supervisión
# --------------------------------------------------------------------------- #
@router.get("/supervisor/overview")
async def supervisor_overview(
    session: SessionDep,
    settings: SettingsDep,
    principal: SupervisorDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Panorama del equipo: carga por agente y últimas derivaciones."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    names = await _agent_names(session, tenant_row.id)
    return {
        "tenant": tenant_row.slug,
        "workload": await repo.workload_by_agent(session, tenant_row.id),
        "messages_by_channel": await repo.channel_stats(session, tenant_row.id),
        "recent_transfers": [
            {
                "conversation_id": str(entry.conversation_id),
                "action": entry.action,
                "from": names.get(entry.from_agent_id),
                "to": names.get(entry.to_agent_id),
                "by": names.get(entry.by_agent_id),
                "note": entry.note,
                "at": entry.created_at.isoformat(),
            }
            for entry in await repo.transfer_activity(session, tenant_row.id)
        ],
    }


@router.get("/stats")
async def stats(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> dict[str, Any]:
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return {
        "tenant": tenant_row.slug,
        "messages_by_channel": await repo.channel_stats(session, tenant_row.id),
    }


# --------------------------------------------------------------------------- #
# Auxiliares
# --------------------------------------------------------------------------- #
def _effective_scope(requested: Scope | None, principal: Principal) -> str:
    if principal.is_supervisor:
        return requested or "all"
    if requested in (None, "all"):
        # Un agente no obtiene la vista global; se le da lo que sí puede ver.
        return "mine_or_unassigned"
    return requested


def _validated_localized(value):
    """Acepta una cadena o un texto por idioma, y limpia lo que sobra.

    Un idioma que no ofrecemos se rechaza al guardar: escribirlo y que nunca
    llegue a nadie sería peor que el error.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    limpio = {}
    for locale, texto in value.items():
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(
                f"Idioma no admitido: {locale!r}; use {', '.join(SUPPORTED_LOCALES)}"
            )
        if texto and texto.strip():
            limpio[locale] = texto.strip()[:2_000]
    return limpio or None


def _require_known_timezone(name: str | None) -> None:
    """Rechaza una zona inexistente al guardarla.

    Se comprueba ahora para no descubrirla de noche, con un cliente esperando
    del otro lado: en ese momento el cálculo caería a UTC en silencio.
    """
    if not name:
        return
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Zona horaria desconocida: {name}",
        ) from exc


def _work_state(status: str) -> str:
    """Del estado guardado al nombre que ve el equipo."""
    for name, stored in WORK_STATES.items():
        if stored == status:
            return name
    # ``snoozed`` y cualquier otro heredado cuentan como pendientes: nadie
    # los da por resueltos.
    return "pending"


def _sla_status(row: Conversation) -> str | None:
    """Estado del objetivo de primera respuesta, para pintarlo en la bandeja.

    Se marca vencida en cuanto pasó la hora, sin esperar al repaso periódico:
    la consola debe mostrarlo al instante y no hasta cinco minutos después.
    """
    if row.first_response_at is not None:
        return "met" if row.sla_breached_at is None else "breached"
    if row.first_response_due_at is None:
        return None
    # ``as_utc`` porque SQLite, el motor de las pruebas, devuelve la fecha sin
    # zona y compararla con ``utcnow()`` reventaría.
    if row.sla_breached_at is not None or repo.as_utc(row.first_response_due_at) <= utcnow():
        return "breached"
    return "pending"


def _department_out(row: Department) -> DepartmentOut:
    return DepartmentOut(
        id=row.id,
        name=row.name,
        is_active=row.is_active,
        business_hours=dict(row.business_hours or {}),
        timezone=row.timezone,
        out_of_hours_message=row.out_of_hours_message,
        first_response_target_minutes=row.first_response_target_minutes,
    )


def _conversation_out(row: Conversation) -> ConversationOut:
    return ConversationOut(
        id=row.id,
        channel=str(row.channel),
        status=row.status,
        control=row.control,
        subject=row.subject,
        contact_name=row.contact.display_name if row.contact else None,
        assignee_id=row.assignee_id,
        assignee_name=row.assignee.label if row.assignee else None,
        department_id=row.department_id,
        department_name=row.department.name if row.department else None,
        unread_count=row.unread_count,
        work_state=_work_state(row.status),
        last_message_at=row.last_message_at.isoformat() if row.last_message_at else None,
        sla_status=_sla_status(row),
        sla_due_at=row.first_response_due_at.isoformat() if row.first_response_due_at else None,
        labels=[
            LabelOut(id=label.id, name=label.name, color=label.color) for label in row.labels
        ],
    )


async def _agent_names(session: Any, tenant_id: uuid.UUID) -> dict[uuid.UUID | None, str]:
    agents = await repo.list_agents(session, tenant_id=tenant_id, only_active=False)
    return {agent.id: agent.label for agent in agents}


async def _department_names(session: Any, tenant_id: uuid.UUID) -> dict[uuid.UUID | None, str]:
    departments = await repo.list_departments(session, tenant_id=tenant_id, only_active=False)
    return {department.id: department.name for department in departments}


async def _notify_assignment(
    session: Any,
    conversation: Conversation,
    entry: Any,
    principal: Principal,
    target: Agent | None = None,
    department: Department | None = None,
) -> None:
    """Avisa a las personas implicadas y, si procede, a toda la cola común.

    Cada tema recibe solo lo que le concierne. Una derivación entre dos agentes
    no altera la cola común, de modo que no se publica allí: hacerlo obligaría a
    cada consola suscrita a los dos temas a refrescarse dos veces por el mismo
    hecho.
    """
    event = {
        "type": "assignment",
        "action": entry.action,
        "conversation_id": str(conversation.id),
        "assignee_id": str(conversation.assignee_id) if conversation.assignee_id else None,
        "assignee_name": target.label if target else None,
        "department_id": str(department.id) if department else None,
        "department_name": department.name if department else None,
        "by": principal.label,
        "note": entry.note,
    }

    destinatarios = {
        str(agent_id)
        for agent_id in (entry.to_agent_id, entry.from_agent_id)
        if agent_id is not None
    }
    for agent_id in destinatarios:
        await hub.publish(agent_topic(agent_id), event)

    # Solo las acciones que cambian el contenido de la cola común —o la de un
    # departamento— interesan a quienes la vigilan.
    if entry.action in {"claim", "release", "close", "reopen", "transfer_department"}:
        tenant = await session.get(Tenant, conversation.tenant_id)
        await hub.publish(inbox_topic(tenant.slug if tenant else "default"), event)

    if entry.action in {"claim", "transfer", "release", "transfer_department"}:
        await _publish_control_change(
            conversation,
            control=conversation.control,
            assignee_name=target.label if target else None,
        )


async def _publish_control_change(
    conversation: Conversation, *, control: str, assignee_name: str | None
) -> None:
    """Avisa al chatbox del cliente para que el encabezado muestre quién le atiende."""
    await hub.publish(
        conversation_topic(conversation.channel_conversation_id),
        {"type": "control_changed", "control": control, "assignee_name": assignee_name},
    )
