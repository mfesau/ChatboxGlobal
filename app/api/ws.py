"""WebSockets del chatbox y de la consola de agentes."""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.api.deps import CONTACT_SESSION_COOKIE, SESSION_COOKIE
from app.core.envelope import ChannelKind, Direction
from app.core.hub import agent_topic, conversation_topic, hub, inbox_topic, presence_topic
from app.core.security import hash_token
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import Tenant
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["websocket"])

#: Tope de mensajes recuperados al abrir la conexión.
HISTORY_LIMIT = 100


def _make_sender(websocket: WebSocket):
    async def send(event: dict[str, Any]) -> None:
        await websocket.send_json(event)

    return send


@router.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket) -> None:
    """Canal bidireccional del chatbox web.

    El login es obligatorio: la identidad la resuelve la cookie de sesión del
    contacto, nunca un identificador que aporte el cliente. Así el hilo de
    conversación sigue al contacto autenticado y no a un navegador concreto.
    """
    identity = await _authenticate_contact_socket(websocket)
    if identity is None:
        # `_authenticate_contact_socket` ya cerró la conexión con el código adecuado.
        return
    session_id, tenant_slug, display_name, email = identity

    await websocket.accept()
    orchestrator = websocket.app.state.orchestrator
    topic = conversation_topic(session_id)
    sender = _make_sender(websocket)

    await hub.subscribe(topic, sender)
    try:
        snapshot = await _thread_snapshot(session_id, tenant_slug)
        await websocket.send_json({"type": "ready", "session_id": session_id, **snapshot})

        while True:
            data = await websocket.receive_json()
            kind = data.get("type", "message")

            if kind == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            if kind != "message":
                continue

            payload = {
                "session_id": session_id,
                "tenant": tenant_slug,
                # La identidad viene de la sesión autenticada, nunca del cliente.
                "display_name": display_name,
                "email": email,
                "text": data.get("text"),
                "attachments": data.get("attachments"),
                "action": data.get("action"),
                "client_message_id": data.get("client_message_id"),
                "locale": data.get("locale"),
            }
            # Eco inmediato: el usuario ve su mensaje sin esperar al modelo.
            await websocket.send_json(
                {
                    "type": "ack",
                    "client_message_id": data.get("client_message_id"),
                    "text": data.get("text"),
                }
            )
            await orchestrator.handle_event(
                ChannelKind.WEB,
                payload=payload,
                headers=dict(websocket.headers),
                verify=False,
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("chat_socket_error", session_id=session_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
    finally:
        await hub.unsubscribe(topic, sender)


@router.websocket("/ws/inbox")
async def inbox_socket(
    websocket: WebSocket,
    tenant: str | None = Query(default=None),
    api_key: str | None = Query(default=None),
) -> None:
    """Avisos en directo para la consola del equipo.

    Cada consola se suscribe a la cola común, a la presencia del equipo y —si
    quien la abre tiene sesión— a su tema personal, por el que llegan las
    derivaciones y menciones dirigidas a esa persona.
    """
    settings = websocket.app.state.settings
    tenant_slug = tenant or settings.default_tenant_slug

    agent_id, agent_label = await _authenticate_socket(websocket, api_key, tenant_slug)
    if agent_id is None and agent_label is None:
        # `_authenticate_socket` ya cerró la conexión con el código adecuado.
        return

    await websocket.accept()
    sender = _make_sender(websocket)
    topics = [inbox_topic(tenant_slug), presence_topic(tenant_slug)]
    if agent_id:
        topics.append(agent_topic(agent_id))
    for topic in topics:
        await hub.subscribe(topic, sender)

    try:
        await websocket.send_json(
            {"type": "ready", "topics": topics, "agent_id": agent_id, "agent": agent_label}
        )
        while True:
            # La consola solo escucha; se leen sus latidos para detectar cierres.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        for topic in topics:
            await hub.unsubscribe(topic, sender)


async def _authenticate_socket(
    websocket: WebSocket, api_key: str | None, tenant_slug: str
) -> tuple[str | None, str | None]:
    """Resuelve la identidad del socket.

    Devuelve ``(None, None)`` y cierra la conexión cuando la autenticación falla.
    Los WebSocket no admiten cabeceras personalizadas desde el navegador, de modo
    que la sesión viaja en la cookie y la clave de servicio, como parámetro.
    """
    settings = websocket.app.state.settings

    token = websocket.cookies.get(SESSION_COOKIE)
    if token:
        async with session_scope() as session:
            agent = await repo.resolve_agent_session(session, hash_token(token))
            if agent is not None:
                return str(agent.id), agent.label

    expected = settings.admin_api_key
    if expected is not None and api_key == expected.get_secret_value():
        return None, "clave de servicio"

    # El login es obligatorio en todo entorno: sin sesión de agente ni clave
    # de servicio válida, se rechaza la conexión también en desarrollo.
    await websocket.close(code=4401)
    return None, None


async def _authenticate_contact_socket(
    websocket: WebSocket,
) -> tuple[str, str, str | None, str | None] | None:
    """Resuelve el contacto autenticado del chatbox o cierra la conexión.

    El login es obligatorio, también en desarrollo: sin cookie de sesión de
    contacto válida no hay conversación. Devuelve primitivas —no el objeto
    ORM— para que sigan siendo válidas fuera de la sesión que las cargó.
    """
    token = websocket.cookies.get(CONTACT_SESSION_COOKIE)
    if token:
        async with session_scope() as session:
            contact = await repo.resolve_contact_session(session, hash_token(token))
            if contact is not None:
                tenant = await session.get(Tenant, contact.tenant_id)
                if tenant is not None:
                    return str(contact.id), tenant.slug, contact.display_name, contact.primary_email

    await websocket.close(code=4401)
    return None


async def _thread_snapshot(session_id: str, tenant_slug: str) -> dict[str, Any]:
    """Historial y estado actual del hilo, para el evento ``ready``.

    ``control``/``assignee_name`` le bastan al chatbox para decidir si el
    encabezado debe mostrar el nombre del propio cliente o el de quien lo
    atiende — sin ellos tendría que adivinarlo o esperar un segundo mensaje.
    """
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, tenant_slug)
        conversation = await repo.find_conversation(
            session,
            tenant_id=tenant.id,
            channel=ChannelKind.WEB,
            channel_conversation_id=session_id,
        )
        if conversation is None:
            return {"history": [], "control": "bot", "assignee_name": None}

        messages = await repo.recent_messages(session, conversation.id, limit=HISTORY_LIMIT)
        assignee_name = None
        if conversation.assignee_id:
            assignee = await repo.get_agent(session, conversation.assignee_id)
            assignee_name = assignee.label if assignee else None

        return {
            "history": [
                {
                    "direction": "inbound" if row.direction is Direction.INBOUND else "outbound",
                    "text": row.text,
                    "content_type": str(row.content_type),
                    "attachments": row.attachments or [],
                    "author_type": row.author_type,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in messages
            ],
            "control": conversation.control,
            "assignee_name": assignee_name,
        }
