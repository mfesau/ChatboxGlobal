"""Adaptador del SDK de Microsoft (Bot Framework / Azure Bot Service).

Cubre todos los canales que expone el servicio: Microsoft Teams, Web Chat,
Direct Line, correo, SMS y otros. El protocolo se implementa directamente sobre
el esquema *Activity* v3, lo que evita arrastrar ``botbuilder`` y su propia pila
web dentro de un servicio FastAPI.

Entrada: ``POST /api/messages`` con una *Activity* y un JWT emitido por el
         servicio de canales, validado contra los metadatos OpenID.
Salida:  ``POST {serviceUrl}v3/conversations/{id}/activities`` autenticado con
         un token de cliente de Microsoft Entra ID.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

import httpx
import jwt
from jwt import PyJWKClient

from app.channels.base import ChannelAdapter, ChannelError, SignatureError, register_channel
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

OPENID_CONFIG_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
BOT_FRAMEWORK_ISSUERS = frozenset(
    {
        "https://api.botframework.com",
        "https://api.botframework.us",
    }
)
#: Ámbito OAuth de la Connector API; no es un secreto.
CONNECTOR_SCOPE = "https://api.botframework.com/.default"  # noqa: S105
#: Margen de seguridad antes de renovar el token de cliente.
TOKEN_SKEW_S = 300

_MIME_PREFIX_TO_CONTENT: tuple[tuple[str, ContentType], ...] = (
    ("image/", ContentType.IMAGE),
    ("audio/", ContentType.AUDIO),
    ("video/", ContentType.VIDEO),
)


@register_channel(ChannelKind.MSBOT)
class MicrosoftBotAdapter(ChannelAdapter):
    """Traductor entre las *Activities* del Bot Framework y el formato canónico."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        self._jwk_client: PyJWKClient | None = None
        self._jwk_lock = asyncio.Lock()
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    # --------------------------------------------------------------- entrada
    async def verify_request(self, *, headers: Mapping[str, str], body: bytes) -> None:
        """Valida el JWT del canal.

        El servicio de canales firma cada llamada; sin esta comprobación
        cualquiera podría inyectar *Activities* en el endpoint público.
        """
        if not self.settings.microsoft_validate_jwt:
            log.warning("msbot_jwt_validation_disabled")
            return

        app_id = self.settings.microsoft_app_id
        if not app_id:
            raise SignatureError("MICROSOFT_APP_ID no está configurado")

        header = headers.get("authorization") or headers.get("Authorization") or ""
        if not header.lower().startswith("bearer "):
            raise SignatureError("Falta la cabecera Authorization: Bearer")
        token = header.split(" ", 1)[1].strip()

        try:
            # Se inspecciona la cabecera antes de descargar las claves: un token
            # mal formado se rechaza sin gastar una llamada de red.
            algorithm = jwt.get_unverified_header(token).get("alg")
            if algorithm != "RS256":
                raise SignatureError(f"Algoritmo de firma no admitido: {algorithm}")

            jwk_client = await self._get_jwk_client()
            signing_key = await asyncio.to_thread(jwk_client.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=app_id,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise SignatureError(f"JWT no válido: {exc}") from exc

        if claims.get("iss") not in BOT_FRAMEWORK_ISSUERS:
            raise SignatureError(f"Emisor inesperado: {claims.get('iss')}")

    async def _get_jwk_client(self) -> PyJWKClient:
        if self._jwk_client is not None:
            return self._jwk_client
        async with self._jwk_lock:
            if self._jwk_client is None:
                response = await self._client.get(OPENID_CONFIG_URL)
                response.raise_for_status()
                jwks_uri = response.json()["jwks_uri"]
                # ``PyJWKClient`` almacena y renueva las claves por su cuenta.
                self._jwk_client = PyJWKClient(jwks_uri, cache_keys=True, lifespan=3_600)
        return self._jwk_client

    async def parse(
        self, *, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        activity_type = payload.get("type")
        conversation = payload.get("conversation") or {}
        conversation_id = conversation.get("id")
        if not conversation_id:
            return []

        sender = payload.get("from") or {}
        recipient = payload.get("recipient") or {}
        activity_id = payload.get("id") or f"{conversation_id}:{time.time_ns()}"

        # En un canal/equipo de Teams, `channelData.team.id` identifica al
        # equipo — no al bot, que es el mismo para todos—, así que es lo que
        # se usa para poder conectar "tantos equipos de Teams como se quiera"
        # por departamento (ver ChannelAccount.department_id). En un chat 1:1,
        # Web Chat o Direct Line no hay equipo: se mantiene el comportamiento
        # de siempre, sin acotar por departamento.
        team_id = ((payload.get("channelData") or {}).get("team") or {}).get("id")
        ref = ConversationRef(
            channel=ChannelKind.MSBOT,
            channel_conversation_id=conversation_id,
            channel_account_id=team_id or recipient.get("id") or self.settings.microsoft_app_id,
            service_url=payload.get("serviceUrl"),
            reply_to_message_id=activity_id,
            extra={
                "channel_id": payload.get("channelId"),
                "conversation_type": conversation.get("conversationType"),
                "aad_tenant_id": (payload.get("channelData") or {})
                .get("tenant", {})
                .get("id")
                or conversation.get("tenantId"),
                "bot": recipient,
                "locale": payload.get("locale"),
            },
        )
        party = Party(
            channel_user_id=sender.get("id", ""),
            display_name=sender.get("name"),
            aad_object_id=sender.get("aadObjectId"),
            email=sender.get("email"),
            locale=payload.get("locale"),
            raw=sender,
        )

        if activity_type == "message":
            return [
                InboundMessage(
                    channel=ChannelKind.MSBOT,
                    conversation=ref,
                    sender=party,
                    provider_message_id=activity_id,
                    content_type=self._content_type(payload),
                    text=_strip_mentions(payload),
                    attachments=self._parse_attachments(payload),
                    action=payload.get("value") if payload.get("value") else None,
                    timestamp=_parse_timestamp(payload.get("timestamp")),
                    raw=payload,
                )
            ]

        if activity_type in {"conversationUpdate", "installationUpdate", "endOfConversation"}:
            return [
                InboundMessage(
                    channel=ChannelKind.MSBOT,
                    conversation=ref,
                    sender=party,
                    provider_message_id=activity_id,
                    content_type=ContentType.SYSTEM,
                    action={"kind": activity_type, "payload": payload},
                    timestamp=_parse_timestamp(payload.get("timestamp")),
                    raw=payload,
                )
            ]

        if activity_type == "invoke":
            # Acciones de tarjeta adaptativa y submit de Teams.
            return [
                InboundMessage(
                    channel=ChannelKind.MSBOT,
                    conversation=ref,
                    sender=party,
                    provider_message_id=activity_id,
                    content_type=ContentType.INTERACTIVE,
                    action={
                        "kind": "invoke",
                        "name": payload.get("name"),
                        "value": payload.get("value"),
                    },
                    timestamp=_parse_timestamp(payload.get("timestamp")),
                    raw=payload,
                )
            ]

        # ``typing``, ``messageReaction`` y demás no requieren respuesta.
        return []

    def _content_type(self, payload: dict[str, Any]) -> ContentType:
        attachments = payload.get("attachments") or []
        if payload.get("text"):
            return ContentType.TEXT
        if not attachments:
            return ContentType.INTERACTIVE if payload.get("value") else ContentType.UNKNOWN
        return _content_type_for_mime(attachments[0].get("contentType", ""))

    def _parse_attachments(self, payload: dict[str, Any]) -> list[Attachment]:
        parsed: list[Attachment] = []
        for attachment in payload.get("attachments") or []:
            mime = attachment.get("contentType", "")
            if mime.startswith("application/vnd.microsoft.card"):
                # Las tarjetas no son ficheros; se conservan en ``raw``.
                continue
            parsed.append(
                Attachment(
                    content_type=_content_type_for_mime(mime),
                    url=attachment.get("contentUrl"),
                    mime_type=mime or None,
                    filename=attachment.get("name"),
                )
            )
        return parsed

    # ---------------------------------------------------------------- salida
    async def send(
        self, *, ref: ConversationRef, message: OutboundMessage
    ) -> DeliveryReceipt:
        if not ref.service_url:
            return DeliveryReceipt.failed(
                "missing_service_url",
                "La conversación no conserva serviceUrl; no es posible responder",
            )
        try:
            token = await self._get_token()
        except ChannelError as exc:
            return DeliveryReceipt.failed(exc.code, str(exc), retryable=exc.retryable)

        activity = self._build_activity(ref, message)
        url = urljoin(
            _ensure_trailing_slash(ref.service_url),
            f"v3/conversations/{ref.channel_conversation_id}/activities",
        )
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = await self._client.post(url, json=activity, headers=headers)
        except httpx.HTTPError as exc:
            return DeliveryReceipt.failed("network_error", str(exc), retryable=True)

        if response.status_code == 401:
            # El token pudo caducar entre la comprobación y el envío: se fuerza renovación.
            self._token = None
            self._token_expires_at = 0.0
            return DeliveryReceipt.failed("unauthorized", response.text[:500], retryable=True)
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            return DeliveryReceipt.failed(
                f"http_{response.status_code}", response.text[:500], retryable=retryable
            )

        data = response.json() if response.content else {}
        return DeliveryReceipt.sent(data.get("id"), **data)

    def _build_activity(
        self, ref: ConversationRef, message: OutboundMessage
    ) -> dict[str, Any]:
        activity: dict[str, Any] = {
            "type": "message",
            "textFormat": message.channel_data.get("text_format", "markdown"),
            "conversation": {"id": ref.channel_conversation_id},
            "from": ref.extra.get("bot") or {"id": self.settings.microsoft_app_id or "bot"},
            "channelId": ref.extra.get("channel_id"),
        }
        if message.text:
            activity["text"] = message.text
        if ref.reply_to_message_id:
            activity["replyToId"] = ref.reply_to_message_id
        if locale := ref.extra.get("locale"):
            activity["locale"] = locale

        attachments: list[dict[str, Any]] = [
            {
                "contentType": attachment.mime_type or "application/octet-stream",
                "contentUrl": attachment.url,
                "name": attachment.filename,
            }
            for attachment in message.attachments
            if attachment.url
        ]
        if card := message.channel_data.get("adaptive_card"):
            attachments.append(
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            )
        if attachments:
            activity["attachments"] = attachments

        if message.quick_replies:
            activity["suggestedActions"] = {
                "actions": [
                    {
                        "type": "imBack",
                        "title": option.get("title", ""),
                        "value": option.get("id") or option.get("title", ""),
                    }
                    for option in message.quick_replies
                ]
            }
        return activity

    async def set_typing(self, *, ref: ConversationRef) -> None:
        if not ref.service_url:
            return
        try:
            token = await self._get_token()
        except ChannelError:
            return
        url = urljoin(
            _ensure_trailing_slash(ref.service_url),
            f"v3/conversations/{ref.channel_conversation_id}/activities",
        )
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            await self._client.post(
                url,
                json={
                    "type": "typing",
                    "conversation": {"id": ref.channel_conversation_id},
                    "from": ref.extra.get("bot") or {"id": self.settings.microsoft_app_id or "bot"},
                },
                headers=headers,
            )
        except httpx.HTTPError:  # el indicador de escritura es prescindible
            log.debug("msbot_typing_failed", conversation=ref.channel_conversation_id)

    # ----------------------------------------------------------- credenciales
    async def _get_token(self) -> str | None:
        """Obtiene y almacena el token de cliente de Microsoft Entra ID.

        Devuelve ``None`` cuando no hay credenciales configuradas, situación
        válida únicamente frente al Bot Framework Emulator en desarrollo.
        """
        app_id = self.settings.microsoft_app_id
        secret = self.settings.microsoft_app_password
        if not app_id or secret is None:
            if self.settings.environment == "prod":
                raise ChannelError(
                    "MICROSOFT_APP_ID y MICROSOFT_APP_PASSWORD son obligatorios",
                    code="not_configured",
                )
            return None

        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        async with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token

            tenant = (
                self.settings.microsoft_app_tenant_id
                if self.settings.microsoft_app_type == "SingleTenant"
                else "botframework.com"
            )
            if not tenant:
                raise ChannelError(
                    "MICROSOFT_APP_TENANT_ID es obligatorio en modo SingleTenant",
                    code="not_configured",
                )

            try:
                response = await self._client.post(
                    f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": app_id,
                        "client_secret": secret.get_secret_value(),
                        "scope": CONNECTOR_SCOPE,
                    },
                )
            except httpx.HTTPError as exc:
                raise ChannelError(str(exc), code="token_network_error", retryable=True) from exc

            if response.status_code >= 400:
                raise ChannelError(
                    f"El servicio de tokens devolvió {response.status_code}: {response.text[:300]}",
                    code="token_rejected",
                    retryable=response.status_code >= 500,
                )

            data = response.json()
            self._token = data["access_token"]
            self._token_expires_at = time.monotonic() + max(
                int(data.get("expires_in", 3_600)) - TOKEN_SKEW_S, 60
            )
            return self._token


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _content_type_for_mime(mime: str) -> ContentType:
    for prefix, content_type in _MIME_PREFIX_TO_CONTENT:
        if mime.startswith(prefix):
            return content_type
    return ContentType.DOCUMENT if mime else ContentType.UNKNOWN


def _strip_mentions(payload: dict[str, Any]) -> str | None:
    """Elimina las menciones al bot del texto, habitual en Teams.

    Teams entrega ``"<at>Bot</at> hola"``; la lógica de negocio solo necesita
    ``"hola"``.
    """
    text = payload.get("text")
    if not text:
        return None
    recipient_id = (payload.get("recipient") or {}).get("id")
    for entity in payload.get("entities") or []:
        if entity.get("type") != "mention":
            continue
        mentioned = (entity.get("mentioned") or {}).get("id")
        if recipient_id and mentioned != recipient_id:
            continue
        if mention_text := entity.get("text"):
            text = text.replace(mention_text, "")
    return text.strip() or None


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def _ensure_trailing_slash(url: str) -> str:
    return url if url.endswith("/") else url + "/"
