"""Esquemas de entrada y salida de la consola."""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.branding import (
    InvalidColor,
    normalize_hex,
)
from app.core.business_hours import parse_clock
from app.core.envelope import (
    ChannelKind,
)
from app.core.localized import SUPPORTED_LOCALES
from app.db.models import (
    ROLE_AGENT,
)

Scope = Literal["unassigned", "mine", "mine_or_unassigned", "all"]


# --------------------------------------------------------------------------- #
# Esquemas
# --------------------------------------------------------------------------- #
class LabelOut(BaseModel):
    id: uuid.UUID
    name: str
    color: str
    #: Nulo = de toda la empresa.
    department_id: uuid.UUID | None = None


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
    #: Idioma original y traducciones para el equipo (ver app/core/translation.py).
    translations: dict[str, Any] = Field(default_factory=dict)


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
    logo_url: str | None = None
    hotel_enabled: bool = False


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
    currency: str = Field(default="PYG", min_length=3, max_length=3)


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
    currency: str = Field(default="PYG", min_length=3, max_length=3)
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
    department_id: uuid.UUID | None = None


class CannedResponseIn(BaseModel):
    shortcode: str = Field(min_length=1, max_length=40, pattern=SHORTCODE_PATTERN)
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4_000)
    #: Nulo = de toda la empresa.
    department_id: uuid.UUID | None = None


class CannedResponsePatchIn(BaseModel):
    shortcode: str | None = Field(
        default=None, min_length=1, max_length=40, pattern=SHORTCODE_PATTERN
    )
    title: str | None = Field(default=None, min_length=1, max_length=160)
    body: str | None = Field(default=None, min_length=1, max_length=4_000)
    #: Solo se cambia si viene en la petición; ``null`` la pasa a toda la empresa.
    department_id: uuid.UUID | None = None


class LabelIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#6b7280", max_length=20)
    department_id: uuid.UUID | None = None


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
    department_id: uuid.UUID | None = None


class MacroIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    steps: list[MacroStep] = Field(min_length=1, max_length=20)
    department_id: uuid.UUID | None = None

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
    #: Si viene, el texto se traduce a este idioma antes de enviarlo; el cliente
    #: recibe la traducción y la consola guarda también lo que se escribió.
    translate_to: str | None = None
    #: Idioma en que escribe el agente (el de su consola).
    source_language: str | None = None


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
