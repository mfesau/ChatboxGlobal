"""Pruebas de la capa HTTP: webhooks, consola y comprobaciones de salud.

Se ejecuta la aplicación ASGI en memoria y se puebla ``app.state`` igual que lo
hace el ciclo de vida, pero sin arrancar los trabajadores de la cola: así las
pruebas comprueban el contrato HTTP sin depender de temporizadores.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import ClassVar

import httpx
import pytest_asyncio

from app.channels.base import ChannelRegistry
from app.config import get_settings
from app.core.orchestrator import Orchestrator
from app.core.pipeline import Handler, NextFn, Pipeline, TurnContext
from app.core.security import hash_password
from app.db import repositories as repo
from app.db.engine import session_scope
from app.main import create_app

APP_SECRET = "secreto-webhook"
AGENT_PASSWORD = "ClaveConsola123"
CONTACT_PASSWORD = "ClaveClienteSegura1"


async def _login_as_agent(client: httpx.AsyncClient, *, role: str = "supervisor") -> None:
    """Da de alta un agente con contraseña conocida y autentica ``client``."""
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        await repo.create_agent(
            session,
            tenant_id=tenant.id,
            email="consola@empresa.local",
            display_name="Consola",
            role=role,
            password_hash=hash_password(AGENT_PASSWORD),
        )
    response = await client.post(
        "/api/auth/login", json={"email": "consola@empresa.local", "password": AGENT_PASSWORD}
    )
    assert response.status_code == 200, response.text


async def _register_contact(
    client: httpx.AsyncClient, *, email: str = "cliente@clientes.local"
) -> None:
    """Registra un cliente del chatbox y deja su cookie en ``client``.

    Distinta de la cookie de agente: ambas identidades pueden convivir en el
    mismo cliente HTTP, cada una con su propio nombre de cookie.
    """
    response = await client.post(
        "/api/contact/register", json={"email": email, "password": CONTACT_PASSWORD}
    )
    assert response.status_code == 201, response.text


class EchoHandler(Handler):
    name: ClassVar[str] = "echo"

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        if ctx.text:
            ctx.reply(f"eco: {ctx.text}")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    settings = get_settings()
    application = create_app()
    application.state.settings = settings
    application.state.registry = ChannelRegistry(settings)
    application.state.orchestrator = Orchestrator(
        settings=settings,
        registry=application.state.registry,
        pipeline=Pipeline([EchoHandler()]),
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://pruebas") as active:
        # Referencia explícita a la aplicación: algunas pruebas cambian la
        # configuración y necesitan descartar los adaptadores ya construidos.
        active.asgi_app = application
        yield active
    await application.state.registry.aclose()


# --------------------------------------------------------------------------- #
# Salud
# --------------------------------------------------------------------------- #
async def test_health_reports_ok(client: httpx.AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_checks_the_database(client: httpx.AsyncClient):
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


# --------------------------------------------------------------------------- #
# Chatbox web
# --------------------------------------------------------------------------- #
async def test_web_message_requires_login(client: httpx.AsyncClient):
    response = await client.post(
        "/api/web/messages",
        json={"session_id": "web-http-1", "text": "Buenas tardes"},
    )
    assert response.status_code == 401


async def test_web_message_endpoint_processes_the_turn(client: httpx.AsyncClient):
    await _register_contact(client)
    response = await client.post(
        "/api/web/messages",
        json={"session_id": "web-http-1", "text": "Buenas tardes"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "parsed": 1, "processed": 1, "skipped": 0}


async def test_web_message_rejects_malformed_json(client: httpx.AsyncClient):
    await _register_contact(client)
    response = await client.post(
        "/api/web/messages",
        content=b"{no es json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400


async def test_chatbox_page_is_served(client: httpx.AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_console_page_is_served(client: httpx.AsyncClient):
    """Una sola consola para todos los roles: se adapta ya autenticada."""
    response = await client.get("/console")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_old_role_specific_console_routes_are_gone(client: httpx.AsyncClient):
    for path in ("/agente", "/supervisor", "/administrador"):
        assert (await client.get(path)).status_code == 404


# --------------------------------------------------------------------------- #
# WhatsApp
# --------------------------------------------------------------------------- #
async def test_whatsapp_verification_rejects_unknown_token(client: httpx.AsyncClient):
    response = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "falso", "hub.challenge": "1"},
    )
    assert response.status_code == 403


async def test_whatsapp_webhook_rejects_invalid_signature(
    client: httpx.AsyncClient, monkeypatch
):
    settings = get_settings()
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(APP_SECRET))
    # El adaptador toma el secreto de la configuración compartida.
    client.asgi_app.state.registry._instances.clear()

    response = await client.post(
        "/webhooks/whatsapp",
        content=b'{"entry": []}',
        headers={"x-hub-signature-256": "sha256=incorrecta", "content-type": "application/json"},
    )
    assert response.status_code == 401


async def test_whatsapp_webhook_accepts_signed_payload(client: httpx.AsyncClient, monkeypatch):
    from pydantic import SecretStr

    settings = get_settings()
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(APP_SECRET))
    client.asgi_app.state.registry._instances.clear()

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "555"},
                            "contacts": [{"profile": {"name": "Luis"}, "wa_id": "595981000111"}],
                            "messages": [
                                {
                                    "from": "595981000111",
                                    "id": "wamid.HTTP1",
                                    "timestamp": "1740000000",
                                    "type": "text",
                                    "text": {"body": "Hola"},
                                }
                            ],
                        }
                    }
                ]
            }
        ],
    }
    body = json.dumps(payload).encode()
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

    response = await client.post(
        "/webhooks/whatsapp",
        content=body,
        headers={
            "x-hub-signature-256": f"sha256={digest}",
            "content-type": "application/json",
        },
    )

    assert response.status_code == 200
    assert response.json()["processed"] == 1


# --------------------------------------------------------------------------- #
# Microsoft Bot Framework
# --------------------------------------------------------------------------- #
async def test_bot_framework_endpoint_requires_a_token(client: httpx.AsyncClient, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "microsoft_validate_jwt", True)
    monkeypatch.setattr(settings, "microsoft_app_id", "app-id-de-prueba")
    client.asgi_app.state.registry._instances.clear()

    response = await client.post("/api/messages", json={"type": "message"})
    assert response.status_code == 401


async def test_bot_framework_endpoint_accepts_activity_without_validation(
    client: httpx.AsyncClient, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "microsoft_validate_jwt", False)
    client.asgi_app.state.registry._instances.clear()

    response = await client.post(
        "/api/messages",
        json={
            "type": "message",
            "id": "act-1",
            "serviceUrl": "https://smba.trafficmanager.net/emea/",
            "channelId": "msteams",
            "from": {"id": "29:usuario", "name": "Luis"},
            "conversation": {"id": "19:hilo"},
            "recipient": {"id": "28:bot"},
            "text": "Hola desde Teams",
        },
    )
    assert response.status_code == 202


# --------------------------------------------------------------------------- #
# Canales desconocidos
# --------------------------------------------------------------------------- #
async def test_unknown_channel_returns_not_found(client: httpx.AsyncClient):
    response = await client.post("/webhooks/telegram", json={})
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Consola
# --------------------------------------------------------------------------- #
async def test_console_lists_conversations_and_messages(client: httpx.AsyncClient):
    await _register_contact(client)
    await _login_as_agent(client)
    await client.post(
        "/api/web/messages", json={"session_id": "web-consola-1", "text": "Necesito ayuda"}
    )

    listing = await client.get("/api/conversations")
    assert listing.status_code == 200
    conversations = listing.json()
    assert len(conversations) == 1
    assert conversations[0]["channel"] == "web"
    assert conversations[0]["control"] == "bot"

    thread = await client.get(f"/api/conversations/{conversations[0]['id']}/messages")
    assert thread.status_code == 200
    assert [m["text"] for m in thread.json()] == ["Necesito ayuda", "eco: Necesito ayuda"]


async def test_console_can_hand_control_to_a_person(client: httpx.AsyncClient):
    await _register_contact(client)
    await _login_as_agent(client)
    await client.post("/api/web/messages", json={"session_id": "web-consola-2", "text": "Hola"})
    conversation_id = (await client.get("/api/conversations")).json()[0]["id"]

    response = await client.post(
        f"/api/conversations/{conversation_id}/control", json={"control": "human"}
    )
    assert response.status_code == 200
    assert response.json()["control"] == "human"

    listing = (await client.get("/api/conversations")).json()
    assert listing[0]["control"] == "human"


async def test_console_reply_is_queued(client: httpx.AsyncClient):
    await _register_contact(client)
    await _login_as_agent(client)
    await client.post("/api/web/messages", json={"session_id": "web-consola-3", "text": "Hola"})
    conversation_id = (await client.get("/api/conversations")).json()[0]["id"]

    response = await client.post(
        f"/api/conversations/{conversation_id}/reply",
        json={"text": "Le atiende un agente."},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


async def test_console_rejects_unknown_conversation(client: httpx.AsyncClient):
    import uuid

    await _login_as_agent(client)
    response = await client.get(f"/api/conversations/{uuid.uuid4()}/messages")
    assert response.status_code == 404


async def test_stats_groups_messages_by_channel(client: httpx.AsyncClient):
    await _register_contact(client)
    await _login_as_agent(client)
    await client.post("/api/web/messages", json={"session_id": "web-stats-1", "text": "Hola"})

    response = await client.get("/api/stats")
    assert response.status_code == 200
    assert response.json()["messages_by_channel"]["web"] == 2


async def test_admin_requires_key_when_configured(client: httpx.AsyncClient, monkeypatch):
    from pydantic import SecretStr

    settings = get_settings()
    monkeypatch.setattr(settings, "admin_api_key", SecretStr("clave-consola"))

    assert (await client.get("/api/conversations")).status_code == 401

    authorized = await client.get(
        "/api/conversations", headers={"X-API-Key": "clave-consola"}
    )
    assert authorized.status_code == 200


async def test_console_has_no_anonymous_access_in_dev(client: httpx.AsyncClient):
    """El login es obligatorio en todo entorno: sin sesión ni clave, 401."""
    assert (await client.get("/api/conversations")).status_code == 401
    assert (await client.get("/api/agents")).status_code == 401


# --------------------------------------------------------------------------- #
# Cuenta del chatbox
# --------------------------------------------------------------------------- #
async def test_contact_can_register_and_use_the_chatbox(client: httpx.AsyncClient):
    response = await client.post(
        "/api/contact/register",
        json={"email": "nueva@clientes.local", "password": CONTACT_PASSWORD},
    )
    assert response.status_code == 201
    assert "chatbox_contact_session" in response.cookies

    me = await client.get("/api/contact/me")
    assert me.status_code == 200
    assert me.json()["contact"]["email"] == "nueva@clientes.local"


async def test_contact_registration_rejects_duplicate_email(client: httpx.AsyncClient):
    await _register_contact(client, email="repetido@clientes.local")

    # Un segundo cliente, sin la cookie del primero, para probar el registro
    # y no el inicio de sesión.
    transport = httpx.ASGITransport(app=client.asgi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pruebas") as second:
        response = await second.post(
            "/api/contact/register",
            json={"email": "repetido@clientes.local", "password": CONTACT_PASSWORD},
        )
    assert response.status_code == 409


async def test_contact_registration_rejects_a_short_password(client: httpx.AsyncClient):
    response = await client.post(
        "/api/contact/register", json={"email": "corta@clientes.local", "password": "1234567"}
    )
    assert response.status_code == 422


async def test_contact_login_round_trip(client: httpx.AsyncClient):
    await _register_contact(client, email="retorno@clientes.local")
    await client.post("/api/contact/logout")

    assert (await client.get("/api/contact/me")).status_code == 401

    login = await client.post(
        "/api/contact/login",
        json={"email": "retorno@clientes.local", "password": CONTACT_PASSWORD},
    )
    assert login.status_code == 200
