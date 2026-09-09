"""Adaptador de SMS a través de Twilio.

Twilio no manda JSON: manda un formulario codificado como el de una página web,
y firma la petición con un HMAC-SHA1 que abarca **la URL completa más los
campos del formulario ordenados**, no solo el cuerpo. Por eso
``ChannelAdapter.verify_request`` recibe ``url``: sin ella no hay forma de
comprobar la firma. La reconstruye ``app/api/webhooks.py`` a partir de
``PUBLIC_BASE_URL``, que es la dirección que se dio de alta en el panel de
Twilio; detrás del proxy inverso, la que ve la aplicación es la interna.

Entrada: webhook ``POST /webhooks/sms``.
Salida:  ``POST /2010-04-01/Accounts/{sid}/Messages.json`` con autenticación
básica.
"""

from __future__ import annotations

import base64
import hashlib
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
    DeliveryStatus,
    InboundMessage,
    OutboundMessage,
    Party,
)
from app.logging_setup import get_logger

log = get_logger(__name__)

API_BASE = "https://api.twilio.com"
API_VERSION = "2010-04-01"

#: Un SMS son 160 caracteres; Twilio concatena hasta 1600 y los cobra por
#: tramos. Pasado ese punto rechaza el envío, así que se parte antes.
TEXT_LIMIT = 1_600

#: Cabecera con la firma de Twilio.
SIGNATURE_HEADER = "x-twilio-signature"

#: Estados de entrega que Twilio devuelve por la devolución de llamada.
_STATUS_MAP: dict[str, DeliveryStatus] = {
    "queued": DeliveryStatus.PENDING,
    "sending": DeliveryStatus.PENDING,
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "read": DeliveryStatus.READ,
    "undelivered": DeliveryStatus.FAILED,
    "failed": DeliveryStatus.FAILED,
}


