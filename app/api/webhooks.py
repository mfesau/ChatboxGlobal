"""Endpoints de entrada de los canales externos.

Todos comparten la misma disciplina: validar la autenticidad, delegar en el
orquestador y responder lo antes posible. WhatsApp reintenta la entrega si el
webhook tarda más de unos segundos, y el servicio de canales de Microsoft
también, de modo que aquí no se realiza trabajo prolongado.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import ContactDep, OrchestratorDep, SessionDep, SettingsDep
from app.channels.base import SignatureError
from app.channels.facebook import FacebookAdapter
from app.channels.whatsapp import WhatsAppAdapter
from app.core import web_thread
from app.core.envelope import ChannelKind
from app.core.secrets import DecryptionError, EncryptionNotConfiguredError, decrypt_json
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["webhooks"])


async def _read_body(request: Request) -> tuple[dict[str, Any], bytes]:
    """Devuelve el cuerpo decodificado y su forma original en bytes.

    Los bytes sin modificar son imprescindibles: la firma HMAC se calcula sobre
    ellos y cualquier reserialización la invalidaría.

    Casi todos los canales mandan JSON. Twilio no: manda un formulario
    codificado como el de una página web, así que se admiten los dos y el
    adaptador recibe un diccionario en cualquier caso.
    """
    raw = await request.body()
    if not raw:
        return {}, raw

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type == "application/x-www-form-urlencoded":
        posted = await request.form()
        return {key: str(value) for key, value in posted.items()}, raw

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"JSON no válido: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Se esperaba un objeto JSON en la raíz",
        )
    return payload, raw


def _public_url(request: Request, settings: Any) -> str:
    """URL completa de la petición, tal como la compuso el proveedor.

    Detrás del proxy inverso, ``request.url`` puede traer el esquema y el
    puerto internos, y Twilio firmó la dirección pública. Se reconstruye a
    partir de ``PUBLIC_BASE_URL``, que es la que se dio de alta en su panel; el
    camino y la consulta sí salen de la petición.
    """
    base = settings.public_base_url.rstrip("/")
    consulta = f"?{request.url.query}" if request.url.query else ""
    return f"{base}{request.url.path}{consulta}"


async def _account_credentials(
    session: Any, settings: Any, channel: ChannelKind
) -> list[dict[str, str]]:
    """Credenciales propias de las cuentas de ese canal, ya descifradas.

    Una credencial que no se pueda descifrar se descarta en lugar de tumbar el
    webhook: pasa si se cambió ``SECRET_ENCRYPTION_KEY``, y entonces lo correcto
    es seguir aceptando lo que valide con las demás —o con la global de
    ``.env``— y no dejar de recibir mensajes por una cuenta rota.
    """
    credenciales: list[dict[str, str]] = []
    for blob in await repo.list_channel_credential_blobs(session, channel=channel):
        try:
            credenciales.append(decrypt_json(blob, settings=settings))
        except (DecryptionError, EncryptionNotConfiguredError) as exc:
            log.warning("channel_credentials_unreadable", channel=str(channel), error=str(exc))
    return credenciales


# --------------------------------------------------------------------------- #
# WhatsApp Cloud API
# --------------------------------------------------------------------------- #
@router.get("/webhooks/whatsapp", include_in_schema=False)
async def verify_whatsapp(
    request: Request,
    orchestrator: OrchestratorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Responde al reto de suscripción de Meta."""
    adapter = orchestrator.registry.get(ChannelKind.WHATSAPP)
    assert isinstance(adapter, WhatsAppAdapter)
    credenciales = await _account_credentials(session, settings, ChannelKind.WHATSAPP)
    try:
        challenge = adapter.verify_subscription(
            dict(request.query_params), credentials=credenciales
        )
    except SignatureError as exc:
        log.warning("whatsapp_verification_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return Response(content=challenge, media_type="text/plain")


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(
    request: Request,
    orchestrator: OrchestratorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    payload, raw = await _read_body(request)
    credenciales = await _account_credentials(session, settings, ChannelKind.WHATSAPP)
    try:
        result = await orchestrator.handle_event(
            ChannelKind.WHATSAPP,
            payload=payload,
            headers=request.headers,
            raw_body=raw,
            credentials=credenciales,
        )
    except SignatureError as exc:
        log.warning("whatsapp_signature_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {"status": "ok", **result}


# --------------------------------------------------------------------------- #
# Facebook Messenger
# --------------------------------------------------------------------------- #
@router.get("/webhooks/facebook", include_in_schema=False)
async def verify_facebook(
    request: Request,
    orchestrator: OrchestratorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> Response:
    """Responde al reto de suscripción de Meta."""
    adapter = orchestrator.registry.get(ChannelKind.FACEBOOK)
    assert isinstance(adapter, FacebookAdapter)
    credenciales = await _account_credentials(session, settings, ChannelKind.FACEBOOK)
    try:
        challenge = adapter.verify_subscription(
            dict(request.query_params), credentials=credenciales
        )
    except SignatureError as exc:
        log.warning("facebook_verification_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return Response(content=challenge, media_type="text/plain")


@router.post("/webhooks/facebook")
async def facebook_webhook(
    request: Request,
    orchestrator: OrchestratorDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    payload, raw = await _read_body(request)
    credenciales = await _account_credentials(session, settings, ChannelKind.FACEBOOK)
    try:
        result = await orchestrator.handle_event(
            ChannelKind.FACEBOOK,
            payload=payload,
            headers=request.headers,
            raw_body=raw,
            credentials=credenciales,
        )
    except SignatureError as exc:
        log.warning("facebook_signature_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return {"status": "ok", **result}


# --------------------------------------------------------------------------- #
# Microsoft Bot Framework
# --------------------------------------------------------------------------- #
@router.post("/api/messages")
async def bot_framework_webhook(
    request: Request, orchestrator: OrchestratorDep
) -> Response:
    """Endpoint de mensajería del bot registrado en Azure Bot Service."""
    payload, raw = await _read_body(request)
    try:
        await orchestrator.handle_event(
            ChannelKind.MSBOT,
            payload=payload,
            headers=request.headers,
            raw_body=raw,
        )
    except SignatureError as exc:
        log.warning("msbot_jwt_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    # El protocolo espera 200 o 202 sin cuerpo; la respuesta viaja por la
    # Connector API, no por esta conexión.
    return Response(status_code=status.HTTP_202_ACCEPTED)


# --------------------------------------------------------------------------- #
# Chatbox web (alternativa REST al WebSocket)
# --------------------------------------------------------------------------- #
@router.post("/api/web/messages")
async def web_message(
    request: Request,
    session: SessionDep,
    orchestrator: OrchestratorDep,
    settings: SettingsDep,
    contact: ContactDep,
) -> dict[str, Any]:
    """Recibe un mensaje del widget cuando el WebSocket no está disponible.

    El login es obligatorio: la identidad no la aporta el cliente sino la
    sesión autenticada, para que nadie pueda escribir a nombre de otro correo.
    """
    payload, raw = await _read_body(request)
    payload.setdefault("tenant", settings.default_tenant_slug)
    payload["session_id"] = await web_thread.current_thread_id(session, contact)
    payload["display_name"] = contact.display_name
    payload["email"] = contact.primary_email

    # Se suelta la transacción de la petición antes de entregar el mensaje: el
    # orquestador abre la suya para escribir, y sostener las dos a la vez sobre
    # la misma base es el interbloqueo que ya documenta ``send_from_agent``.
    # Lo que hacía falta de ``contact`` ya está copiado arriba.
    await session.rollback()
    result = await orchestrator.handle_event(
        ChannelKind.WEB,
        payload=payload,
        headers=request.headers,
        raw_body=raw,
        verify=False,
    )
    return {"status": "ok", **result}


# --------------------------------------------------------------------------- #
# Canales futuros
# --------------------------------------------------------------------------- #
@router.post("/webhooks/{channel}")
async def generic_webhook(
    channel: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    orchestrator: OrchestratorDep,
) -> dict[str, Any]:
    """Ruta genérica para cualquier canal ya inscrito en el registro.

    Permite incorporar un canal nuevo sin escribir un endpoint dedicado: solo
    los que necesitan un reto ``GET`` de alta —los de Meta— tienen el suyo.
    Las credenciales de las cuentas se resuelven aquí igual que en las rutas
    dedicadas; sin ellas, un canal como Telegram no tendría con qué comprobar
    el secreto de su webhook.
    """
    try:
        kind = ChannelKind(channel)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Canal desconocido: {channel}"
        ) from exc

    payload, raw = await _read_body(request)
    credentials = await _account_credentials(session, settings, kind)
    try:
        result = await orchestrator.handle_event(
            kind,
            payload=payload,
            headers=request.headers,
            raw_body=raw,
            credentials=credentials,
            url=_public_url(request, settings),
        )
    except SignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    return {"status": "ok", **result}
