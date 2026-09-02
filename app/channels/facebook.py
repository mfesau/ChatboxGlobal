"""Adaptador de Facebook Messenger (Meta Graph API).

Misma familia que WhatsApp Cloud API —comparte el esquema de verificación de
firma y de reto de suscripción del webhook—, pero cada página tiene su propio
token de acceso: a diferencia de WhatsApp, aquí no hay una credencial global
de respaldo en ``.env`` (ver ``ConversationRef.extra["credentials"]``,
resuelto por ``OutboxDispatcher`` antes de llamar a :meth:`send`).

Entrada: webhook ``POST`` con firma ``X-Hub-Signature-256``.
Salida:  ``POST /me/messages?access_token=...``.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.channels.base import ChannelAdapter, SignatureError, register_channel
from app.config import Settings
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    DeliveryReceipt,
    DeliveryStatus,
    InboundMessage,
    OutboundMessage,
    Party,
)
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Límite de caracteres del cuerpo de un mensaje de texto en Messenger.
TEXT_LIMIT = 2_000

_ATTACHMENT_TYPES: dict[str, ContentType] = {
    "image": ContentType.IMAGE,
    "audio": ContentType.AUDIO,
    "video": ContentType.VIDEO,
    "file": ContentType.DOCUMENT,
}


@register_channel(ChannelKind.FACEBOOK)
class FacebookAdapter(ChannelAdapter):
    """Traductor entre Facebook Messenger y el formato canónico."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._client = httpx.AsyncClient(
            base_url=f"{settings.facebook_api_base}/{settings.facebook_api_version}",
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --------------------------------------------------------------- entrada
    def verify_subscription(
        self,
        params: Mapping[str, str],
        *,
        credentials: Sequence[Mapping[str, str]] = (),
    ) -> str:
        """Resuelve el reto ``GET`` de alta de webhook y devuelve ``hub.challenge``.

        Como en WhatsApp, vale el token de ``.env`` o el de cualquier página
        dada de alta desde la consola: el reto no dice de qué página viene.
        """
        esperados = [c["verify_token"] for c in credentials if c.get("verify_token")]
        if (global_token := self.settings.facebook_verify_token) is not None:
            esperados.append(global_token.get_secret_value())
        if not esperados:
            raise SignatureError("FACEBOOK_VERIFY_TOKEN no está configurado")
        if params.get("hub.mode") != "subscribe":
            raise SignatureError("hub.mode no soportado")
        recibido = params.get("hub.verify_token", "")
        if not any(hmac.compare_digest(recibido, e) for e in esperados):
            raise SignatureError("hub.verify_token no coincide")
        return params.get("hub.challenge", "")

    async def verify_request(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        credentials: Sequence[Mapping[str, str]] = (),
    ) -> None:
        secretos = [c["app_secret"] for c in credentials if c.get("app_secret")]
        if (global_secret := self.settings.facebook_app_secret) is not None:
            secretos.append(global_secret.get_secret_value())
        if not secretos:
            if self.settings.environment == "prod":
                raise SignatureError("FACEBOOK_APP_SECRET es obligatorio en producción")
            log.warning("facebook_signature_skipped", reason="app_secret_no_configurado")
            return

        header = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
        if not header or not header.startswith("sha256="):
            raise SignatureError("Falta la cabecera X-Hub-Signature-256")

        recibida = header.removeprefix("sha256=")
        for secreto in secretos:
            digest = hmac.new(secreto.encode(), body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(digest, recibida):
                return
        raise SignatureError("La firma del cuerpo no coincide")

    async def parse(
        self, *, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        if payload.get("object") != "page":
            return []

        results: list[InboundMessage] = []
        for entry in payload.get("entry") or []:
            page_id = entry.get("id")
            for event in entry.get("messaging") or []:
                parsed = self._parse_event(event, page_id)
                if parsed is not None:
                    results.append(parsed)
        return results

    def _parse_event(self, event: dict[str, Any], page_id: str | None) -> InboundMessage | None:
        psid = (event.get("sender") or {}).get("id")
        if not psid:
            return None

        if message := event.get("message"):
            return self._parse_message(event, message, psid, page_id)
        if postback := event.get("postback"):
            return self._parse_postback(event, postback, psid, page_id)
        if delivery := event.get("delivery"):
            return self._parse_receipt(event, "delivery", delivery, psid, page_id)
        if read := event.get("read"):
            return self._parse_receipt(event, "read", read, psid, page_id)
        # ``optin``, cambios de plantilla y demás no requieren respuesta.
        return None

    def _ref(self, psid: str, page_id: str | None) -> ConversationRef:
        return ConversationRef(
            channel=ChannelKind.FACEBOOK,
            channel_conversation_id=psid,
            channel_account_id=page_id,
            extra={"psid": psid},
        )

    def _parse_message(
        self, event: dict[str, Any], message: dict[str, Any], psid: str, page_id: str | None
    ) -> InboundMessage | None:
        message_id = message.get("mid")
        if not message_id:
            return None
        if message.get("is_echo"):
            # Confirmación del propio envío de la página; no es un mensaje entrante.
            return None

        text = message.get("text")
        attachments = [
            Attachment(
                content_type=_ATTACHMENT_TYPES.get(item.get("type", ""), ContentType.UNKNOWN),
                url=(item.get("payload") or {}).get("url"),
            )
            for item in message.get("attachments") or []
        ]
        content_type = ContentType.TEXT if text else ContentType.UNKNOWN
        if attachments and not text:
            content_type = attachments[0].content_type

        return InboundMessage(
            channel=ChannelKind.FACEBOOK,
            conversation=self._ref(psid, page_id),
            sender=Party(channel_user_id=psid, raw={"psid": psid}),
            provider_message_id=message_id,
            content_type=content_type,
            text=text,
            attachments=attachments,
            timestamp=_from_epoch(event.get("timestamp")),
            raw=event,
        )

    def _parse_postback(
        self, event: dict[str, Any], postback: dict[str, Any], psid: str, page_id: str | None
    ) -> InboundMessage:
        return InboundMessage(
            channel=ChannelKind.FACEBOOK,
            conversation=self._ref(psid, page_id),
            sender=Party(channel_user_id=psid, raw={"psid": psid}),
            provider_message_id=f"postback:{psid}:{event.get('timestamp')}",
            content_type=ContentType.INTERACTIVE,
            text=postback.get("title"),
            action={"id": postback.get("payload"), "title": postback.get("title")},
            timestamp=_from_epoch(event.get("timestamp")),
            raw=event,
        )

    def _parse_receipt(
        self,
        event: dict[str, Any],
        kind: str,
        receipt: dict[str, Any],
        psid: str,
        page_id: str | None,
    ) -> InboundMessage:
        """Acuse de entrega o lectura, convertido en un evento de sistema."""
        status = DeliveryStatus.DELIVERED if kind == "delivery" else DeliveryStatus.READ
        mids = receipt.get("mids") or []
        return InboundMessage(
            channel=ChannelKind.FACEBOOK,
            conversation=self._ref(psid, page_id),
            sender=Party(channel_user_id=psid),
            provider_message_id=f"{kind}:{psid}:{receipt.get('watermark')}",
            content_type=ContentType.SYSTEM,
            action={
                "kind": "delivery_status",
                "target_provider_message_id": mids[0] if mids else None,
                "status": str(status),
                "provider_status": kind,
            },
            timestamp=_from_epoch(event.get("timestamp")),
            raw=event,
        )

    # ---------------------------------------------------------------- salida
    async def send(
        self, *, ref: ConversationRef, message: OutboundMessage
    ) -> DeliveryReceipt:
        token = ref.extra.get("credentials", {}).get("access_token")
        if not token:
            return DeliveryReceipt.failed(
                "not_configured",
                "Falta el token de acceso de la página; dela de alta en «Cuentas de canal»",
            )

        last: DeliveryReceipt | None = None
        for body in self._build_payloads(ref.channel_conversation_id, message):
            last = await self._post(token, body)
            if not last.ok:
                return last
        return last or DeliveryReceipt.failed("empty_message", "Nada que enviar")

    def _build_payloads(self, psid: str, message: OutboundMessage) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for attachment in message.attachments:
            attachment_type = _reverse_attachment_type(attachment.content_type)
            if not attachment.url:
                continue
            payloads.append(
                {
                    "recipient": {"id": psid},
                    "message": {
                        "attachment": {
                            "type": attachment_type,
                            "payload": {"url": attachment.url, "is_reusable": True},
                        }
                    },
                }
            )

        text = (message.text or "").strip()
        if text and message.quick_replies:
            payloads.append(
                {
                    "recipient": {"id": psid},
                    "message": {
                        "text": text[:TEXT_LIMIT],
                        "quick_replies": [
                            {
                                "content_type": "text",
                                "title": option.get("title", "")[:20],
                                "payload": option.get("id") or option.get("title", ""),
                            }
                            for option in message.quick_replies[:13]
                        ],
                    },
                }
            )
        elif text:
            payloads.extend(
                {"recipient": {"id": psid}, "message": {"text": chunk}}
                for chunk in _split_text(text, TEXT_LIMIT)
            )
        return payloads

    async def _post(self, token: str, body: dict[str, Any]) -> DeliveryReceipt:
        try:
            response = await self._client.post(
                "/me/messages", params={"access_token": token}, json=body
            )
        except httpx.HTTPError as exc:
            return DeliveryReceipt.failed("network_error", str(exc), retryable=True)

        if response.status_code >= 400:
            detail = response.text[:500]
            retryable = response.status_code == 429 or response.status_code >= 500
            return DeliveryReceipt.failed(
                f"http_{response.status_code}", detail, retryable=retryable
            )

        data = response.json()
        return DeliveryReceipt.sent(data.get("message_id"), **data)

    async def set_typing(self, *, ref: ConversationRef) -> None:
        token = ref.extra.get("credentials", {}).get("access_token")
        if not token:
            return
        await self._post(
            token,
            {
                "recipient": {"id": ref.channel_conversation_id},
                "sender_action": "typing_on",
            },
        )

    async def mark_read(self, *, ref: ConversationRef, provider_message_id: str) -> None:
        token = ref.extra.get("credentials", {}).get("access_token")
        if not token:
            return
        await self._post(
            token,
            {
                "recipient": {"id": ref.channel_conversation_id},
                "sender_action": "mark_seen",
            },
        )


def _reverse_attachment_type(content_type: ContentType) -> str:
    for name, mapped in _ATTACHMENT_TYPES.items():
        if mapped is content_type:
            return name
    return "file"


def _split_text(text: str, limit: int) -> list[str]:
    """Divide por saltos de línea cuando es posible, y por longitud si no."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _from_epoch(value: Any) -> datetime:
    try:
        # Messenger manda milisegundos, no segundos como WhatsApp.
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)
