"""Adaptador de Telegram (Bot API).

El más simple de los canales externos. No hay firma criptográfica del cuerpo
como en Meta: Telegram devuelve, en cada aviso, la cabecera
``X-Telegram-Bot-Api-Secret-Token`` con el valor que se le declaró al dar de
alta el webhook. Comprobarla es lo que impide que un tercero que adivine la
URL —que es pública— inyecte conversaciones falsas.

Cada bot tiene su token y no hay credencial global de respaldo en ``.env``: se
da de alta desde la consola, en «Cuentas de canal», y llega hasta aquí en
``ConversationRef.extra["credentials"]``, que resuelve ``OutboxDispatcher``
antes de llamar a :meth:`send`.

Entrada: webhook ``POST /webhooks/telegram``.
Salida:  ``POST https://api.telegram.org/bot<token>/sendMessage``.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from app.channels.base import ChannelAdapter, SignatureError, register_channel
from app.channels.text import split_text
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
from app.logging_setup import get_logger

log = get_logger(__name__)

API_BASE = "https://api.telegram.org"

#: Tope de caracteres de un mensaje de texto en la Bot API.
TEXT_LIMIT = 4_096

#: Cabecera con la que Telegram devuelve el secreto declarado en ``setWebhook``.
#: Es el nombre de la cabecera, no el secreto; el analizador se guía por el
#: nombre de la constante.
SECRET_HEADER = "x-telegram-bot-api-secret-token"  # noqa: S105

#: Botones por fila en el teclado en línea. Más de dos y los títulos se
#: recortan en un teléfono estrecho.
BUTTONS_PER_ROW = 2


@register_channel(ChannelKind.TELEGRAM)
class TelegramAdapter(ChannelAdapter):
    """Traductor entre la Bot API de Telegram y el formato canónico."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._client = httpx.AsyncClient(
            base_url=API_BASE, timeout=httpx.Timeout(20.0, connect=5.0)
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --------------------------------------------------------------- entrada
    async def verify_request(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        credentials: Sequence[Mapping[str, str]] = (),
        url: str | None = None,
    ) -> None:
        """Comprueba el secreto declarado en ``setWebhook``.

        Vale el de cualquier bot dado de alta: como en Meta, la comprobación
        ocurre antes de leer el cuerpo, así que todavía no se sabe de qué
        cuenta viene el aviso.
        """
        esperados = [c["verify_token"] for c in credentials if c.get("verify_token")]
        if not esperados:
            # Sin secreto configurado la URL del webhook es la única defensa, y
            # es pública. Se admite mientras se prueba, nunca en producción.
            if self.settings.environment == "prod":
                raise SignatureError(
                    "Configure el token de verificación del bot de Telegram en la consola"
                )
            log.warning("telegram_secret_skipped", reason="verify_token_no_configurado")
            return

        recibido = headers.get(SECRET_HEADER) or headers.get("X-Telegram-Bot-Api-Secret-Token")
        if not recibido:
            raise SignatureError("Falta la cabecera X-Telegram-Bot-Api-Secret-Token")
        if not any(hmac.compare_digest(recibido, esperado) for esperado in esperados):
            raise SignatureError("El secreto del webhook no coincide")

    async def parse(
        self, *, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        if callback := payload.get("callback_query"):
            parsed = self._parse_callback(callback)
            return [parsed] if parsed else []

        # Una edición se trata como un mensaje más: el identificador lleva la
        # marca de tiempo, de modo que no choca con el original en la clave de
        # idempotencia y el equipo ve las dos versiones.
        message = payload.get("message") or payload.get("edited_message")
        if not message:
            return []
        parsed = self._parse_message(message, edited="edited_message" in payload)
        return [parsed] if parsed else []

    def _ref(self, chat: dict[str, Any], bot_id: str | None = None) -> ConversationRef:
        return ConversationRef(
            channel=ChannelKind.TELEGRAM,
            channel_conversation_id=str(chat.get("id")),
            channel_account_id=bot_id,
            extra={"chat_type": chat.get("type")},
        )

    def _sender(self, user: dict[str, Any]) -> Party:
        nombre = " ".join(
            part for part in (user.get("first_name"), user.get("last_name")) if part
        )
        return Party(
            channel_user_id=str(user.get("id")),
            display_name=nombre or user.get("username"),
            locale=user.get("language_code"),
            raw={"username": user.get("username")},
        )

    def _parse_message(
        self, message: dict[str, Any], *, edited: bool = False
    ) -> InboundMessage | None:
        chat = message.get("chat") or {}
        user = message.get("from") or {}
        if not chat.get("id") or not user.get("id"):
            return None

        message_id = message.get("message_id")
        # El identificador de Telegram solo es único dentro de un chat, así que
        # por sí solo no sirve como clave de idempotencia entre conversaciones.
        provider_message_id = f"{chat['id']}:{message_id}"
        if edited:
            provider_message_id += f":edit:{message.get('edit_date')}"

        text = message.get("text") or message.get("caption")
        attachments = self._attachments(message)
        content_type = ContentType.TEXT if text else ContentType.UNKNOWN
        if attachments:
            content_type = ContentType.TEXT if text else attachments[0].content_type

        return InboundMessage(
            channel=ChannelKind.TELEGRAM,
            conversation=self._ref(chat),
            sender=self._sender(user),
            provider_message_id=provider_message_id,
            content_type=content_type,
            text=text,
            attachments=attachments,
            timestamp=_from_epoch(message.get("date")),
            raw=message,
        )

    def _attachments(self, message: dict[str, Any]) -> list[Attachment]:
        """Normaliza el adjunto, que Telegram nombra distinto según el tipo.

        Solo se guarda el ``file_id``: el fichero vive en Telegram y se baja
        después, en dos pasos, desde :meth:`fetch_media`.
        """
        if photos := message.get("photo"):
            # Telegram manda la misma foto en varios tamaños, de menor a mayor.
            mayor = photos[-1]
            return [
                Attachment(
                    content_type=ContentType.IMAGE,
                    provider_media_id=mayor.get("file_id"),
                    mime_type="image/jpeg",
                    size_bytes=mayor.get("file_size"),
                )
            ]

        for key, kind in (
            ("document", ContentType.DOCUMENT),
            ("video", ContentType.VIDEO),
            ("animation", ContentType.VIDEO),
            ("audio", ContentType.AUDIO),
            ("voice", ContentType.AUDIO),
            ("video_note", ContentType.VIDEO),
            ("sticker", ContentType.STICKER),
        ):
            if item := message.get(key):
                return [
                    Attachment(
                        content_type=kind,
                        provider_media_id=item.get("file_id"),
                        mime_type=item.get("mime_type"),
                        filename=item.get("file_name"),
                        size_bytes=item.get("file_size"),
                    )
                ]
        return []

    def _parse_callback(self, callback: dict[str, Any]) -> InboundMessage | None:
        """Pulsación de un botón del teclado en línea."""
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        user = callback.get("from") or {}
        if not chat.get("id") or not user.get("id"):
            return None

        return InboundMessage(
            channel=ChannelKind.TELEGRAM,
            conversation=self._ref(chat),
            sender=self._sender(user),
            provider_message_id=f"callback:{callback.get('id')}",
            content_type=ContentType.INTERACTIVE,
            text=callback.get("data"),
            action={"id": callback.get("data"), "title": callback.get("data")},
            timestamp=_from_epoch(message.get("date")),
            raw=callback,
        )

    async def fetch_media(
        self, *, attachment: Any, ref: Any
    ) -> tuple[bytes, str | None] | None:
        """Baja un adjunto. Dos pasos, como en WhatsApp.

        ``getFile`` devuelve una ruta temporal, y el fichero se pide después a
        un dominio distinto del de la API, con el token en la propia URL.
        """
        file_id = getattr(attachment, "provider_media_id", None)
        token = (getattr(ref, "extra", {}) or {}).get("credentials", {}).get("access_token")
        if not file_id or not token:
            return None

        try:
            described = await self._client.get(
                f"/bot{token}/getFile", params={"file_id": file_id}
            )
            described.raise_for_status()
            file_path = (described.json().get("result") or {}).get("file_path")
            if not file_path:
                return None
            downloaded = await self._client.get(f"/file/bot{token}/{file_path}")
            downloaded.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("telegram_media_download_failed", file_id=file_id, error=str(exc))
            return None

        return downloaded.content, downloaded.headers.get("content-type")

    # ---------------------------------------------------------------- salida
    async def send(
        self, *, ref: ConversationRef, message: OutboundMessage
    ) -> DeliveryReceipt:
        token = ref.extra.get("credentials", {}).get("access_token")
        if not token:
            return DeliveryReceipt.failed(
                "not_configured",
                "Falta el token del bot; dele de alta en «Cuentas de canal»",
            )

        last: DeliveryReceipt | None = None
        for method, body in self._build_calls(ref.channel_conversation_id, message):
            last = await self._call(token, method, body)
            if not last.ok:
                return last
        return last or DeliveryReceipt.failed("empty_message", "Nada que enviar")

    def _build_calls(
        self, chat_id: str, message: OutboundMessage
    ) -> list[tuple[str, dict[str, Any]]]:
        """Traduce un saliente a la secuencia de llamadas de la Bot API."""
        calls: list[tuple[str, dict[str, Any]]] = []

        for attachment in message.attachments:
            if not attachment.url:
                # Telegram descarga el fichero de la URL que se le pase; sin
                # ella no hay nada que mandar.
                continue
            method, field = _METHOD_FOR_ATTACHMENT.get(
                attachment.content_type, ("sendDocument", "document")
            )
            calls.append((method, {"chat_id": chat_id, field: attachment.url}))

        text = (message.text or "").strip()
        if not text:
            return calls

        chunks = split_text(text, TEXT_LIMIT)
        for index, chunk in enumerate(chunks):
            body: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            # Los botones se cuelgan del último trozo: repetirlos en cada uno
            # dejaría varios teclados vivos a la vez en el mismo hilo.
            if message.quick_replies and index == len(chunks) - 1:
                body["reply_markup"] = {"inline_keyboard": _keyboard(message.quick_replies)}
            calls.append(("sendMessage", body))
        return calls

    async def _call(self, token: str, method: str, body: dict[str, Any]) -> DeliveryReceipt:
        try:
            response = await self._client.post(f"/bot{token}/{method}", json=body)
        except httpx.HTTPError as exc:
            return DeliveryReceipt.failed("network_error", str(exc), retryable=True)

        if response.status_code >= 400:
            detail = response.text[:500]
            # 429 llega con `retry_after`; el reintento del despachador ya
            # espera con retroceso, así que basta con marcarlo reintentable.
            retryable = response.status_code == 429 or response.status_code >= 500
            return DeliveryReceipt.failed(
                f"http_{response.status_code}", detail, retryable=retryable
            )

        data = response.json()
        result = data.get("result") or {}
        chat_id = (result.get("chat") or {}).get("id")
        provider_id = (
            f"{chat_id}:{result.get('message_id')}" if result.get("message_id") else None
        )
        return DeliveryReceipt.sent(provider_id, **data)

    async def set_typing(self, *, ref: ConversationRef) -> None:
        token = ref.extra.get("credentials", {}).get("access_token")
        if not token:
            return
        await self._call(
            token,
            "sendChatAction",
            {"chat_id": ref.channel_conversation_id, "action": "typing"},
        )

    # ------------------------------------------------------------ alta del webhook
    async def register_webhook(self, *, token: str, url: str, secret: str | None) -> str | None:
        """Declara el webhook ante Telegram. Devuelve el error, o ``None``.

        Telegram no tiene panel: el webhook se da de alta llamando a la propia
        API. Sin esto habría que salir de la consola y componer un ``curl`` a
        mano con el token dentro.
        """
        body: dict[str, Any] = {"url": url, "drop_pending_updates": True}
        if secret:
            body["secret_token"] = secret
        try:
            response = await self._client.post(f"/bot{token}/setWebhook", json=body)
        except httpx.HTTPError as exc:
            return str(exc)
        if response.status_code >= 400:
            return response.text[:500]
        data = response.json()
        return None if data.get("ok") else str(data.get("description") or data)


#: Método y campo de la Bot API para cada tipo de adjunto.
_METHOD_FOR_ATTACHMENT: dict[ContentType, tuple[str, str]] = {
    ContentType.IMAGE: ("sendPhoto", "photo"),
    ContentType.VIDEO: ("sendVideo", "video"),
    ContentType.AUDIO: ("sendAudio", "audio"),
    ContentType.DOCUMENT: ("sendDocument", "document"),
    ContentType.STICKER: ("sendSticker", "sticker"),
}


def _keyboard(quick_replies: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    """Reparte las respuestas rápidas en filas de dos botones.

    ``callback_data`` no admite más de 64 bytes; se recorta antes de mandarlo
    porque Telegram rechaza la llamada entera si se pasa.
    """
    botones = [
        {
            "text": option.get("title", "")[:64],
            "callback_data": (option.get("id") or option.get("title", ""))[:64],
        }
        for option in quick_replies
    ]
    return [
        botones[index : index + BUTTONS_PER_ROW]
        for index in range(0, len(botones), BUTTONS_PER_ROW)
    ]


def _from_epoch(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)
