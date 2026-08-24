"""Formato interno común (*canonical envelope*).

Todo canal se normaliza a estas estructuras antes de entrar en la lógica de
negocio. Añadir un canal nuevo no exige tocar el orquestador ni los handlers:
basta con producir un :class:`InboundMessage` y consumir un
:class:`OutboundMessage`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ChannelKind(StrEnum):
    """Canales soportados. Ampliable sin migración de datos."""

    WHATSAPP = "whatsapp"
    MSBOT = "msbot"          # Microsoft Bot Framework (Teams, Web Chat, Direct Line)
    WEB = "web"              # Chatbox propio embebido
    INTERNAL = "internal"    # Mensajes generados por el sistema


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ContentType(StrEnum):
    """Tipo semántico del contenido, independiente del proveedor."""

    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACT = "contact"
    INTERACTIVE = "interactive"   # botones, listas, tarjetas adaptativas
    REACTION = "reaction"
    SYSTEM = "system"             # altas/bajas de conversación, typing, etc.
    UNKNOWN = "unknown"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> uuid.UUID:
    return uuid.uuid4()


@dataclass(slots=True)
class Attachment:
    """Adjunto normalizado. ``provider_media_id`` permite la descarga diferida."""

    content_type: ContentType = ContentType.DOCUMENT
    url: str | None = None
    provider_media_id: str | None = None
    mime_type: str | None = None
    filename: str | None = None
    size_bytes: int | None = None
    caption: str | None = None
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_type": str(self.content_type),
            "url": self.url,
            "provider_media_id": self.provider_media_id,
            "mime_type": self.mime_type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "caption": self.caption,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Attachment:
        return cls(
            content_type=ContentType(data.get("content_type", "document")),
            url=data.get("url"),
            provider_media_id=data.get("provider_media_id"),
            mime_type=data.get("mime_type"),
            filename=data.get("filename"),
            size_bytes=data.get("size_bytes"),
            caption=data.get("caption"),
            sha256=data.get("sha256"),
        )


@dataclass(slots=True)
class Party:
    """Interlocutor: contacto externo o identidad del bot."""

    channel_user_id: str
    display_name: str | None = None
    phone: str | None = None
    email: str | None = None
    locale: str | None = None
    aad_object_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ConversationRef:
    """Datos mínimos para responder por el canal de origen.

    Es el equivalente neutro del ``ConversationReference`` del SDK de Microsoft y
    del par ``phone_number_id`` / ``wa_id`` de WhatsApp. Se persiste junto a la
    conversación para habilitar mensajes proactivos, es decir, salientes que no
    responden a un entrante inmediato.
    """

    channel: ChannelKind
    channel_conversation_id: str
    channel_account_id: str | None = None   # phone_number_id / bot app id
    service_url: str | None = None          # Bot Framework: endpoint de respuesta
    reply_to_message_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": str(self.channel),
            "channel_conversation_id": self.channel_conversation_id,
            "channel_account_id": self.channel_account_id,
            "service_url": self.service_url,
            "reply_to_message_id": self.reply_to_message_id,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationRef:
        return cls(
            channel=ChannelKind(data["channel"]),
            channel_conversation_id=data["channel_conversation_id"],
            channel_account_id=data.get("channel_account_id"),
            service_url=data.get("service_url"),
            reply_to_message_id=data.get("reply_to_message_id"),
            extra=data.get("extra") or {},
        )


@dataclass(slots=True)
class InboundMessage:
    """Mensaje entrante normalizado."""

    channel: ChannelKind
    conversation: ConversationRef
    sender: Party
    provider_message_id: str
    content_type: ContentType = ContentType.TEXT
    text: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    #: Carga útil de una interacción: identificador del botón pulsado, etc.
    action: dict[str, Any] | None = None
    timestamp: datetime = field(default_factory=utcnow)
    tenant_slug: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    #: Clave de idempotencia; por omisión, canal más identificador del proveedor.
    dedupe_key: str | None = None

    def __post_init__(self) -> None:
        if not self.dedupe_key:
            self.dedupe_key = f"{self.channel}:{self.provider_message_id}"

    @property
    def is_actionable(self) -> bool:
        """Indica si la lógica de negocio debe reaccionar a este mensaje."""
        return self.content_type is not ContentType.SYSTEM


@dataclass(slots=True)
class OutboundMessage:
    """Mensaje saliente normalizado, independiente del canal."""

    text: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    #: Respuestas rápidas: ``[{"id": "si", "title": "Sí"}]``.
    quick_replies: list[dict[str, str]] = field(default_factory=list)
    content_type: ContentType = ContentType.TEXT
    #: Metadatos libres para el adaptador: plantillas, tarjetas adaptativas, etc.
    channel_data: dict[str, Any] = field(default_factory=dict)
    #: Identificador interno; correlaciona el acuse de recibo del proveedor.
    client_message_id: uuid.UUID = field(default_factory=new_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "attachments": [a.to_dict() for a in self.attachments],
            "quick_replies": self.quick_replies,
            "content_type": str(self.content_type),
            "channel_data": self.channel_data,
            "client_message_id": str(self.client_message_id),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutboundMessage:
        raw_id = data.get("client_message_id")
        return cls(
            text=data.get("text"),
            attachments=[Attachment.from_dict(a) for a in data.get("attachments") or []],
            quick_replies=data.get("quick_replies") or [],
            content_type=ContentType(data.get("content_type", "text")),
            channel_data=data.get("channel_data") or {},
            client_message_id=uuid.UUID(raw_id) if raw_id else new_id(),
        )


@dataclass(slots=True)
class DeliveryReceipt:
    """Resultado de un envío realizado por un adaptador de canal."""

    status: DeliveryStatus
    provider_message_id: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    retryable: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is not DeliveryStatus.FAILED

    @classmethod
    def sent(cls, provider_message_id: str | None = None, **raw: Any) -> DeliveryReceipt:
        return cls(status=DeliveryStatus.SENT, provider_message_id=provider_message_id, raw=raw)

    @classmethod
    def failed(
        cls,
        error_code: str,
        error_detail: str = "",
        *,
        retryable: bool = False,
    ) -> DeliveryReceipt:
        return cls(
            status=DeliveryStatus.FAILED,
            error_code=error_code,
            error_detail=error_detail,
            retryable=retryable,
        )