@register_channel(ChannelKind.SMS)
class TwilioAdapter(ChannelAdapter):
    """Traductor entre la API de mensajes de Twilio y el formato canónico."""

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
        """Comprueba ``X-Twilio-Signature``.

        Vale el testigo de cualquier cuenta dada de alta: la firma se comprueba
        antes de leer el cuerpo, de modo que todavía no se sabe de qué número
        viene el mensaje.
        """
        testigos = [c["auth_token"] for c in credentials if c.get("auth_token")]
        if not testigos:
            if self.settings.environment == "prod":
                raise SignatureError(
                    "Configure el token de autenticación de Twilio en la consola"
                )
            log.warning("twilio_signature_skipped", reason="auth_token_no_configurado")
            return

        recibida = headers.get(SIGNATURE_HEADER) or headers.get("X-Twilio-Signature")
        if not recibida:
            raise SignatureError("Falta la cabecera X-Twilio-Signature")
        if not url:
            raise SignatureError("No se pudo determinar la dirección de la petición")

        firmado = _signed_payload(url, body)
        for testigo in testigos:
            digest = hmac.new(testigo.encode(), firmado.encode("utf-8"), hashlib.sha1).digest()
            if hmac.compare_digest(base64.b64encode(digest).decode(), recibida):
                return
        raise SignatureError("La firma de Twilio no coincide")

    async def parse(
        self, *, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        sid = payload.get("MessageSid") or payload.get("SmsSid")
        if not sid:
            return []

        # La misma URL recibe los mensajes entrantes y los acuses de los
        # salientes; se distinguen por traer o no `MessageStatus`.
        if estado := payload.get("MessageStatus") or payload.get("SmsStatus"):
            if not payload.get("Body") and "From" not in payload:
                return [self._parse_status(payload, sid, estado)]
            if estado in {"sent", "delivered", "undelivered", "failed"}:
                return [self._parse_status(payload, sid, estado)]

        remitente = payload.get("From")
        if not remitente:
            return []

        return [
            InboundMessage(
                channel=ChannelKind.SMS,
                conversation=self._ref(remitente, payload.get("To")),
                sender=Party(
                    channel_user_id=remitente,
                    phone=remitente,
                    raw={"city": payload.get("FromCity"), "country": payload.get("FromCountry")},
                ),
                provider_message_id=sid,
                content_type=ContentType.TEXT,
                text=payload.get("Body"),
                attachments=_media(payload),
                timestamp=datetime.now(UTC),
                raw=payload,
            )
        ]

    def _ref(self, remitente: str, destino: str | None) -> ConversationRef:
        return ConversationRef(
            channel=ChannelKind.SMS,
            channel_conversation_id=remitente,
            # El número propio es lo que identifica la cuenta, igual que el
            # phone_number_id en WhatsApp o el id de página en Facebook.
            channel_account_id=destino,
        )

    def _parse_status(
        self, payload: dict[str, Any], sid: str, estado: str
    ) -> InboundMessage:
        """Acuse de entrega, convertido en un evento de sistema."""
        return InboundMessage(
            channel=ChannelKind.SMS,
            conversation=self._ref(payload.get("To") or "", payload.get("From")),
            sender=Party(channel_user_id=payload.get("To") or ""),
            provider_message_id=f"status:{sid}:{estado}",
            content_type=ContentType.SYSTEM,
            action={
                "kind": "delivery_status",
                "target_provider_message_id": sid,
                "status": str(_STATUS_MAP.get(estado, DeliveryStatus.SENT)),
                "provider_status": estado,
            },
            timestamp=datetime.now(UTC),
            raw=payload,
        )

    # ---------------------------------------------------------------- salida
    async def send(
        self, *, ref: ConversationRef, message: OutboundMessage
    ) -> DeliveryReceipt:
        credentials = ref.extra.get("credentials", {})
        sid = credentials.get("account_sid")
        token = credentials.get("auth_token")
        remitente = credentials.get("from_number")
        if not (sid and token and remitente):
            return DeliveryReceipt.failed(
                "not_configured",
                "Faltan los datos de Twilio; complételos en «Cuentas de canal»",
            )

        last: DeliveryReceipt | None = None
        for body in _build_payloads(ref.channel_conversation_id, remitente, message):
            last = await self._post(sid, token, body)
            if not last.ok:
                return last
        return last or DeliveryReceipt.failed("empty_message", "Nada que enviar")

    async def _post(self, sid: str, token: str, body: dict[str, Any]) -> DeliveryReceipt:
        try:
            response = await self._client.post(
                f"/{API_VERSION}/Accounts/{sid}/Messages.json",
                data=body,
                auth=(sid, token),
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
        return DeliveryReceipt.sent(data.get("sid"), **data)


# --------------------------------------------------------------------------- #
# Firma
# --------------------------------------------------------------------------- #
def _signed_payload(url: str, body: bytes) -> str:
    """Reconstruye la cadena que Twilio firma: la URL más los campos ordenados.

    Se ordenan por nombre y se concatenan sin separador alguno, nombre pegado a
    valor. Es un formato peculiar, pero es el que documenta Twilio y cualquier
    desviación da una firma distinta.
    """
    from urllib.parse import parse_qsl

    campos = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
    return url + "".join(f"{nombre}{valor}" for nombre, valor in sorted(campos))


# --------------------------------------------------------------------------- #
# Traducción
# --------------------------------------------------------------------------- #
def _media(payload: dict[str, Any]) -> list[Attachment]:
    """Adjuntos de un MMS. Twilio los numera en campos sueltos."""
    try:
        cuantos = int(payload.get("NumMedia") or 0)
    except ValueError:
        return []

    adjuntos = []
    for indice in range(cuantos):
        url = payload.get(f"MediaUrl{indice}")
        if not url:
            continue
        mime = payload.get(f"MediaContentType{indice}") or ""
        adjuntos.append(
            Attachment(content_type=_content_type_for(mime), url=url, mime_type=mime or None)
        )
    return adjuntos


def _content_type_for(mime: str) -> ContentType:
    if mime.startswith("image/"):
        return ContentType.IMAGE
    if mime.startswith("video/"):
        return ContentType.VIDEO
    if mime.startswith("audio/"):
        return ContentType.AUDIO
    return ContentType.DOCUMENT


def _build_payloads(
    destino: str, remitente: str, message: OutboundMessage
) -> list[dict[str, Any]]:
    texto = (message.text or "").strip()
    if message.quick_replies:
        # Un SMS no tiene botones. Se ofrecen numerados, igual que hace
        # WhatsApp cuando se pasa de tres opciones y cambia a lista.
        opciones = "\n".join(
            f"{indice}. {opcion.get('title', '')}"
            for indice, opcion in enumerate(message.quick_replies, start=1)
        )
        texto = f"{texto}\n{opciones}".strip()

    # Twilio admite un solo MediaUrl por llamada, así que cada adjunto va en la
    # suya; el primero viaja con el texto para no mandar un SMS vacío detrás.
    urls = [a.url for a in message.attachments if a.url]
    payloads: list[dict[str, Any]] = []

    if texto:
        trozos = split_text(texto, TEXT_LIMIT)
        for indice, trozo in enumerate(trozos):
            cuerpo: dict[str, Any] = {"To": destino, "From": remitente, "Body": trozo}
            if indice == 0 and urls:
                cuerpo["MediaUrl"] = urls.pop(0)
            payloads.append(cuerpo)
    for url in urls:
        payloads.append({"To": destino, "From": remitente, "MediaUrl": url})
    return payloads
