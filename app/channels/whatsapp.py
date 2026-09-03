"""Adaptador de WhatsApp Cloud API (Meta Graph API).

Entrada: webhook ``POST`` con firma ``X-Hub-Signature-256``.
Salida:  ``POST /{phone_number_id}/messages``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
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

#: Límite de caracteres del cuerpo de un mensaje de texto en WhatsApp.
TEXT_LIMIT = 4_096
#: Número máximo de botones de respuesta rápida; por encima se usa una lista.
MAX_REPLY_BUTTONS = 3

_MEDIA_TYPES: dict[str, ContentType] = {
    "image": ContentType.IMAGE,
    "audio": ContentType.AUDIO,
    "voice": ContentType.AUDIO,
    "video": ContentType.VIDEO,
    "document": ContentType.DOCUMENT,
    "sticker": ContentType.STICKER,
}

_STATUS_MAP: dict[str, DeliveryStatus] = {
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "read": DeliveryStatus.READ,
    "failed": DeliveryStatus.FAILED,
}


class TemplatesUnavailable(RuntimeError):
    """No se pudo obtener la lista de plantillas de la cuenta."""


def _count_placeholders(text: str) -> int:
    """Cuenta los huecos ``{{1}}``, ``{{2}}``... de una plantilla.

    Se devuelve el mayor indice y no cuantas veces aparecen: una plantilla
    puede repetir ``{{1}}`` en dos frases y sigue necesitando un solo dato.
    """
    indices = [int(n) for n in re.findall(r"{{\s*(\d+)\s*}}", text or "")]
    return max(indices, default=0)


@register_channel(ChannelKind.WHATSAPP)
class WhatsAppAdapter(ChannelAdapter):
    """Traductor entre WhatsApp Cloud API y el formato canónico."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._client = httpx.AsyncClient(
            base_url=f"{settings.whatsapp_api_base}/{settings.whatsapp_api_version}",
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

        Meta no dice a qué número corresponde el reto —solo manda el token—, así
        que se acepta el de ``.env`` o el de cualquier cuenta dada de alta desde
        la consola. Es lo que permite tener números en aplicaciones de Meta
        distintas, cada una con su token, sin editar el fichero.
        """
        esperados = [c["verify_token"] for c in credentials if c.get("verify_token")]
        if (global_token := self.settings.whatsapp_verify_token) is not None:
            esperados.append(global_token.get_secret_value())
        if not esperados:
            raise SignatureError("WHATSAPP_VERIFY_TOKEN no está configurado")
        if params.get("hub.mode") != "subscribe":
            raise SignatureError("hub.mode no soportado")
        recibido = params.get("hub.verify_token", "")
        # `compare_digest` en todos y no un `in`: comparar cadenas con `==`
        # deja el tiempo de respuesta al descubierto y con él el token.
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
        if (global_secret := self.settings.whatsapp_app_secret) is not None:
            secretos.append(global_secret.get_secret_value())
        if not secretos:
            if self.settings.environment == "prod":
                raise SignatureError("WHATSAPP_APP_SECRET es obligatorio en producción")
            log.warning("whatsapp_signature_skipped", reason="app_secret_no_configurado")
            return

        header = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
        if not header or not header.startswith("sha256="):
            raise SignatureError("Falta la cabecera X-Hub-Signature-256")

        # La firma llega antes de poder mirar el cuerpo, de modo que aún no se
        # sabe de qué número es: vale si cuadra con cualquiera de los secretos
        # configurados. Todos ellos los dio de alta quien administra, así que
        # aceptar el que valide no rebaja la confianza en nada.
        recibida = header.removeprefix("sha256=")
        for secreto in secretos:
            digest = hmac.new(secreto.encode(), body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(digest, recibida):
                return
        raise SignatureError("La firma del cuerpo no coincide")

    async def parse(
        self, *, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        results: list[InboundMessage] = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                if value.get("messaging_product") != "whatsapp":
                    continue
                phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
                profiles = self._index_profiles(value.get("contacts") or [])

                for raw_message in value.get("messages") or []:
                    parsed = self._parse_message(raw_message, phone_number_id, profiles)
                    if parsed is not None:
                        results.append(parsed)

                for raw_status in value.get("statuses") or []:
                    results.append(self._parse_status(raw_status, phone_number_id))
        return results

    @staticmethod
    def _index_profiles(contacts: list[dict[str, Any]]) -> dict[str, str]:
        return {
            contact.get("wa_id", ""): (contact.get("profile") or {}).get("name", "")
            for contact in contacts
        }

    def _parse_message(
        self,
        raw: dict[str, Any],
        phone_number_id: str | None,
        profiles: dict[str, str],
    ) -> InboundMessage | None:
        wa_id = raw.get("from")
        message_id = raw.get("id")
        if not wa_id or not message_id:
            return None

        message_type = raw.get("type", "unknown")
        text: str | None = None
        action: dict[str, Any] | None = None
        attachments: list[Attachment] = []
        content_type = ContentType.UNKNOWN

        if message_type == "text":
            content_type = ContentType.TEXT
            text = (raw.get("text") or {}).get("body")
        elif message_type in _MEDIA_TYPES:
            content_type = _MEDIA_TYPES[message_type]
            media = raw.get(message_type) or {}
            text = media.get("caption")
            attachments.append(
                Attachment(
                    content_type=content_type,
                    provider_media_id=media.get("id"),
                    mime_type=media.get("mime_type"),
                    filename=media.get("filename"),
                    sha256=media.get("sha256"),
                    caption=media.get("caption"),
                )
            )
        elif message_type == "interactive":
            content_type = ContentType.INTERACTIVE
            interactive = raw.get("interactive") or {}
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            text = reply.get("title")
            action = {"id": reply.get("id"), "title": reply.get("title"), "source": message_type}
        elif message_type == "button":
            # Respuesta a una plantilla con botones.
            content_type = ContentType.INTERACTIVE
            button = raw.get("button") or {}
            text = button.get("text")
            action = {"id": button.get("payload"), "title": button.get("text")}
        elif message_type == "location":
            content_type = ContentType.LOCATION
            location = raw.get("location") or {}
            text = location.get("name") or location.get("address")
            action = {
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
            }
        elif message_type == "reaction":
            content_type = ContentType.REACTION
            reaction = raw.get("reaction") or {}
            text = reaction.get("emoji")
            action = {"target_message_id": reaction.get("message_id")}

        return InboundMessage(
            channel=ChannelKind.WHATSAPP,
            conversation=ConversationRef(
                channel=ChannelKind.WHATSAPP,
                channel_conversation_id=wa_id,
                channel_account_id=phone_number_id,
                reply_to_message_id=message_id,
                extra={"wa_id": wa_id},
            ),
            sender=Party(
                channel_user_id=wa_id,
                display_name=profiles.get(wa_id) or None,
                phone=f"+{wa_id}",
                raw={"wa_id": wa_id},
            ),
            provider_message_id=message_id,
            content_type=content_type,
            text=text,
            attachments=attachments,
            action=action,
            timestamp=_from_epoch(raw.get("timestamp")),
            raw=raw,
        )

    def _parse_status(self, raw: dict[str, Any], phone_number_id: str | None) -> InboundMessage:
        """Convierte un acuse de recibo en un mensaje canónico de tipo sistema."""
        recipient = raw.get("recipient_id", "")
        status_name = raw.get("status", "")
        errors = raw.get("errors") or []
        return InboundMessage(
            channel=ChannelKind.WHATSAPP,
            conversation=ConversationRef(
                channel=ChannelKind.WHATSAPP,
                channel_conversation_id=recipient,
                channel_account_id=phone_number_id,
            ),
            sender=Party(channel_user_id=recipient, phone=f"+{recipient}" if recipient else None),
            provider_message_id=f"status:{raw.get('id')}:{status_name}",
            content_type=ContentType.SYSTEM,
            action={
                "kind": "delivery_status",
                "target_provider_message_id": raw.get("id"),
                "status": str(_STATUS_MAP.get(status_name, DeliveryStatus.SENT)),
                "provider_status": status_name,
                "error_code": str(errors[0].get("code")) if errors else None,
                "error_detail": errors[0].get("title") if errors else None,
            },
            timestamp=_from_epoch(raw.get("timestamp")),
            raw=raw,
        )

    # ---------------------------------------------------------------- salida
    def _resolve_token(self, ref: ConversationRef) -> str | None:
        """Token propio de la cuenta si lo tiene; si no, el global de ``.env``.

        Cubre el caso común de un solo token de sistema para varios números
        de la misma cuenta de WhatsApp Business, sin obligar a dar de alta
        una credencial por número.
        """
        account_token = ref.extra.get("credentials", {}).get("access_token")
        if account_token:
            return account_token
        token = self.settings.whatsapp_access_token
        return token.get_secret_value() if token else None

    async def list_templates(self) -> list[dict[str, Any]]:
        """Plantillas aprobadas de la cuenta, listas para elegir en la consola.

        Solo se devuelven las aprobadas: una plantilla pendiente o rechazada la
        rechaza la propia Graph API al enviarla, y ofrecerla seria enseñar algo
        que no funciona.

        De cada una se extrae el numero de huecos del cuerpo. La consola lo usa
        para pedir exactamente esos datos: mandar una plantilla con menos
        parametros de los que declara falla, y con mas tambien.
        """
        token = self.settings.whatsapp_access_token
        waba = self.settings.whatsapp_waba_id
        if token is None or not waba:
            raise TemplatesUnavailable(
                "Faltan WHATSAPP_ACCESS_TOKEN o WHATSAPP_WABA_ID"
            )
        response = await self._client.get(
            f"/{waba}/message_templates",
            params={"fields": "name,status,category,language,components", "limit": 100},
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        )
        if response.status_code >= 400:
            detalle = (response.json().get("error") or {}).get("message", response.text[:200])
            raise TemplatesUnavailable(detalle)

        plantillas = []
        for row in response.json().get("data", []):
            if row.get("status") != "APPROVED":
                continue
            cuerpo = next(
                (c for c in row.get("components", []) if c.get("type") == "BODY"), {}
            )
            texto = cuerpo.get("text", "")
            plantillas.append(
                {
                    "name": row.get("name"),
                    "language": row.get("language"),
                    "category": row.get("category"),
                    "body": texto,
                    "variables": _count_placeholders(texto),
                }
            )
        return plantillas

    async def send(
        self, *, ref: ConversationRef, message: OutboundMessage
    ) -> DeliveryReceipt:
        token = self._resolve_token(ref)
        phone_number_id = ref.channel_account_id or self.settings.whatsapp_phone_number_id
        if token is None or not phone_number_id:
            return DeliveryReceipt.failed(
                "not_configured",
                "Faltan WHATSAPP_ACCESS_TOKEN o el identificador de número emisor",
            )

        # Los adjuntos propios se suben antes a Meta y se mandan por su
        # identificador. Ver `_ensure_media_ids` para el porqué.
        fallo = await self._ensure_media_ids(phone_number_id, token, message)
        if fallo is not None:
            return fallo

        last: DeliveryReceipt | None = None
        for body in self._build_payloads(ref.channel_conversation_id, message):
            last = await self._post(phone_number_id, token, body)
            if not last.ok:
                return last
        return last or DeliveryReceipt.failed("empty_message", "Nada que enviar")

    def _build_payloads(
        self, to: str, message: OutboundMessage
    ) -> list[dict[str, Any]]:
        """Descompone el mensaje canónico en una o varias cargas de la Graph API."""
        if template := message.channel_data.get("template"):
            return [
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": "template",
                    "template": template,
                }
            ]

        payloads: list[dict[str, Any]] = []
        for attachment in message.attachments:
            media_type = _reverse_media_type(attachment.content_type)
            media: dict[str, Any] = {}
            if attachment.provider_media_id:
                media["id"] = attachment.provider_media_id
            elif attachment.url:
                media["link"] = attachment.url
            else:
                continue
            if attachment.caption and media_type in {"image", "video", "document"}:
                media["caption"] = attachment.caption
            if attachment.filename and media_type == "document":
                media["filename"] = attachment.filename
            payloads.append(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": media_type,
                    media_type: media,
                }
            )

        text = (message.text or "").strip()
        if text and message.quick_replies:
            payloads.append(self._build_interactive(to, text, message.quick_replies))
        elif text:
            payloads.extend(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": "text",
                    "text": {"preview_url": False, "body": chunk},
                }
                for chunk in _split_text(text, TEXT_LIMIT)
            )
        return payloads

    @staticmethod
    def _build_interactive(
        to: str, text: str, quick_replies: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Botones si caben tres o menos; en caso contrario, lista desplegable."""
        base = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
        }
        body = {"text": text[:1024]}
        if len(quick_replies) <= MAX_REPLY_BUTTONS:
            return base | {
                "interactive": {
                    "type": "button",
                    "body": body,
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {
                                    "id": option.get("id", str(index)),
                                    "title": option.get("title", "")[:20],
                                },
                            }
                            for index, option in enumerate(quick_replies[:MAX_REPLY_BUTTONS])
                        ]
                    },
                }
            }
        return base | {
            "interactive": {
                "type": "list",
                "body": body,
                "action": {
                    "button": "Ver opciones",
                    "sections": [
                        {
                            "title": "Opciones",
                            "rows": [
                                {
                                    "id": option.get("id", str(index)),
                                    "title": option.get("title", "")[:24],
                                    "description": option.get("description", "")[:72],
                                }
                                for index, option in enumerate(quick_replies[:10])
                            ],
                        }
                    ],
                },
            }
        }

    async def fetch_media(
        self, *, attachment: Attachment, ref: ConversationRef
    ) -> tuple[bytes, str | None] | None:
        """Descarga un adjunto entrante de WhatsApp.

        Son dos pasos y no uno: primero se pide la ficha del medio, que
        responde con una dirección temporal, y luego se descarga esa dirección
        con el mismo token. La dirección caduca en minutos y el fichero
        desaparece del lado de Meta a los pocos días, así que se guarda aquí en
        cuanto llega; si se dejara para cuando alguien abra la conversación,
        buena parte de los adjuntos ya no estarían.
        """
        if not attachment.provider_media_id:
            return None
        token = self._resolve_token(ref)
        if token is None:
            return None
        cabeceras = {"Authorization": f"Bearer {token}"}
        try:
            ficha = await self._client.get(f"/{attachment.provider_media_id}", headers=cabeceras)
            if ficha.status_code >= 400:
                log.warning(
                    "whatsapp_media_lookup_failed",
                    media_id=attachment.provider_media_id,
                    status=ficha.status_code,
                )
                return None
            datos = ficha.json()
            enlace = datos.get("url")
            if not enlace:
                return None
            # La dirección es absoluta y de otro dominio (`lookaside.fbsbx.com`),
            # así que no puede ir por el cliente, que lleva la base de la Graph API.
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as cliente:
                descarga = await cliente.get(enlace, headers=cabeceras)
            if descarga.status_code >= 400:
                log.warning(
                    "whatsapp_media_download_failed",
                    media_id=attachment.provider_media_id,
                    status=descarga.status_code,
                )
                return None
        except httpx.HTTPError as exc:
            log.warning("whatsapp_media_error", error=str(exc))
            return None
        return descarga.content, datos.get("mime_type") or attachment.mime_type

    async def _ensure_media_ids(
        self, phone_number_id: str, token: str, message: OutboundMessage
    ) -> DeliveryReceipt | None:
        """Sube a Meta los adjuntos guardados aquí y les pone su identificador.

        La Graph API admite dos formas de mandar una imagen: un ``link`` que
        ella misma descarga, o el ``id`` de un fichero subido antes. El enlace
        no sirve para los adjuntos propios, por dos motivos independientes:

        1. La ``url`` que guarda el sistema es relativa —``/uploads/...``—, y
           Meta no tiene forma de resolverla.
        2. Aunque se compusiera la dirección pública completa, esa ruta exige
           sesión desde que los adjuntos dejaron de servirse como estáticos
           (ver ``app/api/attachments.py``). Quien descarga es un servidor de
           Meta, sin sesión: recibiría un 401.

        Por eso se sube el fichero y se manda por ``id``. Devuelve ``None`` si
        todo fue bien, o el acuse de fallo que corresponda.
        """
        for attachment in message.attachments:
            if attachment.provider_media_id or not attachment.url:
                continue
            # Un adjunto con dirección absoluta viene de fuera y Meta sí puede
            # descargarlo; se deja como enlace.
            if not attachment.url.startswith("/uploads/"):
                continue

            ruta = self._local_path(attachment.url)
            if ruta is None or not ruta.is_file():
                return DeliveryReceipt.failed(
                    "missing_attachment",
                    f"No se encontró el fichero del adjunto: {attachment.url}",
                )
            try:
                with ruta.open("rb") as fichero:
                    response = await self._client.post(
                        f"/{phone_number_id}/media",
                        data={"messaging_product": "whatsapp"},
                        files={
                            "file": (
                                ruta.name,
                                fichero,
                                attachment.mime_type or "application/octet-stream",
                            )
                        },
                        headers={"Authorization": f"Bearer {token}"},
                    )
            except (httpx.HTTPError, OSError) as exc:
                return DeliveryReceipt.failed("media_upload_error", str(exc), retryable=True)

            if response.status_code >= 400:
                retryable = response.status_code == 429 or response.status_code >= 500
                return DeliveryReceipt.failed(
                    f"media_http_{response.status_code}",
                    response.text[:500],
                    retryable=retryable,
                )
            attachment.provider_media_id = response.json().get("id")
        return None

    def _local_path(self, url: str) -> Path | None:
        """Traduce ``/uploads/{inquilino}/{fichero}`` a su ruta en disco.

        Se exige exactamente esa forma y se rechaza cualquier segmento raro: la
        dirección sale de la base, pero componer una ruta a partir de texto sin
        comprobarlo es como se llega a leer ficheros de fuera del directorio.
        """
        partes = url.split("/")
        if len(partes) != 4 or partes[0] != "" or partes[1] != "uploads":
            return None
        if any(parte in ("", ".", "..") or "\\" in parte for parte in partes[2:]):
            return None
        return Path(self.settings.uploads_dir) / partes[2] / partes[3]

    async def _post(
        self, phone_number_id: str, token: str, body: dict[str, Any]
    ) -> DeliveryReceipt:
        try:
            response = await self._client.post(
                f"/{phone_number_id}/messages",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            return DeliveryReceipt.failed("network_error", str(exc), retryable=True)

        if response.status_code >= 400:
            detail = response.text[:500]
            # 4xx salvo 429 indica una carga inválida: reintentar no ayuda.
            retryable = response.status_code == 429 or response.status_code >= 500
            return DeliveryReceipt.failed(
                f"http_{response.status_code}", detail, retryable=retryable
            )

        data = response.json()
        provider_id = ((data.get("messages") or [{}])[0]).get("id")
        return DeliveryReceipt.sent(provider_id, **data)

    async def set_typing(self, *, ref: ConversationRef) -> None:
        token = self._resolve_token(ref)
        phone_number_id = ref.channel_account_id or self.settings.whatsapp_phone_number_id
        if token is None or not phone_number_id or not ref.reply_to_message_id:
            return
        await self._post(
            phone_number_id,
            token,
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": ref.reply_to_message_id,
                "typing_indicator": {"type": "text"},
            },
        )

    async def mark_read(self, *, ref: ConversationRef, provider_message_id: str) -> None:
        token = self._resolve_token(ref)
        phone_number_id = ref.channel_account_id or self.settings.whatsapp_phone_number_id
        if token is None or not phone_number_id:
            return
        await self._post(
            phone_number_id,
            token,
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": provider_message_id,
            },
        )


def _reverse_media_type(content_type: ContentType) -> str:
    for name, mapped in _MEDIA_TYPES.items():
        if mapped is content_type and name != "voice":
            return name
    return "document"


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
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)
