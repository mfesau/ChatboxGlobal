"""Adaptador del chatbox web propio.

La entrada llega por WebSocket o por ``POST`` REST desde el widget; la salida se
difunde a las conexiones suscritas al tema de la conversación. El mensaje queda
persistido en cualquier caso, de modo que un cliente que se reconecta recupera
el historial completo aunque estuviese desconectado durante el envío.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from app.channels.base import ChannelAdapter, register_channel
from app.config import Settings
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    DeliveryReceipt,
    InboundMessage,
    OutboundMessage,
    Party,
)
from app.core.hub import conversation_topic, hub


@register_channel(ChannelKind.WEB)
class WebAdapter(ChannelAdapter):
    """Canal interno servido por la propia aplicación."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)

    async def parse(
        self, *, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return []

        text = payload.get("text")
        attachments = [
            Attachment(
                content_type=ContentType(item.get("content_type", "document")),
                url=item.get("url"),
                mime_type=item.get("mime_type"),
                filename=item.get("filename"),
                size_bytes=item.get("size_bytes"),
            )
            for item in payload.get("attachments") or []
        ]

        content_type = ContentType.TEXT
        if payload.get("action"):
            content_type = ContentType.INTERACTIVE
        elif attachments and not text:
            content_type = attachments[0].content_type

        return [
            InboundMessage(
                channel=ChannelKind.WEB,
                conversation=ConversationRef(
                    channel=ChannelKind.WEB,
                    channel_conversation_id=session_id,
                    channel_account_id="web",
                    extra={"user_agent": headers.get("user-agent")},
                ),
                sender=Party(
                    channel_user_id=session_id,
                    display_name=payload.get("display_name") or "Visitante",
                    email=payload.get("email"),
                    locale=payload.get("locale"),
                ),
                # El cliente puede aportar su propio identificador para lograr
                # idempotencia ante reintentos de red.
                provider_message_id=str(
                    payload.get("client_message_id") or f"web:{session_id}:{time.time_ns()}"
                ),
                content_type=content_type,
                text=text,
                attachments=attachments,
                action=payload.get("action"),
                tenant_slug=payload.get("tenant"),
                raw=payload,
            )
        ]

    async def send(
        self, *, ref: ConversationRef, message: OutboundMessage
    ) -> DeliveryReceipt:
        delivered = await hub.publish(
            conversation_topic(ref.channel_conversation_id),
            {
                "type": "message",
                "direction": "outbound",
                "conversation_id": ref.channel_conversation_id,
                "message": message.to_dict(),
            },
        )
        # Sin suscriptores activos el mensaje no se pierde: ya está persistido y
        # el cliente lo recuperará al reconectar.
        return DeliveryReceipt.sent(
            f"web:{message.client_message_id}", delivered_to=delivered
        )

    async def set_typing(self, *, ref: ConversationRef) -> None:
        await hub.publish(
            conversation_topic(ref.channel_conversation_id),
            {"type": "typing", "conversation_id": ref.channel_conversation_id},
        )
