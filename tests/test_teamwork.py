"""Pruebas del trabajo en equipo: acceso, alcance por rol, derivación y notas.

Escenario compartido: dos agentes —Ana y Luis— y una supervisora, Marta. Todas
las conversaciones entran por la cola común, sea cual sea el canal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.channels.base import ChannelRegistry
from app.channels.whatsapp import WhatsAppAdapter
from app.config import get_settings
from app.core import hotel_booking
from app.core.envelope import ChannelKind, ConversationRef
from app.core.orchestrator import Orchestrator
from app.core.pipeline import Handler, NextFn, Pipeline, TurnContext
from app.core.saml import AgentInactiveError, login_or_provision_agent
from app.core.secrets import EncryptionNotConfiguredError, decrypt_json, encrypt_json
from app.core.security import (
    WeakPasswordError,
    hash_password,
    hash_token,
    needs_rehash,
    new_session_token,
    verify_password,
)
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import (
    ROLE_ADMIN,
    ROLE_AGENT,
    Assignment,
    Conversation,
    Department,
    InternalNote,
    Message,
    OutboxItem,
)
from app.handlers.ai import AIHandler
from app.handlers.builtin import BusinessHoursHandler, FirstResponseSlaHandler
from app.main import create_app

PASSWORD = "ClaveSegura123"


class EchoHandler(Handler):
    name: ClassVar[str] = "echo"

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        if ctx.text:
            ctx.reply(f"eco: {ctx.text}")


# --------------------------------------------------------------------------- #
# Andamiaje
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def app() -> AsyncIterator[Any]:
    settings = get_settings()
    application = create_app()
    application.state.settings = settings
    application.state.registry = ChannelRegistry(settings)
    application.state.orchestrator = Orchestrator(
        settings=settings,
        registry=application.state.registry,
        # El horario de atención va delante del eco, en el mismo orden que en
        # la cadena real: sin horario configurado deja pasar el turno, así que
        # el resto de las pruebas no cambia.
        pipeline=Pipeline([BusinessHoursHandler(), FirstResponseSlaHandler(), EchoHandler()]),
    )
    yield application
    await application.state.registry.aclose()


@pytest_asyncio.fixture
async def team() -> dict[str, Any]:
    """Crea el equipo directamente en la base, con contraseña conocida."""
    created: dict[str, Any] = {}
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        password_hash = hash_password(PASSWORD)
        for key, email, name, role in (
            ("ana", "ana@empresa.local", "Ana Rodríguez", "agent"),
            ("luis", "luis@empresa.local", "Luis Pérez", "agent"),
            ("marta", "marta@empresa.local", "Marta Giménez", "supervisor"),
            ("admin", "admin@empresa.local", "Administrador", "admin"),
        ):
            agent = await repo.create_agent(
                session,
                tenant_id=tenant.id,
                email=email,
                display_name=name,
                role=role,
                password_hash=password_hash,
            )
            created[key] = {"id": str(agent.id), "email": email, "name": name, "role": role}
        created["tenant_id"] = tenant.id
    return created


@pytest_asyncio.fixture
async def anonymous(app) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pruebas") as client:
        # `arrive()` necesita reconstruir clientes propios por cliente simulado,
        # ahora que el chatbox exige una cuenta por cada uno.
        client.asgi_app = app
        yield client


@pytest_asyncio.fixture
async def as_agent(app) -> AsyncIterator[Callable[[str], Any]]:
    """Fábrica de clientes con sesión iniciada; cada uno guarda su cookie."""
    clients: list[httpx.AsyncClient] = []

    async def login(email: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://pruebas")
        response = await client.post(
            "/api/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        clients.append(client)
        return client

    yield login
    for client in clients:
        await client.aclose()


#: Contraseña compartida por los clientes simulados en ``arrive()``.
CLIENT_PASSWORD = "ClaveClienteSegura1"


async def arrive(anonymous: httpx.AsyncClient, session_id: str, text: str = "Hola") -> str:
    """Simula la llegada de un chat y devuelve el identificador del hilo.

    El chatbox exige login: cada ``session_id`` representa un cliente distinto,
    con su propia cuenta. Reutilizar el mismo ``session_id`` en dos llamadas
    simula al mismo cliente volviendo a escribir, de modo que inicia sesión en
    vez de registrarse de nuevo.
    """
    email = f"{session_id}@clientes.local"
    transport = httpx.ASGITransport(app=anonymous.asgi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pruebas") as client:
        register = await client.post(
            "/api/contact/register", json={"email": email, "password": CLIENT_PASSWORD}
        )
        if register.status_code == 409:
            login = await client.post(
                "/api/contact/login", json={"email": email, "password": CLIENT_PASSWORD}
            )
            assert login.status_code == 200, login.text
            contact_id = login.json()["contact"]["id"]
        else:
            assert register.status_code == 201, register.text
            contact_id = register.json()["contact"]["id"]

        response = await client.post(
            "/api/web/messages", json={"session_id": session_id, "text": text}
        )
        assert response.status_code == 200

    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        conversation = await repo.find_conversation(
            session,
            tenant_id=tenant.id,
            channel="web",
            channel_conversation_id=contact_id,
        )
        assert conversation is not None
        return str(conversation.id)


def ids(payload: list[dict[str, Any]]) -> set[str]:
    return {row["id"] for row in payload}


# --------------------------------------------------------------------------- #
# Credenciales
# --------------------------------------------------------------------------- #
def test_password_round_trip():
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt$")
    assert PASSWORD not in stored
    assert verify_password(PASSWORD, stored) is True
    assert verify_password("otra-cosa", stored) is False


def test_password_hashes_differ_by_salt():
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_short_password_is_rejected():
    with pytest.raises(WeakPasswordError):
        hash_password("corta")


@pytest.mark.parametrize("stored", [None, "", "texto-sin-formato", "scrypt$mal"])
def test_verification_tolerates_unusable_hashes(stored):
    assert verify_password(PASSWORD, stored) is False


def test_legacy_parameters_are_flagged_for_rehash():
    assert needs_rehash("scrypt$1024$8$1$c2FsdA$a2V5") is True
    assert needs_rehash(hash_password(PASSWORD)) is False


def test_session_tokens_are_unique_and_stored_hashed():
    first, second = new_session_token(), new_session_token()
    assert first != second
    assert hash_token(first) != first
    assert len(hash_token(first)) == 64


# --------------------------------------------------------------------------- #
# Acceso
# --------------------------------------------------------------------------- #
async def test_login_succeeds_and_reports_the_role(anonymous, team):
    response = await anonymous.post(
        "/api/auth/login", json={"email": team["marta"]["email"], "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent"]["role"] == "supervisor"
    assert body["agent"]["is_supervisor"] is True
    assert "chatbox_session" in response.cookies


async def test_login_rejects_a_wrong_password(anonymous, team):
    response = await anonymous.post(
        "/api/auth/login", json={"email": team["ana"]["email"], "password": "incorrecta"}
    )
    assert response.status_code == 401


async def test_login_rejects_an_unknown_address(anonymous, team):
    response = await anonymous.post(
        "/api/auth/login", json={"email": "nadie@empresa.local", "password": PASSWORD}
    )
    assert response.status_code == 401


async def test_login_rejects_a_deactivated_agent(anonymous, team):
    async with session_scope() as session:
        agent = await repo.get_agent(session, uuid.UUID(team["ana"]["id"]))
        agent.is_active = False

    response = await anonymous.post(
        "/api/auth/login", json={"email": team["ana"]["email"], "password": PASSWORD}
    )
    assert response.status_code == 401


async def test_whoami_identifies_the_agent(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    body = (await ana.get("/api/auth/me")).json()
    assert body["agent"]["email"] == team["ana"]["email"]
    assert body["is_supervisor"] is False


async def test_logout_invalidates_the_session(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    assert (await ana.post("/api/auth/logout")).status_code == 200
    # La cookie sigue en el cliente, pero la sesión ya no existe en el servidor,
    # y el login es obligatorio: sin sesión válida, 401.
    assert (await ana.get("/api/conversations")).status_code == 401
    assert (await ana.get("/api/auth/me")).status_code == 401


# --------------------------------------------------------------------------- #
# La cola común como punto único de entrada
# --------------------------------------------------------------------------- #
async def test_every_channel_lands_unassigned_in_the_common_queue(anonymous, as_agent, team):
    await arrive(anonymous, "web-cola-1", "Consulta desde la web")
    ana = await as_agent(team["ana"]["email"])

    queue = (await ana.get("/api/conversations?scope=unassigned")).json()

    assert len(queue) == 1
    assert queue[0]["assignee_id"] is None
    assert queue[0]["control"] == "bot"


async def test_summary_counts_the_queue_and_the_own_workload(anonymous, as_agent, team):
    first = await arrive(anonymous, "web-cola-2")
    await arrive(anonymous, "web-cola-3")
    ana = await as_agent(team["ana"]["email"])

    assert (await ana.get("/api/inbox/summary")).json()["unassigned"] == 2

    await ana.post(f"/api/conversations/{first}/claim")
    summary = (await ana.get("/api/inbox/summary")).json()

    assert summary["unassigned"] == 1
    assert summary["mine"] == 1
    assert summary["is_supervisor"] is False


async def test_claiming_assigns_the_thread_and_silences_the_assistant(
    anonymous, as_agent, team
):
    conversation_id = await arrive(anonymous, "web-tomar-1")
    ana = await as_agent(team["ana"]["email"])

    response = await ana.post(f"/api/conversations/{conversation_id}/claim")

    assert response.status_code == 200
    assert response.json()["assignee_id"] == team["ana"]["id"]
    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        assert str(conversation.assignee_id) == team["ana"]["id"]
        assert conversation.control == "human"


async def test_a_claimed_thread_disappears_for_the_rest_of_the_team(
    anonymous, as_agent, team
):
    """Una vez tomada, el hilo sale de la cola común y deja de ser visible.

    Se responde 404 y no 409: para un compañero, una conversación que no puede
    ver simplemente no existe. El conflicto explícito queda reservado a quien sí
    la ve, es decir, a supervisión.
    """
    conversation_id = await arrive(anonymous, "web-tomar-2")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])

    await ana.post(f"/api/conversations/{conversation_id}/claim")

    assert (await luis.post(f"/api/conversations/{conversation_id}/claim")).status_code == 404
    assert conversation_id not in ids((await luis.get("/api/conversations")).json())


async def test_the_supervisor_gets_an_explicit_conflict(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-tomar-3")
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    response = await marta.post(f"/api/conversations/{conversation_id}/claim")

    assert response.status_code == 409
    assert "Otro compañero" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Alcance de visibilidad
# --------------------------------------------------------------------------- #
async def test_an_agent_does_not_see_a_colleagues_workload(anonymous, as_agent, team):
    de_ana = await arrive(anonymous, "web-alcance-1")
    de_luis = await arrive(anonymous, "web-alcance-2")
    en_cola = await arrive(anonymous, "web-alcance-3")

    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    await ana.post(f"/api/conversations/{de_ana}/claim")
    await luis.post(f"/api/conversations/{de_luis}/claim")

    visible = ids((await ana.get("/api/conversations")).json())

    assert de_ana in visible
    assert en_cola in visible          # la cola común es de todos
    assert de_luis not in visible      # la cartera del compañero, no


async def test_asking_for_everything_does_not_widen_an_agents_view(
    anonymous, as_agent, team
):
    de_luis = await arrive(anonymous, "web-alcance-4")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    await luis.post(f"/api/conversations/{de_luis}/claim")

    assert de_luis not in ids((await ana.get("/api/conversations?scope=all")).json())


async def test_the_supervisor_sees_every_thread(anonymous, as_agent, team):
    de_ana = await arrive(anonymous, "web-alcance-5")
    de_luis = await arrive(anonymous, "web-alcance-6")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    marta = await as_agent(team["marta"]["email"])
    await ana.post(f"/api/conversations/{de_ana}/claim")
    await luis.post(f"/api/conversations/{de_luis}/claim")

    visible = ids((await marta.get("/api/conversations")).json())

    assert {de_ana, de_luis} <= visible
    assert (await marta.get("/api/inbox/summary")).json()["is_supervisor"] is True


async def test_admin_sees_every_thread_and_can_reassign_it(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-admin-reasigna")
    ana = await as_agent(team["ana"]["email"])
    admin = await as_agent(team["admin"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    visible = ids((await admin.get("/api/conversations?scope=all")).json())
    response = await admin.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"], "note": "Reasignado por administración."},
    )

    assert conversation_id in visible
    assert response.status_code == 200
    assert response.json()["assignee_id"] == team["luis"]["id"]


async def test_reading_a_colleagues_thread_returns_not_found(anonymous, as_agent, team):
    """Se responde 404 y no 403: la existencia del hilo ya sería información."""
    de_luis = await arrive(anonymous, "web-alcance-7")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    await luis.post(f"/api/conversations/{de_luis}/claim")

    assert (await ana.get(f"/api/conversations/{de_luis}/messages")).status_code == 404
    assert (await luis.get(f"/api/conversations/{de_luis}/messages")).status_code == 200


# --------------------------------------------------------------------------- #
# Derivación conservando el historial
# --------------------------------------------------------------------------- #
async def test_transfer_moves_responsibility_and_keeps_the_whole_history(
    anonymous, as_agent, team
):
    conversation_id = await arrive(anonymous, "web-derivar-1", "Primera consulta")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])

    await ana.post(f"/api/conversations/{conversation_id}/claim")
    await ana.post(
        f"/api/conversations/{conversation_id}/reply", json={"text": "Le atiendo yo, Ana."}
    )
    antes = (await ana.get(f"/api/conversations/{conversation_id}/messages")).json()

    response = await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"], "note": "Es un caso de facturación."},
    )
    assert response.status_code == 200
    assert response.json()["assignee_id"] == team["luis"]["id"]

    despues = (await luis.get(f"/api/conversations/{conversation_id}/messages")).json()

    # Quien recibe la conversación ve exactamente lo mismo que veía quien la pasó.
    assert [m["id"] for m in despues] == [m["id"] for m in antes]
    assert [m["text"] for m in despues] == [m["text"] for m in antes]
    assert len(despues) >= 3


async def test_after_a_transfer_the_thread_changes_hands_in_both_inboxes(
    anonymous, as_agent, team
):
    conversation_id = await arrive(anonymous, "web-derivar-2")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])

    await ana.post(f"/api/conversations/{conversation_id}/claim")
    await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"]},
    )

    assert conversation_id not in ids((await ana.get("/api/conversations?scope=mine")).json())
    assert conversation_id in ids((await luis.get("/api/conversations?scope=mine")).json())


async def test_transfer_note_becomes_an_internal_note(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-derivar-3")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"], "note": "Pendiente de la factura 2026-114."},
    )

    notes = (await luis.get(f"/api/conversations/{conversation_id}/notes")).json()

    assert len(notes) == 1
    assert "2026-114" in notes[0]["body"]
    assert notes[0]["agent"] == team["ana"]["name"]


async def test_assignment_history_records_the_full_chain(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-derivar-4")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    marta = await as_agent(team["marta"]["email"])

    await ana.post(f"/api/conversations/{conversation_id}/claim")
    await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"], "note": "Cambio de turno."},
    )
    await luis.post(f"/api/conversations/{conversation_id}/release", json={})

    history = (await marta.get(f"/api/conversations/{conversation_id}/assignments")).json()

    assert [entry["action"] for entry in history] == ["claim", "transfer", "release"]
    assert history[1]["from_agent"] == team["ana"]["name"]
    assert history[1]["to_agent"] == team["luis"]["name"]
    assert history[1]["note"] == "Cambio de turno."
    assert history[2]["to_agent"] is None


async def test_an_agent_cannot_transfer_a_colleagues_thread(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-derivar-5")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    await luis.post(f"/api/conversations/{conversation_id}/claim")

    response = await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["ana"]["id"]},
    )
    # No la ve, de modo que para Ana la conversación no existe.
    assert response.status_code == 404


async def test_the_supervisor_can_reassign_any_thread(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-derivar-6")
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    response = await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"], "note": "Reparto de carga."},
    )

    assert response.status_code == 200
    assert response.json()["assignee_id"] == team["luis"]["id"]


async def test_transfer_to_an_unknown_agent_is_rejected(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-derivar-7")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    response = await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


async def test_transfer_to_the_current_owner_is_rejected(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-derivar-8")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    response = await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["ana"]["id"]},
    )
    assert response.status_code == 409


async def test_releasing_returns_the_thread_to_the_queue_and_the_assistant(
    anonymous, as_agent, team
):
    conversation_id = await arrive(anonymous, "web-liberar-1")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    response = await ana.post(f"/api/conversations/{conversation_id}/release", json={})

    assert response.status_code == 200
    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        assert conversation.assignee_id is None
        assert conversation.control == "bot"


# --------------------------------------------------------------------------- #
# Notas internas
# --------------------------------------------------------------------------- #
async def test_internal_notes_never_reach_the_customer(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-notas-1")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    response = await ana.post(
        f"/api/conversations/{conversation_id}/notes",
        json={"body": "Cliente recurrente: aplicar descuento del 10 %."},
    )
    assert response.status_code == 201

    async with session_scope() as session:
        notes = list((await session.execute(select(InternalNote))).scalars())
        messages = list((await session.execute(select(Message))).scalars())

    assert len(notes) == 1
    # La nota no genera mensaje alguno, así que no puede salir por ningún canal.
    assert all("descuento" not in (message.text or "") for message in messages)


async def test_notes_are_visible_to_whoever_receives_the_thread(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-notas-2")
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    await ana.post(
        f"/api/conversations/{conversation_id}/notes", json={"body": "Habla solo guaraní."}
    )
    await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"]},
    )

    notes = (await luis.get(f"/api/conversations/{conversation_id}/notes")).json()

    assert any("guaraní" in note["body"] for note in notes)


# --------------------------------------------------------------------------- #
# Supervisión
# --------------------------------------------------------------------------- #
async def test_supervisor_overview_reports_the_workload(anonymous, as_agent, team):
    de_ana = await arrive(anonymous, "web-super-1")
    await arrive(anonymous, "web-super-2")
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    await ana.post(f"/api/conversations/{de_ana}/claim")

    overview = (await marta.get("/api/supervisor/overview")).json()

    por_agente = {row["agent"]: row for row in overview["workload"]}
    assert por_agente["Cola común"]["open_conversations"] == 1
    assert por_agente[team["ana"]["name"]]["open_conversations"] == 1
    assert overview["messages_by_channel"]["web"] >= 2


async def test_supervisor_overview_lists_recent_transfers(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-super-3")
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"], "note": "Especialidad de Luis."},
    )

    overview = (await marta.get("/api/supervisor/overview")).json()
    transferencias = [row for row in overview["recent_transfers"] if row["action"] == "transfer"]

    assert transferencias
    assert transferencias[0]["to"] == team["luis"]["name"]
    assert transferencias[0]["note"] == "Especialidad de Luis."


async def test_an_agent_cannot_open_the_supervisor_panel(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    assert (await ana.get("/api/supervisor/overview")).status_code == 403


async def test_only_the_admin_creates_agents(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    admin = await as_agent(team["admin"]["email"])
    nuevo = {
        "email": "nuevo@empresa.local",
        "display_name": "Nuevo Compañero",
        "role": "agent",
        "password": "OtraClave123",
    }

    assert (await ana.post("/api/agents", json=nuevo)).status_code == 403
    assert (await marta.post("/api/agents", json=nuevo)).status_code == 403
    assert (await admin.post("/api/agents", json=nuevo)).status_code == 201
    assert (await admin.post("/api/agents", json=nuevo)).status_code == 409


async def test_creating_an_agent_without_a_password_is_rejected(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    response = await admin.post(
        "/api/agents",
        json={"email": "sin.clave@empresa.local", "role": "agent"},
    )
    assert response.status_code == 422


async def test_creating_a_supervisor_generates_a_password_and_ignores_any_sent(
    anonymous, as_agent, team
):
    """El rol determina la contraseña: a un agente se la pide, a un supervisor
    o administrador se la genera el sistema, aunque el formulario mande una."""
    admin = await as_agent(team["admin"]["email"])
    response = await admin.post(
        "/api/agents",
        json={
            "email": "nueva.supervisora@empresa.local",
            "role": "supervisor",
            "password": "ContraseñaQueDeberiaIgnorarse1",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["temporary_password"]
    assert body["temporary_password"] != "ContraseñaQueDeberiaIgnorarse1"
    # Sin SMTP configurado en las pruebas, el correo no se envía, pero el alta
    # no debe fallar por eso: la contraseña generada queda en la respuesta.
    assert body["invitation_email_sent"] is False

    login = await anonymous.post(
        "/api/auth/login",
        json={
            "email": "nueva.supervisora@empresa.local",
            "password": body["temporary_password"],
        },
    )
    assert login.status_code == 200


async def test_a_new_agent_can_sign_in(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    await admin.post(
        "/api/agents",
        json={
            "email": "recien@empresa.local",
            "display_name": "Recién Llegada",
            "role": "agent",
            "password": "ClaveInicial9",
        },
    )

    response = await anonymous.post(
        "/api/auth/login", json={"email": "recien@empresa.local", "password": "ClaveInicial9"}
    )
    assert response.status_code == 200


async def test_only_admin_may_change_passwords(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    admin = await as_agent(team["admin"]["email"])

    propia = await ana.post(
        f"/api/agents/{team['ana']['id']}/password", json={"password": "NuevaClave123"}
    )
    ajena = await ana.post(
        f"/api/agents/{team['luis']['id']}/password", json={"password": "NuevaClave123"}
    )
    supervision = await marta.post(
        f"/api/agents/{team['luis']['id']}/password", json={"password": "NuevaClave123"}
    )
    administration = await admin.post(
        f"/api/agents/{team['luis']['id']}/password", json={"password": "NuevaClave123"}
    )

    assert propia.status_code == 403
    assert ajena.status_code == 403
    assert supervision.status_code == 403
    assert administration.status_code == 200


async def test_only_the_admin_renames_an_agent(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    admin = await as_agent(team["admin"]["email"])

    assert (
        await ana.patch(
            f"/api/agents/{team['luis']['id']}", json={"display_name": "Luis Nuevo"}
        )
    ).status_code == 403
    assert (
        await marta.patch(
            f"/api/agents/{team['luis']['id']}", json={"display_name": "Luis Nuevo"}
        )
    ).status_code == 403

    renamed = await admin.patch(
        f"/api/agents/{team['luis']['id']}", json={"display_name": "Luis Nuevo"}
    )
    assert renamed.status_code == 200

    colegas = (await ana.get("/api/agents")).json()
    luis = next(row for row in colegas if row["id"] == team["luis"]["id"])
    assert luis["display_name"] == "Luis Nuevo"


async def test_only_the_admin_deactivates_an_agent(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    admin = await as_agent(team["admin"]["email"])

    assert (await ana.delete(f"/api/agents/{team['luis']['id']}")).status_code == 403
    assert (await marta.delete(f"/api/agents/{team['luis']['id']}")).status_code == 403

    deactivated = await admin.delete(f"/api/agents/{team['luis']['id']}")
    assert deactivated.status_code == 200

    # La cuenta sigue existiendo —con su nombre, para el historial— pero ya no
    # puede iniciar sesión.
    login = await admin.post(
        "/api/auth/login", json={"email": team["luis"]["email"], "password": PASSWORD}
    )
    assert login.status_code == 401

    colegas = (await ana.get("/api/agents")).json()
    luis = next(row for row in colegas if row["id"] == team["luis"]["id"])
    assert luis["is_active"] is False
    assert luis["display_name"] == team["luis"]["name"]


async def test_deleting_an_agent_keeps_the_history_but_loses_the_name(
    anonymous, as_agent, team
):
    """La diferencia real entre eliminar y desactivar.

    Desactivar conserva la fila para que el historial se pueda leer. Eliminar
    la borra: la nota sigue ahí —lo que se escribió no se pierde— pero ya no
    hay a quién atribuirla.
    """
    luis = await as_agent(team["luis"]["email"])
    admin = await as_agent(team["admin"]["email"])

    conversation = await arrive(anonymous, "web-borrado-1")
    await luis.post(f"/api/conversations/{conversation}/claim")
    await luis.post(
        f"/api/conversations/{conversation}/notes", json={"body": "Revisar el pedido"}
    )

    assert (await admin.delete(f"/api/agents/{team['luis']['id']}/permanently")).status_code == 200

    # La cuenta ya no está en el directorio del equipo.
    colegas = (await admin.get("/api/agents")).json()
    assert team["luis"]["id"] not in {row["id"] for row in colegas}

    # La nota sobrevive, sin autor; y la conversación vuelve a la cola común.
    notas = (await admin.get(f"/api/conversations/{conversation}/notes")).json()
    assert [n["body"] for n in notas] == ["Revisar el pedido"]
    async with session_scope() as session:
        fila = await repo.get_conversation(session, uuid.UUID(conversation))
        assert fila.assignee_id is None


async def test_only_the_admin_deletes_an_agent(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    ruta = f"/api/agents/{team['luis']['id']}/permanently"
    assert (await ana.delete(ruta)).status_code == 403
    assert (await marta.delete(ruta)).status_code == 403


async def test_an_admin_cannot_delete_their_own_account(as_agent, team):
    """El mismo resguardo que al desactivar: dejaría la casa sin llaves."""
    admin = await as_agent(team["admin"]["email"])
    assert (
        await admin.delete(f"/api/agents/{team['admin']['id']}/permanently")
    ).status_code == 409


async def test_the_last_active_admin_cannot_be_deleted(as_agent, team):
    async with session_scope() as session:
        marta_agent = await repo.get_agent(session, uuid.UUID(team["marta"]["id"]))
        marta_agent.role = "admin"

    marta_as_admin = await as_agent(team["marta"]["email"])
    # Con dos administradores, uno puede eliminar al otro...
    assert (
        await marta_as_admin.delete(f"/api/agents/{team['admin']['id']}/permanently")
    ).status_code == 200
    # ...y entonces Marta queda sola y ya no puede eliminarse por otra vía.
    admin_nuevo = await as_agent(team["marta"]["email"])
    assert (
        await admin_nuevo.delete(f"/api/agents/{team['marta']['id']}/permanently")
    ).status_code == 409


async def test_an_admin_cannot_deactivate_their_own_account(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    response = await admin.delete(f"/api/agents/{team['admin']['id']}")
    assert response.status_code == 409


async def test_the_last_active_admin_cannot_be_deactivated(anonymous, as_agent, team, monkeypatch):
    async with session_scope() as session:
        marta_agent = await repo.get_agent(session, uuid.UUID(team["marta"]["id"]))
        marta_agent.role = "admin"

    # Con dos administradores activos, uno puede desactivar al otro...
    marta_as_admin = await as_agent(team["marta"]["email"])
    assert (await marta_as_admin.delete(f"/api/agents/{team['admin']['id']}")).status_code == 200

    # ...pero, con Marta como única administradora activa, ni siquiera la
    # clave de servicio —que no dispara el resguardo de "no a mí mismo"—
    # puede desactivarla: dejaría al inquilino sin ningún administrador.
    from pydantic import SecretStr

    settings = get_settings()
    monkeypatch.setattr(settings, "admin_api_key", SecretStr("clave-de-prueba"))
    response = await anonymous.delete(
        f"/api/agents/{team['marta']['id']}", headers={"X-API-Key": "clave-de-prueba"}
    )
    assert response.status_code == 409


async def test_only_the_admin_edits_the_automatic_reply(as_agent, team):
    from app.handlers.builtin import FallbackHandler

    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    admin = await as_agent(team["admin"]["email"])

    inicial = (await admin.get("/api/admin/settings")).json()
    assert inicial["fallback_message"] == FallbackHandler.MESSAGE

    assert (
        await ana.put("/api/admin/settings", json={"fallback_message": "Nuevo texto"})
    ).status_code == 403
    assert (
        await marta.put("/api/admin/settings", json={"fallback_message": "Nuevo texto"})
    ).status_code == 403

    updated = await admin.put(
        "/api/admin/settings", json={"fallback_message": "Un momento, por favor."}
    )
    assert updated.status_code == 200
    assert updated.json()["fallback_message"] == "Un momento, por favor."

    leido_de_nuevo = (await admin.get("/api/admin/settings")).json()
    assert leido_de_nuevo["fallback_message"] == "Un momento, por favor."


async def test_only_the_admin_changes_the_brand_colour(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    admin = await as_agent(team["admin"]["email"])

    # De partida no hay color propio y rige la paleta de la hoja de estilos.
    inicial = (await admin.get("/api/admin/branding")).json()
    assert inicial["accent"] is None
    assert inicial["css"] == ""

    for cliente in (ana, marta):
        assert (
            await cliente.put("/api/admin/branding", json={"accent": "#c0392b"})
        ).status_code == 403

    guardado = await admin.put("/api/admin/branding", json={"accent": "#C0392B"})
    assert guardado.status_code == 200
    assert guardado.json()["accent"] == "#c0392b"

    releido = (await admin.get("/api/admin/branding")).json()
    assert releido["accent"] == "#c0392b"
    assert "--accent:#c0392b" in releido["css"]


async def test_the_brand_colour_reaches_the_page_before_it_is_painted(
    as_agent, team, anonymous
):
    """Va incrustado en la cabecera y no en una petición posterior.

    Es lo que evita el destello: si el color llegara después, se vería un
    instante el azul de la hoja antes de repintarse.
    """
    admin = await as_agent(team["admin"]["email"])
    await admin.put("/api/admin/branding", json={"accent": "#7048e8"})

    for pagina in ("/", "/console"):
        html = (await anonymous.get(pagina)).text
        assert "__BRAND_STYLE__" not in html
        assert "--accent:#7048e8" in html


async def test_the_brand_colour_can_be_taken_back(as_agent, team, anonymous):
    admin = await as_agent(team["admin"]["email"])
    await admin.put("/api/admin/branding", json={"accent": "#7048e8"})

    quitado = await admin.put("/api/admin/branding", json={"accent": None})
    assert quitado.status_code == 200
    assert quitado.json()["accent"] is None
    assert quitado.json()["css"] == ""
    assert "#7048e8" not in (await anonymous.get("/")).text


async def test_something_that_is_not_a_colour_is_turned_away(as_agent, team):
    """El valor acaba dentro de una hoja de estilos: se valida en la entrada."""
    admin = await as_agent(team["admin"]["email"])
    for malo in ["rojo", "#12345", "#2f5bd7;}body{display:none", "javascript:alert(1)"]:
        respuesta = await admin.put("/api/admin/branding", json={"accent": malo})
        assert respuesta.status_code == 422, malo


async def test_the_preview_derives_the_palette_without_saving_it(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    muestra = await admin.get("/api/admin/branding/preview", params={"accent": "#ffe600"})
    assert muestra.status_code == 200
    paleta = muestra.json()["palette"]
    # Un amarillo se usa de relleno con texto negro, y como tinta se oscurece.
    assert paleta["light"]["accent_contrast"] == "#000000"
    assert paleta["light"]["accent_ink"] != "#ffe600"
    # Y no se ha guardado nada.
    assert (await admin.get("/api/admin/branding")).json()["accent"] is None


# --------------------------------------------------------------------------- #
# Conversaciones salientes
# --------------------------------------------------------------------------- #
#: Lo que devolvería Meta. Se sustituye la llamada a la Graph API: la prueba
#: verifica lo nuestro —validación, contacto, hilo y carga encolada—, no que
#: Meta siga respondiendo igual.
PLANTILLAS = [
    {"name": "hello_world", "language": "en_US", "category": "UTILITY",
     "body": "Welcome and congratulations!", "variables": 0},
    {"name": "pedido", "language": "es", "category": "UTILITY",
     "body": "Hola {{1}}, su pedido {{2}} sale hoy.", "variables": 2},
]


@pytest_asyncio.fixture
async def whatsapp_listo(app, team, monkeypatch):
    """Cuenta de WhatsApp activa y plantillas simuladas."""
    async with session_scope() as session:
        await repo.create_channel_account(
            session,
            tenant_id=team["tenant_id"],
            channel=ChannelKind.WHATSAPP,
            external_id="1278491988679333",
        )

    async def plantillas(self):
        return PLANTILLAS

    monkeypatch.setattr(WhatsAppAdapter, "list_templates", plantillas)
    return team


async def test_starting_a_conversation_opens_a_thread_with_the_template(
    as_agent, whatsapp_listo
):
    """El caso completo: sin hilo previo, la consola escribe primero."""
    ana = await as_agent(whatsapp_listo["ana"]["email"])
    respuesta = await ana.post(
        "/api/conversations/start",
        json={"to": "+595 982 971717", "template": "pedido", "language": "es",
              "variables": ["Ana", "A-42"]},
    )
    assert respuesta.status_code == 201, respuesta.text
    conversation_id = respuesta.json()["conversation_id"]

    # El hilo queda como cualquier otro: visible en la consola y con el texto
    # ya legible, no con el nombre técnico de la plantilla.
    mensajes = (await ana.get(f"/api/conversations/{conversation_id}/messages")).json()
    assert [m["text"] for m in mensajes] == ["Hola Ana, su pedido A-42 sale hoy."]

    # Y el contacto se crea con el teléfono normalizado, para que un entrante
    # posterior del mismo número caiga en este mismo hilo.
    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        assert conversation.channel_conversation_id == "595982971717"
        contacto = await repo.get_contact(session, conversation.contact_id)
        assert contacto.primary_phone == "+595982971717"


async def test_what_travels_to_whatsapp_is_the_template_and_not_the_text(
    as_agent, whatsapp_listo
):
    """La Graph API recibe la plantilla con sus parámetros aparte.

    El texto guardado es solo para leer el hilo en la consola; enviarlo como
    mensaje suelto lo rechazaría WhatsApp fuera de la ventana de 24 horas.
    """
    ana = await as_agent(whatsapp_listo["ana"]["email"])
    await ana.post(
        "/api/conversations/start",
        json={"to": "595982971717", "template": "pedido", "language": "es",
              "variables": ["Ana", "A-42"]},
    )
    async with session_scope() as session:
        fila = (await session.execute(select(OutboxItem))).scalars().first()
    plantilla = fila.payload["message"]["channel_data"]["template"]
    # Y el destinatario del envío es el número, no un identificador interno.
    assert fila.payload["ref"]["channel_conversation_id"] == "595982971717"
    assert plantilla["name"] == "pedido"
    assert plantilla["language"] == {"code": "es"}
    assert plantilla["components"][0]["parameters"] == [
        {"type": "text", "text": "Ana"},
        {"type": "text", "text": "A-42"},
    ]


async def test_a_template_that_is_not_approved_is_turned_away(as_agent, whatsapp_listo):
    ana = await as_agent(whatsapp_listo["ana"]["email"])
    respuesta = await ana.post(
        "/api/conversations/start",
        json={"to": "595982971717", "template": "inventada", "language": "es"},
    )
    assert respuesta.status_code == 404


async def test_the_values_must_match_what_the_template_declares(as_agent, whatsapp_listo):
    """Con menos parámetros de los que declara, Meta falla con un error opaco."""
    ana = await as_agent(whatsapp_listo["ana"]["email"])
    respuesta = await ana.post(
        "/api/conversations/start",
        json={"to": "595982971717", "template": "pedido", "language": "es",
              "variables": ["Solo uno"]},
    )
    assert respuesta.status_code == 422
    assert "2" in respuesta.json()["detail"]


async def test_something_that_is_not_a_phone_number_is_turned_away(as_agent, whatsapp_listo):
    ana = await as_agent(whatsapp_listo["ana"]["email"])
    for malo in ["hola", "12345", "+" + "9" * 20, ""]:
        respuesta = await ana.post(
            "/api/conversations/start",
            json={"to": malo, "template": "hello_world", "language": "en_US"},
        )
        assert respuesta.status_code == 422, malo


async def test_without_whatsapp_configured_the_console_is_told_so(as_agent, team, monkeypatch):
    """No hay token ni cuenta: se dice por qué, en vez de fallar en la cola."""
    ana = await as_agent(team["ana"]["email"])
    assert (await ana.get("/api/whatsapp/templates")).status_code == 503

    async def plantillas(self):
        return PLANTILLAS

    monkeypatch.setattr(WhatsAppAdapter, "list_templates", plantillas)
    # Plantillas sí, pero ninguna cuenta de WhatsApp dada de alta.
    respuesta = await ana.post(
        "/api/conversations/start",
        json={"to": "595982971717", "template": "hello_world", "language": "en_US"},
    )
    assert respuesta.status_code == 409


async def test_colleague_directory_feeds_the_transfer_selector(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    colegas = (await ana.get("/api/agents")).json()
    assert {row["email"] for row in colegas} >= {
        team["ana"]["email"],
        team["luis"]["email"],
        team["marta"]["email"],
    }


# --------------------------------------------------------------------------- #
# Cierre y reapertura
# --------------------------------------------------------------------------- #
async def test_closing_and_reopening_preserves_the_history(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-cierre-1", "Consulta inicial")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    antes = (await ana.get(f"/api/conversations/{conversation_id}/messages")).json()

    await ana.post(f"/api/conversations/{conversation_id}/close")
    assert conversation_id not in ids((await ana.get("/api/conversations")).json())

    await ana.post(f"/api/conversations/{conversation_id}/reopen")
    despues = (await ana.get(f"/api/conversations/{conversation_id}/messages")).json()

    assert [m["id"] for m in despues] == [m["id"] for m in antes]
    async with session_scope() as session:
        acciones = [
            entry.action
            for entry in (await session.execute(select(Assignment))).scalars()
        ]
    assert acciones == ["claim", "close", "reopen"]


async def test_a_new_message_reopens_a_closed_thread(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-cierre-2")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    await ana.post(f"/api/conversations/{conversation_id}/close")

    await arrive(anonymous, "web-cierre-2", "Sigo teniendo el problema")

    async with session_scope() as session:
        conversation = (
            await session.execute(
                select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
            )
        ).scalar_one()
        assert conversation.status == "open"


# --------------------------------------------------------------------------- #
# Departamentos
# --------------------------------------------------------------------------- #
async def test_only_the_admin_creates_departments(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    admin = await as_agent(team["admin"]["email"])

    assert (await ana.post("/api/departments", json={"name": "Ventas"})).status_code == 403
    assert (await marta.post("/api/departments", json={"name": "Ventas"})).status_code == 403

    created = await admin.post("/api/departments", json={"name": "Ventas"})
    assert created.status_code == 201
    assert created.json()["name"] == "Ventas"
    assert (await admin.post("/api/departments", json={"name": "Ventas"})).status_code == 409

    # Cualquiera con sesión puede listarlos: el desplegable de derivar lo necesita.
    listed = await ana.get("/api/departments")
    assert listed.status_code == 200
    assert "Ventas" in {row["name"] for row in listed.json()}


async def test_admin_grants_extra_departments_to_an_agent(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    ventas = (await admin.post("/api/departments", json={"name": "Ventas 2"})).json()
    soporte = (await admin.post("/api/departments", json={"name": "Soporte 2"})).json()

    ana = await as_agent(team["ana"]["email"])
    assert (
        await ana.put(
            f"/api/agents/{team['ana']['id']}/departments",
            json={"department_id": ventas["id"], "extra_department_ids": []},
        )
    ).status_code == 403

    granted = await admin.put(
        f"/api/agents/{team['ana']['id']}/departments",
        json={"department_id": ventas["id"], "extra_department_ids": [soporte["id"]]},
    )
    assert granted.status_code == 200

    colegas = (await ana.get("/api/agents")).json()
    yo = next(row for row in colegas if row["id"] == team["ana"]["id"])
    assert yo["department_id"] == ventas["id"]
    assert yo["extra_department_ids"] == [soporte["id"]]


async def test_a_conversation_without_department_stays_visible_to_everyone(
    anonymous, as_agent, team
):
    conversation_id = await arrive(anonymous, "web-depto-1")
    ana = await as_agent(team["ana"]["email"])

    queue = (await ana.get("/api/conversations?scope=unassigned")).json()
    assert conversation_id in ids(queue)


async def test_an_agent_without_department_access_does_not_see_that_queue(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Facturación"})).json()

    conversation_id = await arrive(anonymous, "web-depto-2")
    marta = await as_agent(team["marta"]["email"])
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )

    ana = await as_agent(team["ana"]["email"])
    queue = (await ana.get("/api/conversations?scope=unassigned")).json()
    assert conversation_id not in ids(queue)

    # Marta es supervisora: ve todo, con o sin departamento.
    assert conversation_id in ids((await marta.get("/api/conversations?scope=unassigned")).json())


async def test_an_agent_with_department_access_sees_that_queue(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Cobranzas"})).json()
    await admin.put(
        f"/api/agents/{team['ana']['id']}/departments",
        json={"department_id": department["id"], "extra_department_ids": []},
    )

    conversation_id = await arrive(anonymous, "web-depto-3")
    marta = await as_agent(team["marta"]["email"])
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )

    ana = await as_agent(team["ana"]["email"])
    queue = (await ana.get("/api/conversations?scope=unassigned")).json()
    assert conversation_id in ids(queue)


async def test_transfer_to_department_releases_the_assignee_and_is_logged(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Reclamos"})).json()

    conversation_id = await arrive(anonymous, "web-depto-4")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    response = await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"], "note": "No es de mi área."},
    )
    assert response.status_code == 200
    assert response.json()["department_name"] == "Reclamos"

    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        assert conversation.assignee_id is None
        assert conversation.control == "bot"
        assert str(conversation.department_id) == department["id"]

    # Ana ya no tiene acceso —el departamento no es el suyo—; se comprueba el
    # historial con la supervisora, que ve cualquier conversación.
    marta = await as_agent(team["marta"]["email"])
    history = (await marta.get(f"/api/conversations/{conversation_id}/assignments")).json()
    assert history[-1]["action"] == "transfer_department"
    assert history[-1]["to_department"] == "Reclamos"


async def test_transfer_requires_exactly_one_target(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Logística"})).json()
    conversation_id = await arrive(anonymous, "web-depto-5")
    ana = await as_agent(team["ana"]["email"])

    neither = await ana.post(f"/api/conversations/{conversation_id}/transfer", json={})
    both = await ana.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_agent_id": team["luis"]["id"], "to_department_id": department["id"]},
    )
    assert neither.status_code == 422
    assert both.status_code == 422


# --------------------------------------------------------------------------- #
# Adjuntos
# --------------------------------------------------------------------------- #
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


async def test_contact_can_upload_an_image(anonymous):
    transport = httpx.ASGITransport(app=anonymous.asgi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pruebas") as client:
        register = await client.post(
            "/api/contact/register",
            json={"email": "sube-imagen@clientes.local", "password": "ClaveClienteSegura1"},
        )
        assert register.status_code == 201

        response = await client.post(
            "/api/contact/uploads",
            files={"file": ("foto.png", _PNG_BYTES, "image/png")},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["url"].startswith("/uploads/")
        assert body["mime_type"] == "image/png"
        assert body["content_type"] == "image"


async def test_agent_can_upload_an_image(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    response = await ana.post(
        "/api/uploads", files={"file": ("foto.jpg", _PNG_BYTES, "image/jpeg")}
    )
    assert response.status_code == 201
    assert response.json()["mime_type"] == "image/jpeg"


async def test_upload_rejects_a_non_image_type(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    response = await ana.post(
        "/api/uploads", files={"file": ("nota.txt", b"hola", "text/plain")}
    )
    assert response.status_code == 415


async def test_upload_rejects_a_file_over_the_limit(as_agent, team, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_max_bytes", 10)
    ana = await as_agent(team["ana"]["email"])
    response = await ana.post(
        "/api/uploads", files={"file": ("foto.png", _PNG_BYTES, "image/png")}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Descarga de adjuntos: la misma regla que para el hilo que los contiene
# --------------------------------------------------------------------------- #
async def _attach_as_agent(client: httpx.AsyncClient, conversation_id: str) -> str:
    """Sube una imagen y la envía al cliente. Devuelve la URL del adjunto."""
    upload = await client.post(
        "/api/uploads", files={"file": ("foto.png", _PNG_BYTES, "image/png")}
    )
    assert upload.status_code == 201
    attachment = upload.json()

    reply = await client.post(
        f"/api/conversations/{conversation_id}/reply",
        json={"text": "Le envío la imagen.", "attachments": [attachment]},
    )
    assert reply.status_code == 202
    return attachment["url"]


async def test_attachment_requires_a_session(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-adjunto-1")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    url = await _attach_as_agent(ana, conversation_id)

    # `anonymous` no tiene cookie de agente ni de contacto.
    assert (await anonymous.get(url)).status_code == 401


async def test_an_agent_with_access_downloads_the_attachment(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-adjunto-2")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    url = await _attach_as_agent(ana, conversation_id)

    response = await ana.get(url)

    assert response.status_code == 200
    assert response.content == _PNG_BYTES
    assert response.headers["content-type"] == "image/png"
    # Contenido privado: ninguna caché compartida debe guardarlo.
    assert "no-store" in response.headers["cache-control"]


async def test_the_supervisor_downloads_any_attachment(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-adjunto-3")
    ana = await as_agent(team["ana"]["email"])
    marta = await as_agent(team["marta"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    url = await _attach_as_agent(ana, conversation_id)

    assert (await marta.get(url)).status_code == 200


async def test_an_agent_outside_the_department_cannot_download_it(anonymous, as_agent, team):
    """El adjunto queda tan acotado como la conversación que lo contiene."""
    conversation_id = await arrive(anonymous, "web-adjunto-4")
    admin = await as_agent(team["admin"]["email"])
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])

    await ana.post(f"/api/conversations/{conversation_id}/claim")
    url = await _attach_as_agent(ana, conversation_id)

    department = (await admin.post("/api/departments", json={"name": "Adjuntos"})).json()
    await admin.put(
        f"/api/agents/{team['luis']['id']}/departments",
        json={"department_id": department["id"], "extra_department_ids": []},
    )
    transfer = await admin.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    assert transfer.status_code == 200

    # Luis atiende ese departamento; Ana, no.
    assert (await luis.get(url)).status_code == 200
    assert (await ana.get(url)).status_code == 404


async def test_the_owner_contact_downloads_its_own_attachment(anonymous):
    transport = httpx.ASGITransport(app=anonymous.asgi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pruebas") as duenio:
        register = await duenio.post(
            "/api/contact/register",
            json={"email": "duenio-adjunto@clientes.local", "password": CLIENT_PASSWORD},
        )
        assert register.status_code == 201

        upload = await duenio.post(
            "/api/contact/uploads", files={"file": ("foto.png", _PNG_BYTES, "image/png")}
        )
        attachment = upload.json()
        sent = await duenio.post(
            "/api/web/messages",
            json={"session_id": "adjunto-propio", "text": "Mire", "attachments": [attachment]},
        )
        assert sent.status_code == 200

        assert (await duenio.get(attachment["url"])).status_code == 200

        # Otro cliente, con su propia cuenta, no alcanza ese fichero.
        async with httpx.AsyncClient(transport=transport, base_url="http://pruebas") as ajeno:
            await ajeno.post(
                "/api/contact/register",
                json={"email": "ajeno-adjunto@clientes.local", "password": CLIENT_PASSWORD},
            )
            assert (await ajeno.get(attachment["url"])).status_code == 404


async def test_attachment_rejects_a_name_outside_the_expected_shape(as_agent, team):
    """El patrón del nombre descarta el recorrido de rutas de raíz."""
    ana = await as_agent(team["ana"]["email"])
    namespace = str(team["tenant_id"])

    for nombre in ("no-es-un-uuid.png", "0" * 32 + ".exe", "..%2Fmain.py", "algo.png"):
        response = await ana.get(f"/uploads/{namespace}/{nombre}")
        assert response.status_code == 404, nombre


async def test_attachment_of_another_tenant_is_not_found(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-adjunto-5")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")
    url = await _attach_as_agent(ana, conversation_id)
    filename = url.rsplit("/", 1)[-1]

    # Mismo fichero, inquilino ajeno en la ruta.
    response = await ana.get(f"/uploads/{uuid.uuid4()}/{filename}")
    assert response.status_code == 404


async def test_attachment_never_seen_in_a_message_is_not_served(as_agent, team):
    """Un fichero huérfano no se entrega, aunque exista en disco."""
    ana = await as_agent(team["ana"]["email"])
    upload = await ana.post(
        "/api/uploads", files={"file": ("suelta.png", _PNG_BYTES, "image/png")}
    )
    url = upload.json()["url"]

    # Se subió, pero nunca se adjuntó a un mensaje: no hay conversación que
    # respalde el acceso.
    assert (await ana.get(url)).status_code == 404


# --------------------------------------------------------------------------- #
# Ficha de contacto
# --------------------------------------------------------------------------- #
async def test_any_agent_can_view_the_contact_detail(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-ficha-1")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    response = await ana.get(f"/api/conversations/{conversation_id}/contact")
    assert response.status_code == 200
    assert response.json()["comments"] == []


async def test_a_plain_agent_cannot_edit_the_contact(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-ficha-2")
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{conversation_id}/claim")

    edit = await ana.patch(
        f"/api/conversations/{conversation_id}/contact", json={"display_name": "Nuevo nombre"}
    )
    comment = await ana.post(
        f"/api/conversations/{conversation_id}/contact/comments", json={"body": "Comentario"}
    )
    assert edit.status_code == 403
    assert comment.status_code == 403


async def test_supervisor_can_edit_the_contact_and_add_a_comment(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-ficha-3")
    marta = await as_agent(team["marta"]["email"])

    edit = await marta.patch(
        f"/api/conversations/{conversation_id}/contact",
        json={"display_name": "Cliente Verificado", "primary_phone": "0981123456"},
    )
    assert edit.status_code == 200

    comment = await marta.post(
        f"/api/conversations/{conversation_id}/contact/comments",
        json={"body": "Cliente recurrente, trato preferente."},
    )
    assert comment.status_code == 201

    detail = (await marta.get(f"/api/conversations/{conversation_id}/contact")).json()
    assert detail["display_name"] == "Cliente Verificado"
    assert detail["primary_phone"] == "0981123456"
    assert len(detail["comments"]) == 1
    assert detail["comments"][0]["body"] == "Cliente recurrente, trato preferente."
    assert detail["comments"][0]["agent"] == team["marta"]["name"]
    assert detail["comments"][0]["created_at"]


async def test_editing_the_contact_email_rejects_a_duplicate(anonymous, as_agent, team):
    first_id = await arrive(anonymous, "web-ficha-4")
    second_id = await arrive(anonymous, "web-ficha-5")
    admin = await as_agent(team["admin"]["email"])

    first_email = (await admin.get(f"/api/conversations/{first_id}/contact")).json()[
        "primary_email"
    ]

    response = await admin.patch(
        f"/api/conversations/{second_id}/contact", json={"primary_email": first_email}
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Directorio de contactos
# --------------------------------------------------------------------------- #
async def test_a_plain_agent_cannot_use_the_contacts_directory(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-directorio-1")
    ana = await as_agent(team["ana"]["email"])
    contact_id = (await ana.get(f"/api/conversations/{conversation_id}/contact")).json()["id"]

    listing = await ana.get("/api/contacts")
    profile = await ana.get(f"/api/contacts/{contact_id}")
    assert listing.status_code == 403
    assert profile.status_code == 403


async def test_supervisor_can_list_and_search_contacts(anonymous, as_agent, team):
    await arrive(anonymous, "web-directorio-2")
    await arrive(anonymous, "web-directorio-3")
    marta = await as_agent(team["marta"]["email"])

    listing = (await marta.get("/api/contacts")).json()
    emails = {row["primary_email"] for row in listing}
    assert "web-directorio-2@clientes.local" in emails
    assert "web-directorio-3@clientes.local" in emails
    for row in listing:
        assert row["conversation_count"] >= 1

    filtered = (await marta.get("/api/contacts", params={"search": "web-directorio-2"})).json()
    assert [row["primary_email"] for row in filtered] == ["web-directorio-2@clientes.local"]


async def test_contact_profile_lists_its_conversation_and_comment(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-directorio-4")
    marta = await as_agent(team["marta"]["email"])
    contact_id = (await marta.get(f"/api/conversations/{conversation_id}/contact")).json()["id"]

    comment = await marta.post(
        f"/api/contacts/{contact_id}/comments", json={"body": "Cliente frecuente."}
    )
    assert comment.status_code == 201

    profile = (await marta.get(f"/api/contacts/{contact_id}")).json()
    assert {c["id"] for c in profile["conversations"]} == {conversation_id}
    assert len(profile["comments"]) == 1
    assert profile["comments"][0]["body"] == "Cliente frecuente."
    assert profile["comments"][0]["agent"] == team["marta"]["name"]


async def test_supervisor_can_edit_a_contact_from_the_directory(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-directorio-5")
    admin = await as_agent(team["admin"]["email"])
    contact_id = (await admin.get(f"/api/conversations/{conversation_id}/contact")).json()["id"]

    edit = await admin.patch(
        f"/api/contacts/{contact_id}",
        json={"display_name": "Cliente del directorio", "primary_phone": "0981000000"},
    )
    assert edit.status_code == 200

    profile = (await admin.get(f"/api/contacts/{contact_id}")).json()
    assert profile["display_name"] == "Cliente del directorio"
    assert profile["primary_phone"] == "0981000000"


async def test_editing_from_the_directory_rejects_a_duplicate_email(anonymous, as_agent, team):
    first_id = await arrive(anonymous, "web-directorio-6")
    second_id = await arrive(anonymous, "web-directorio-7")
    admin = await as_agent(team["admin"]["email"])
    first_email = (await admin.get(f"/api/conversations/{first_id}/contact")).json()[
        "primary_email"
    ]
    second_contact_id = (await admin.get(f"/api/conversations/{second_id}/contact")).json()["id"]

    response = await admin.patch(
        f"/api/contacts/{second_contact_id}", json={"primary_email": first_email}
    )
    assert response.status_code == 409


async def test_contact_profile_404_for_an_unknown_contact(as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    response = await marta.get(f"/api/contacts/{uuid.uuid4()}")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# SSO de la consola (SAML)
#
# La validación criptográfica de una aserción firmada es responsabilidad de
# `python3-saml`, ya probada aguas arriba; aquí se cubre lo propio de este
# servicio: que las rutas queden inactivas sin configurar, que no abran un
# redireccionamiento abierto, y la política de aprovisionamiento (alta
# automática con el rol básico, sin elevar a quien ya existe, cuentas
# desactivadas no reviven) aislada de la aserción SAML en sí.
# --------------------------------------------------------------------------- #
def _configure_saml(monkeypatch: Any, settings: Any) -> None:
    monkeypatch.setattr(settings, "saml_idp_entity_id", "https://sts.windows.net/tenant-id/")
    monkeypatch.setattr(
        settings, "saml_idp_sso_url", "https://login.microsoftonline.com/tenant-id/saml2"
    )
    monkeypatch.setattr(
        settings, "saml_idp_x509_cert", "MIIC8DCCAdigAwIBAgIQfakefakefakefakefakeAAA=="
    )


async def test_sso_status_is_unavailable_by_default(anonymous):
    response = await anonymous.get("/api/auth/sso")
    assert response.status_code == 200
    assert response.json() == {"available": False, "google_available": False}


async def test_saml_routes_are_hidden_when_not_configured(anonymous):
    assert (await anonymous.get("/saml/metadata")).status_code == 404
    assert (await anonymous.get("/saml/login")).status_code == 404
    assert (await anonymous.post("/saml/acs")).status_code == 404


async def test_sso_status_reports_available_once_configured(anonymous, monkeypatch):
    _configure_saml(monkeypatch, get_settings())
    response = await anonymous.get("/api/auth/sso")
    assert response.json() == {"available": True, "google_available": False}


async def test_saml_metadata_describes_this_service_as_the_sp(anonymous, monkeypatch):
    _configure_saml(monkeypatch, get_settings())
    response = await anonymous.get("/saml/metadata")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "EntityDescriptor" in response.text
    assert "/saml/acs" in response.text


async def test_saml_login_redirects_to_the_idp_with_the_return_path(anonymous, monkeypatch):
    _configure_saml(monkeypatch, get_settings())
    response = await anonymous.get("/saml/login?next=/console", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urllib.parse.urlsplit(location)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://login.microsoftonline.com/tenant-id/saml2"
    )
    query = urllib.parse.parse_qs(parsed.query)
    assert "SAMLRequest" in query
    assert query["RelayState"] == ["/console"]


async def test_saml_login_never_opens_a_redirect_to_another_site(anonymous, monkeypatch):
    _configure_saml(monkeypatch, get_settings())
    response = await anonymous.get(
        "/saml/login?next=https://ataque.example/robo", follow_redirects=False
    )
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(response.headers["location"]).query)
    assert query["RelayState"] == ["/console"]


def _configure_google(monkeypatch: Any, settings: Any) -> None:
    monkeypatch.setattr(settings, "google_client_id", "client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "client-secret")


async def test_google_routes_are_hidden_when_not_configured(anonymous):
    assert (await anonymous.get("/auth/google/login")).status_code == 404
    assert (await anonymous.get("/auth/google/callback")).status_code == 404


async def test_google_login_redirects_to_google_with_the_return_path(anonymous, monkeypatch):
    _configure_google(monkeypatch, get_settings())
    response = await anonymous.get("/auth/google/login?next=/console", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urllib.parse.urlsplit(location)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    query = urllib.parse.parse_qs(parsed.query)
    assert query["client_id"] == ["client-id.apps.googleusercontent.com"]
    assert "state" in query
    assert "chatbox_google_state" in response.cookies


async def test_google_login_never_opens_a_redirect_to_another_site(anonymous, monkeypatch):
    _configure_google(monkeypatch, get_settings())
    response = await anonymous.get(
        "/auth/google/login?next=https://ataque.example/robo", follow_redirects=False
    )
    cookie = response.cookies["chatbox_google_state"].strip('"')
    _, _, relay = cookie.partition(":")
    assert relay == "/console"


async def test_google_callback_rejects_an_unknown_state(anonymous, monkeypatch):
    _configure_google(monkeypatch, get_settings())
    response = await anonymous.get(
        "/auth/google/callback?code=abc&state=cualquiera", follow_redirects=False
    )
    assert response.status_code == 401


async def test_a_successful_google_callback_actually_opens_the_session(
    anonymous, monkeypatch
):
    """Entrar con Google tiene que dejar la cookie de sesión, no solo la cuenta.

    Sin ella el alta funciona —el agente queda creado— pero la vuelta a
    ``/console`` llega sin sesión y rebota al acceso: parecía que se podía
    registrar y no iniciar sesión.
    """
    import app.api.google_auth as google_auth

    _configure_google(monkeypatch, get_settings())

    async def perfil_falso(**kwargs):
        return {"email": "persona.google@empresa.local", "display_name": "Persona Google"}

    monkeypatch.setattr(google_auth, "exchange_code_for_userinfo", perfil_falso)

    inicio = await anonymous.get("/auth/google/login?next=/console", follow_redirects=False)
    cookie = inicio.cookies["chatbox_google_state"].strip('"')
    state, _, _ = cookie.partition(":")

    respuesta = await anonymous.get(
        f"/auth/google/callback?code=abc&state={state}", follow_redirects=False
    )
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/console"
    assert "chatbox_session" in respuesta.cookies

    # Y esa cookie vale de verdad para la consola.
    assert (await anonymous.get("/api/auth/me")).status_code == 200


async def test_login_or_provision_agent_creates_a_new_agent_with_the_basic_role(team):
    async with session_scope() as session:
        agent, created = await login_or_provision_agent(
            session,
            tenant_id=team["tenant_id"],
            email="nueva.persona@empresa.local",
            display_name="Nueva Persona",
        )
        assert created is True
        assert agent.role == ROLE_AGENT
        assert agent.password_hash is None
        assert agent.display_name == "Nueva Persona"

    async with session_scope() as session:
        found = await repo.find_agent_by_email(
            session, tenant_id=team["tenant_id"], email="nueva.persona@empresa.local"
        )
        assert found is not None


async def test_login_or_provision_agent_does_not_elevate_an_existing_account(team):
    async with session_scope() as session:
        agent, created = await login_or_provision_agent(
            session,
            tenant_id=team["tenant_id"],
            email=team["admin"]["email"],
            display_name="Suplantado",
        )
    assert created is False
    assert agent.role == ROLE_ADMIN
    # El nombre visible ya existente tampoco se pisa con lo que mande el IdP.
    assert agent.display_name == team["admin"]["name"]


async def test_login_or_provision_agent_rejects_an_inactive_account(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    deactivate = await admin.delete(f"/api/agents/{team['luis']['id']}")
    assert deactivate.status_code == 200

    async with session_scope() as session:
        with pytest.raises(AgentInactiveError):
            await login_or_provision_agent(
                session,
                tenant_id=team["tenant_id"],
                email=team["luis"]["email"],
                display_name=None,
            )


# --------------------------------------------------------------------------- #
# Cifrado de credenciales por cuenta de canal
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def con_cifrado(monkeypatch):
    """Habilita el cifrado de credenciales, apagado en el resto de la batería."""
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    monkeypatch.setattr(
        get_settings(), "secret_encryption_key", SecretStr(Fernet.generate_key().decode())
    )


async def test_an_account_can_carry_its_own_meta_credentials(as_agent, team, con_cifrado):
    """Las tres, para no tener que editar el .env al conectar un canal."""
    admin = await as_agent(team["admin"]["email"])
    alta = await admin.post(
        "/api/channel-accounts",
        json={
            "channel": "whatsapp",
            "external_id": "595900000042",
            "access_token": "token-de-la-cuenta",
            "verify_token": "verificacion-de-la-cuenta",
            "app_secret": "secreto-de-la-cuenta",
        },
    )
    assert alta.status_code == 201, alta.text
    assert alta.json()["has_own_credentials"] is True

    async with session_scope() as session:
        cuenta = await repo.get_channel_account(session, uuid.UUID(alta.json()["id"]))
        guardado = decrypt_json(cuenta.credentials_ciphertext, settings=get_settings())
    assert guardado == {
        "access_token": "token-de-la-cuenta",
        "verify_token": "verificacion-de-la-cuenta",
        "app_secret": "secreto-de-la-cuenta",
    }


async def test_changing_one_credential_leaves_the_others_alone(as_agent, team, con_cifrado):
    """Cambiar el token de acceso no puede borrar el secreto de la app."""
    admin = await as_agent(team["admin"]["email"])
    alta = await admin.post(
        "/api/channel-accounts",
        json={"channel": "whatsapp", "external_id": "595900000043",
              "access_token": "viejo", "app_secret": "secreto"},
    )
    cuenta_id = alta.json()["id"]

    await admin.patch(f"/api/channel-accounts/{cuenta_id}", json={"access_token": "nuevo"})
    async with session_scope() as session:
        cuenta = await repo.get_channel_account(session, uuid.UUID(cuenta_id))
        guardado = decrypt_json(cuenta.credentials_ciphertext, settings=get_settings())
    assert guardado == {"access_token": "nuevo", "app_secret": "secreto"}

    # Y una cadena vacía sí quita ese valor concreto, sin tocar el resto.
    await admin.patch(f"/api/channel-accounts/{cuenta_id}", json={"app_secret": ""})
    async with session_scope() as session:
        cuenta = await repo.get_channel_account(session, uuid.UUID(cuenta_id))
        guardado = decrypt_json(cuenta.credentials_ciphertext, settings=get_settings())
    assert guardado == {"access_token": "nuevo"}


async def test_a_webhook_signed_with_the_account_secret_is_accepted(
    anonymous, as_agent, team, con_cifrado
):
    """El caso que motiva todo esto: firmar con el secreto de la cuenta.

    Sin credenciales propias, la única forma de validar la firma era el
    `WHATSAPP_APP_SECRET` global del `.env` —uno para toda la instalación—.
    """
    admin = await as_agent(team["admin"]["email"])
    await admin.post(
        "/api/channel-accounts",
        json={"channel": "whatsapp", "external_id": "595900000044",
              "app_secret": "secreto-de-esta-cuenta"},
    )

    cuerpo = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    firma = hmac.new(b"secreto-de-esta-cuenta", cuerpo, hashlib.sha256).hexdigest()
    aceptada = await anonymous.post(
        "/webhooks/whatsapp",
        content=cuerpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={firma}"},
    )
    assert aceptada.status_code == 200, aceptada.text

    otra = hmac.new(b"secreto-que-nadie-dio-de-alta", cuerpo, hashlib.sha256).hexdigest()
    rechazada = await anonymous.post(
        "/webhooks/whatsapp",
        content=cuerpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={otra}"},
    )
    assert rechazada.status_code == 401


async def test_the_subscription_challenge_accepts_the_account_token(
    anonymous, as_agent, team, con_cifrado
):
    admin = await as_agent(team["admin"]["email"])
    await admin.post(
        "/api/channel-accounts",
        json={"channel": "whatsapp", "external_id": "595900000045",
              "verify_token": "mi-verificacion"},
    )
    params = {"hub.mode": "subscribe", "hub.verify_token": "mi-verificacion",
              "hub.challenge": "12345"}
    respuesta = await anonymous.get("/webhooks/whatsapp", params=params)
    assert respuesta.status_code == 200
    assert respuesta.text == "12345"

    params["hub.verify_token"] = "no-es"
    assert (await anonymous.get("/webhooks/whatsapp", params=params)).status_code == 403


async def test_a_deactivated_account_stops_authorising_webhooks(
    anonymous, as_agent, team, con_cifrado, monkeypatch
):
    """Desactivar una cuenta tiene que cortar también su credencial.

    Se configura además el secreto global, porque sin ningún secreto en juego
    el entorno `dev` se salta la comprobación entera y la prueba pasaría por el
    motivo equivocado.
    """
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings(), "whatsapp_app_secret", SecretStr("secreto-global"))
    admin = await as_agent(team["admin"]["email"])
    alta = await admin.post(
        "/api/channel-accounts",
        json={"channel": "whatsapp", "external_id": "595900000046",
              "app_secret": "secreto-a-retirar"},
    )
    await admin.patch(f"/api/channel-accounts/{alta.json()['id']}", json={"is_active": False})

    cuerpo = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    firma = hmac.new(b"secreto-a-retirar", cuerpo, hashlib.sha256).hexdigest()
    respuesta = await anonymous.post(
        "/webhooks/whatsapp",
        content=cuerpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={firma}"},
    )
    assert respuesta.status_code == 401

    # El global sigue valiendo: lo que se retiró es la credencial de la cuenta.
    con_global = hmac.new(b"secreto-global", cuerpo, hashlib.sha256).hexdigest()
    aceptada = await anonymous.post(
        "/webhooks/whatsapp",
        content=cuerpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={con_global}"},
    )
    assert aceptada.status_code == 200


def test_encrypt_json_requires_the_encryption_key():
    settings = get_settings()
    with pytest.raises(EncryptionNotConfiguredError):
        encrypt_json({"access_token": "x"}, settings=settings)


def test_encrypt_and_decrypt_json_round_trip(monkeypatch):
    from cryptography.fernet import Fernet
    from pydantic import SecretStr

    settings = get_settings()
    key = SecretStr(Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "secret_encryption_key", key)

    ciphertext = encrypt_json({"access_token": "abc123"}, settings=settings)
    assert "abc123" not in ciphertext
    assert decrypt_json(ciphertext, settings=settings) == {"access_token": "abc123"}


# --------------------------------------------------------------------------- #
# Cuentas de canal: enrutado automático a un departamento
# --------------------------------------------------------------------------- #
async def test_a_channel_account_with_a_department_routes_a_new_conversation(team):
    async with session_scope() as session:
        department = await repo.create_department(
            session, tenant_id=team["tenant_id"], name="Ventas por WhatsApp"
        )
        account = await repo.create_channel_account(
            session,
            tenant_id=team["tenant_id"],
            channel=ChannelKind.WHATSAPP,
            external_id="595900000001",
            department_id=department.id,
        )
        conversation = await repo.resolve_conversation(
            session,
            tenant_id=team["tenant_id"],
            ref=ConversationRef(
                channel=ChannelKind.WHATSAPP,
                channel_conversation_id="595982000001",
                channel_account_id=account.external_id,
            ),
            contact_id=None,
            channel_account=account,
        )
        assert conversation.department_id == department.id


async def test_a_channel_account_without_a_department_stays_in_the_common_queue(team):
    async with session_scope() as session:
        account = await repo.create_channel_account(
            session,
            tenant_id=team["tenant_id"],
            channel=ChannelKind.WHATSAPP,
            external_id="595900000002",
        )
        conversation = await repo.resolve_conversation(
            session,
            tenant_id=team["tenant_id"],
            ref=ConversationRef(
                channel=ChannelKind.WHATSAPP,
                channel_conversation_id="595982000002",
                channel_account_id=account.external_id,
            ),
            contact_id=None,
            channel_account=account,
        )
        assert conversation.department_id is None


# --------------------------------------------------------------------------- #
# Cuentas de canal: administración
# --------------------------------------------------------------------------- #
async def test_a_plain_agent_cannot_manage_channel_accounts(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    listing = await ana.get("/api/channel-accounts")
    creation = await ana.post(
        "/api/channel-accounts", json={"channel": "whatsapp", "external_id": "595900000003"}
    )
    assert listing.status_code == 403
    assert creation.status_code == 403


async def test_admin_can_create_and_edit_a_channel_account(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await admin.post("/api/departments", json={"name": "Soporte WhatsApp"})
    assert department.status_code == 201
    department_id = department.json()["id"]

    created = await admin.post(
        "/api/channel-accounts",
        json={
            "channel": "whatsapp",
            "external_id": "595900000004",
            "display_name": "Soporte WA",
            "department_id": department_id,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["department_name"] == "Soporte WhatsApp"
    assert body["has_own_credentials"] is False

    updated = await admin.patch(
        f"/api/channel-accounts/{body['id']}", json={"display_name": "Soporte WhatsApp (nuevo)"}
    )
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Soporte WhatsApp (nuevo)"
    # El departamento no se tocó: no viajó en el PATCH.
    assert updated.json()["department_name"] == "Soporte WhatsApp"

    listing = (await admin.get("/api/channel-accounts")).json()
    assert any(row["id"] == body["id"] for row in listing)


async def test_only_the_admin_deletes_a_channel_account(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    ana = await as_agent(team["ana"]["email"])
    cuenta = (
        await admin.post(
            "/api/channel-accounts",
            json={"channel": "whatsapp", "external_id": "595900000900"},
        )
    ).json()

    assert (await ana.delete(f"/api/channel-accounts/{cuenta['id']}")).status_code == 403

    borrada = await admin.delete(f"/api/channel-accounts/{cuenta['id']}")
    assert borrada.status_code == 200
    listado = (await admin.get("/api/channel-accounts")).json()
    assert not any(row["id"] == cuenta["id"] for row in listado)

    # Ya no está: repetir el borrado no la encuentra.
    assert (await admin.delete(f"/api/channel-accounts/{cuenta['id']}")).status_code == 404


async def test_deleting_an_account_with_conversations_asks_first(
    anonymous, as_agent, team
):
    """El aviso trae el número de conversaciones: dice qué hay detrás."""
    admin = await as_agent(team["admin"]["email"])
    conversation_id = await arrive(anonymous, "web-borrar-cuenta")

    cuentas = (await admin.get("/api/channel-accounts")).json()
    web = next(row for row in cuentas if row["channel"] == "web")

    primero = await admin.delete(f"/api/channel-accounts/{web['id']}")
    assert primero.status_code == 409
    assert "1" in primero.json()["detail"]

    confirmado = await admin.delete(f"/api/channel-accounts/{web['id']}?confirm=true")
    assert confirmado.status_code == 200
    assert confirmado.json()["conversations_kept"] >= 1

    # Lo que el cliente escribió no se va con la cuenta.
    async with session_scope() as session:
        conversation = await session.get(Conversation, uuid.UUID(conversation_id))
        assert conversation is not None
        assert conversation.channel_account_id is None
    mensajes = (await admin.get(f"/api/conversations/{conversation_id}/messages")).json()
    assert mensajes, "el historial debe sobrevivir al borrado de la cuenta"


async def test_creating_a_channel_account_rejects_an_unknown_department(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    response = await admin.post(
        "/api/channel-accounts",
        json={
            "channel": "whatsapp",
            "external_id": "595900000005",
            "department_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 404


async def test_facebook_requires_an_access_token(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    response = await admin.post(
        "/api/channel-accounts", json={"channel": "facebook", "external_id": "1234567890"}
    )
    assert response.status_code == 422


async def test_setting_an_access_token_requires_the_encryption_key(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    # SECRET_ENCRYPTION_KEY queda vacía por defecto en las pruebas (conftest.py).
    response = await admin.post(
        "/api/channel-accounts",
        json={
            "channel": "whatsapp",
            "external_id": "595900000006",
            "access_token": "un-token-cualquiera",
        },
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# Login unificado en "/": un solo formulario para agentes y clientes
# --------------------------------------------------------------------------- #
async def test_unified_login_recognizes_an_agent(anonymous, team):
    response = await anonymous.post(
        "/api/session/login", json={"email": team["ana"]["email"], "password": PASSWORD}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "agent"
    assert body["redirect"] == "/console"

    # La cookie quedó puesta: una llamada posterior ya viene autenticada.
    me = await anonymous.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["agent"]["email"] == team["ana"]["email"]


async def test_unified_login_recognizes_a_contact(anonymous):
    register = await anonymous.post(
        "/api/contact/register",
        json={"email": "cliente-unificado@clientes.local", "password": "ClaveClienteSegura1"},
    )
    assert register.status_code == 201
    # Registrarse ya deja la sesión de cliente puesta; se limpia para probar
    # el login unificado desde cero, no el alta.
    await anonymous.post("/api/contact/logout")

    response = await anonymous.post(
        "/api/session/login",
        json={"email": "cliente-unificado@clientes.local", "password": "ClaveClienteSegura1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "contact"
    assert body["redirect"] == "/"

    me = await anonymous.get("/api/contact/me")
    assert me.status_code == 200
    assert me.json()["contact"]["email"] == "cliente-unificado@clientes.local"


async def test_unified_login_rejects_a_wrong_password(anonymous, team):
    response = await anonymous.post(
        "/api/session/login",
        json={"email": team["ana"]["email"], "password": "contraseña-incorrecta"},
    )
    assert response.status_code == 401


async def test_unified_login_rejects_an_email_in_neither_table(anonymous):
    response = await anonymous.post(
        "/api/session/login",
        json={"email": "nadie@ninguna-parte.local", "password": "lo-que-sea-1234"},
    )
    assert response.status_code == 401


async def test_unified_login_rejects_an_inactive_agent(as_agent, anonymous, team):
    admin = await as_agent(team["admin"]["email"])
    deactivate = await admin.delete(f"/api/agents/{team['luis']['id']}")
    assert deactivate.status_code == 200

    response = await anonymous.post(
        "/api/session/login", json={"email": team["luis"]["email"], "password": PASSWORD}
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Respuestas guardadas
# --------------------------------------------------------------------------- #
async def test_only_the_admin_manages_canned_responses(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    admin = await as_agent(team["admin"]["email"])

    body = {"shortcode": "saludo", "title": "Saludo inicial", "body": "¡Hola! ¿En qué le ayudo?"}
    assert (await ana.post("/api/canned-responses", json=body)).status_code == 403

    created = await admin.post("/api/canned-responses", json=body)
    assert created.status_code == 201
    canned_id = created.json()["id"]
    assert (await admin.post("/api/canned-responses", json=body)).status_code == 409

    # Cualquiera con sesión puede listarlas: el composer las necesita.
    listed = await ana.get("/api/canned-responses")
    assert listed.status_code == 200
    assert "saludo" in {row["shortcode"] for row in listed.json()}

    assert (
        await ana.patch(f"/api/canned-responses/{canned_id}", json={"title": "Otro"})
    ).status_code == 403
    edited = await admin.patch(f"/api/canned-responses/{canned_id}", json={"title": "Otro"})
    assert edited.status_code == 200
    assert edited.json()["title"] == "Otro"

    assert (await ana.delete(f"/api/canned-responses/{canned_id}")).status_code == 403
    assert (await admin.delete(f"/api/canned-responses/{canned_id}")).status_code == 204
    assert canned_id not in {row["id"] for row in (await admin.get("/api/canned-responses")).json()}


# --------------------------------------------------------------------------- #
# Etiquetas
# --------------------------------------------------------------------------- #
async def test_only_the_admin_manages_labels(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    admin = await as_agent(team["admin"]["email"])

    assert (
        await ana.post("/api/labels", json={"name": "Urgente", "color": "#ff0000"})
    ).status_code == 403

    created = await admin.post("/api/labels", json={"name": "Urgente", "color": "#ff0000"})
    assert created.status_code == 201
    label_id = created.json()["id"]
    assert (
        await admin.post("/api/labels", json={"name": "Urgente", "color": "#ff0000"})
    ).status_code == 409

    listed = await ana.get("/api/labels")
    assert listed.status_code == 200
    assert "Urgente" in {row["name"] for row in listed.json()}

    assert (await ana.delete(f"/api/labels/{label_id}")).status_code == 403
    assert (await admin.delete(f"/api/labels/{label_id}")).status_code == 204
    assert label_id not in {row["id"] for row in (await admin.get("/api/labels")).json()}


async def test_an_agent_can_tag_a_conversation_and_filter_by_it(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    urgente = (await admin.post("/api/labels", json={"name": "Urgente 2"})).json()
    facturacion = (await admin.post("/api/labels", json={"name": "Facturación 2"})).json()

    conversation_id = await arrive(anonymous, "web-etiqueta-1")
    other_conversation_id = await arrive(anonymous, "web-etiqueta-2")

    ana = await as_agent(team["ana"]["email"])
    tagged = await ana.put(
        f"/api/conversations/{conversation_id}/labels",
        json={"label_ids": [urgente["id"], facturacion["id"]]},
    )
    assert tagged.status_code == 200
    assert {row["name"] for row in tagged.json()} == {"Urgente 2", "Facturación 2"}

    queue = {row["id"]: row for row in (await ana.get("/api/conversations")).json()}
    assert {row["name"] for row in queue[conversation_id]["labels"]} == {
        "Urgente 2",
        "Facturación 2",
    }
    assert queue[other_conversation_id]["labels"] == []

    filtered = await ana.get(f"/api/conversations?label={urgente['id']}")
    assert ids(filtered.json()) == {conversation_id}

    # Reemplaza por completo el conjunto, no lo acumula.
    replaced = await ana.put(
        f"/api/conversations/{conversation_id}/labels", json={"label_ids": [facturacion["id"]]}
    )
    assert {row["name"] for row in replaced.json()} == {"Facturación 2"}


# --------------------------------------------------------------------------- #
# Horario de atención
# --------------------------------------------------------------------------- #
#: Un horario imposible de cumplir: el lunes, de 00:00 a 00:01. Cualquier
#: instante real de la prueba cae fuera, sin depender del reloj de la máquina.
SIEMPRE_CERRADO = {"1": [["00:00", "00:01"]]}


async def test_only_the_admin_sets_business_hours(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    ana = await as_agent(team["ana"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Horarios 1"})).json()

    body = {"business_hours": {"1": [["09:00", "18:00"]]}, "timezone": "UTC"}
    assert (
        await ana.put(f"/api/departments/{department['id']}/business-hours", json=body)
    ).status_code == 403

    saved = await admin.put(f"/api/departments/{department['id']}/business-hours", json=body)
    assert saved.status_code == 200
    assert saved.json()["business_hours"] == {"1": [["09:00", "18:00"]]}
    assert saved.json()["timezone"] == "UTC"


async def test_a_malformed_schedule_is_rejected_when_it_is_configured(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Horarios 2"})).json()
    url = f"/api/departments/{department['id']}/business-hours"

    # Día fuera de 1..7, hora ilegible y zona inexistente: los tres se
    # rechazan al guardar y no de madrugada, con un cliente esperando.
    dia_invalido = await admin.put(url, json={"business_hours": {"9": [["09:00", "18:00"]]}})
    assert dia_invalido.status_code == 422
    hora_invalida = await admin.put(url, json={"business_hours": {"1": [["nueve", "18:00"]]}})
    assert hora_invalida.status_code == 422
    assert (
        await admin.put(url, json={"business_hours": {}, "timezone": "Marte/Olympus"})
    ).status_code == 422


async def test_out_of_hours_the_assistant_warns_once_and_then_stays_quiet(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Horarios 3"})).json()
    await admin.put(
        f"/api/departments/{department['id']}/business-hours",
        json={
            "business_hours": SIEMPRE_CERRADO,
            "timezone": "UTC",
            "out_of_hours_message": "Estamos cerrados; le respondemos mañana.",
        },
    )

    conversation_id = await arrive(anonymous, "web-horario-1")
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )

    async def salientes() -> list[str]:
        mensajes = (await marta.get(f"/api/conversations/{conversation_id}/messages")).json()
        return [row["text"] for row in mensajes if row["direction"] == "outbound"]

    # El primer turno entró antes de la derivación, cuando el hilo todavía no
    # tenía departamento: ese eco es correcto y sirve de línea de base.
    base = await salientes()

    await arrive(anonymous, "web-horario-1", "¿Hay alguien?")
    assert await salientes() == [*base, "Estamos cerrados; le respondemos mañana."]

    # Insiste: el mensaje se recibe igual, pero no se repite el aviso.
    await arrive(anonymous, "web-horario-1", "Sigo esperando")
    assert await salientes() == [*base, "Estamos cerrados; le respondemos mañana."]
    mensajes = (await marta.get(f"/api/conversations/{conversation_id}/messages")).json()
    assert "Sigo esperando" in {row["text"] for row in mensajes if row["direction"] == "inbound"}


async def test_a_department_without_a_schedule_keeps_answering(anonymous, as_agent, team):
    """Sin horario configurado nada cambia: el asistente sigue respondiendo."""
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Horarios 4"})).json()

    conversation_id = await arrive(anonymous, "web-horario-2")
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    await arrive(anonymous, "web-horario-2", "Hola")

    salientes = [
        row
        for row in (await marta.get(f"/api/conversations/{conversation_id}/messages")).json()
        if row["direction"] == "outbound"
    ]
    assert salientes, "sin horario configurado el asistente debe seguir contestando"


# --------------------------------------------------------------------------- #
# Estado de la conversación
# --------------------------------------------------------------------------- #
async def test_the_three_states_are_marked_by_hand(anonymous, as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    conversation_id = await arrive(anonymous, "web-estado-1")

    async def estado() -> str:
        fila = next(
            row
            for row in (await marta.get("/api/conversations?status=")).json()
            if row["id"] == conversation_id
        )
        return fila["work_state"]

    # Nace pendiente, sin que nadie la haya tocado.
    assert await estado() == "pending"

    for pedido in ("in_progress", "solved", "pending"):
        respuesta = await marta.post(
            f"/api/conversations/{conversation_id}/state", json={"state": pedido}
        )
        assert respuesta.status_code == 200
        assert await estado() == pedido


async def test_marking_in_progress_keeps_it_in_the_working_view(
    anonymous, as_agent, team
):
    """Lo que motivó el filtro: marcarla en curso no la hace desaparecer.

    La bandeja pide «sin resolver», que son dos estados a la vez; sin eso,
    quien acaba de tomar una conversación la perdería de vista al marcarla.
    """
    marta = await as_agent(team["marta"]["email"])
    conversation_id = await arrive(anonymous, "web-estado-2")
    await marta.post(
        f"/api/conversations/{conversation_id}/state", json={"state": "in_progress"}
    )

    sin_resolver = await marta.get("/api/conversations?status=open,in_progress")
    assert conversation_id in ids(sin_resolver.json())

    # Y al resolverla, sale de esa vista pero sigue estando.
    await marta.post(f"/api/conversations/{conversation_id}/state", json={"state": "solved"})
    assert conversation_id not in ids(
        (await marta.get("/api/conversations?status=open,in_progress")).json()
    )
    assert conversation_id in ids(
        (await marta.get("/api/conversations?status=closed")).json()
    )


async def test_solving_and_reopening_leaves_a_trace(anonymous, as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    conversation_id = await arrive(anonymous, "web-estado-3")

    await marta.post(f"/api/conversations/{conversation_id}/state", json={"state": "solved"})
    await marta.post(f"/api/conversations/{conversation_id}/state", json={"state": "pending"})

    historial = (await marta.get(f"/api/conversations/{conversation_id}/assignments")).json()
    acciones = [row["action"] for row in historial]
    assert "close" in acciones
    assert "reopen" in acciones


async def test_an_unknown_state_is_rejected(anonymous, as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    conversation_id = await arrive(anonymous, "web-estado-4")
    respuesta = await marta.post(
        f"/api/conversations/{conversation_id}/state", json={"state": "archivada"}
    )
    assert respuesta.status_code == 422


# --------------------------------------------------------------------------- #
# Macros
# --------------------------------------------------------------------------- #
async def test_only_the_admin_manages_macros(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    ana = await as_agent(team["ana"]["email"])
    etiqueta = (await admin.post("/api/labels", json={"name": "Macro etiqueta"})).json()
    cuerpo = {
        "name": "Cierre rápido",
        "steps": [
            {"action": "label", "label_id": etiqueta["id"]},
            {"action": "note", "body": "Resuelto por macro"},
        ],
    }

    assert (await ana.post("/api/macros", json=cuerpo)).status_code == 403
    creada = await admin.post("/api/macros", json=cuerpo)
    assert creada.status_code == 201
    assert (await admin.post("/api/macros", json=cuerpo)).status_code == 409

    # Cualquiera con sesión las lista: son para usarlas, no para administrarlas.
    assert "Cierre rápido" in {row["name"] for row in (await ana.get("/api/macros")).json()}

    macro_id = creada.json()["id"]
    assert (await ana.delete(f"/api/macros/{macro_id}")).status_code == 403
    assert (await admin.delete(f"/api/macros/{macro_id}")).status_code == 204


async def test_a_macro_with_an_incomplete_step_is_rejected(as_agent, team):
    """Un paso sin lo suyo se rechaza al guardar, no al ejecutarlo."""
    admin = await as_agent(team["admin"]["email"])
    for paso in (
        {"action": "label"},
        {"action": "note"},
        {"action": "transfer_department"},
        {"action": "reply"},
    ):
        respuesta = await admin.post(
            "/api/macros", json={"name": f"Rota {paso['action']}", "steps": [paso]}
        )
        assert respuesta.status_code == 422, paso


async def test_a_macro_pointing_at_something_missing_is_rejected(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    respuesta = await admin.post(
        "/api/macros",
        json={
            "name": "Apunta a la nada",
            "steps": [{"action": "label", "label_id": str(uuid.uuid4())}],
        },
    )
    assert respuesta.status_code == 404


async def test_a_macro_applies_every_step_in_order(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    etiqueta = (await admin.post("/api/labels", json={"name": "Urgente macro"})).json()
    departamento = (await admin.post("/api/departments", json={"name": "Macro destino"})).json()
    plantilla = (
        await admin.post(
            "/api/canned-responses",
            json={"shortcode": "macro", "title": "Cierre", "body": "Quedamos a las órdenes."},
        )
    ).json()

    macro = (
        await admin.post(
            "/api/macros",
            json={
                "name": "Derivar y avisar",
                "steps": [
                    {"action": "label", "label_id": etiqueta["id"]},
                    {"action": "reply", "canned_response_id": plantilla["id"]},
                    {"action": "note", "body": "Ejecutada por macro"},
                    {"action": "transfer_department", "department_id": departamento["id"]},
                ],
            },
        )
    ).json()

    conversation_id = await arrive(anonymous, "web-macro-1")
    respuesta = await marta.post(f"/api/conversations/{conversation_id}/macros/{macro['id']}")
    assert respuesta.status_code == 200
    assert respuesta.json()["applied"] == ["label", "reply", "note", "transfer_department"]

    fila = next(
        row
        for row in (await marta.get("/api/conversations?status=")).json()
        if row["id"] == conversation_id
    )
    assert [row["name"] for row in fila["labels"]] == ["Urgente macro"]
    assert fila["department_name"] == "Macro destino"

    mensajes = (await marta.get(f"/api/conversations/{conversation_id}/messages")).json()
    assert "Quedamos a las órdenes." in {row["text"] for row in mensajes}
    notas = (await marta.get(f"/api/conversations/{conversation_id}/notes")).json()
    assert "Ejecutada por macro" in {row["body"] for row in notas}


async def test_a_failing_step_leaves_the_conversation_untouched(
    anonymous, as_agent, team
):
    """Lo que más importa de ejecutar varios pasos: o pasan todos, o ninguno.

    Se borra el departamento después de guardar la macro, así el último paso
    falla y hay que comprobar que la etiqueta del primero tampoco quedó.
    """
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    etiqueta = (await admin.post("/api/labels", json={"name": "No debe quedar"})).json()
    departamento = (await admin.post("/api/departments", json={"name": "Se borra"})).json()

    macro = (
        await admin.post(
            "/api/macros",
            json={
                "name": "Falla a la mitad",
                "steps": [
                    {"action": "label", "label_id": etiqueta["id"]},
                    {"action": "transfer_department", "department_id": departamento["id"]},
                ],
            },
        )
    ).json()

    async with session_scope() as session:
        await session.delete(await session.get(Department, uuid.UUID(departamento["id"])))

    conversation_id = await arrive(anonymous, "web-macro-2")
    respuesta = await marta.post(f"/api/conversations/{conversation_id}/macros/{macro['id']}")
    assert respuesta.status_code == 404

    fila = next(
        row
        for row in (await marta.get("/api/conversations?status=")).json()
        if row["id"] == conversation_id
    )
    assert fila["labels"] == [], "la etiqueta del primer paso no debía sobrevivir al fallo"


async def test_running_a_macro_twice_does_not_duplicate_the_label(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    etiqueta = (await admin.post("/api/labels", json={"name": "Una sola vez"})).json()
    macro = (
        await admin.post(
            "/api/macros",
            json={
                "name": "Solo etiqueta",
                "steps": [{"action": "label", "label_id": etiqueta["id"]}],
            },
        )
    ).json()

    conversation_id = await arrive(anonymous, "web-macro-3")
    for _ in range(2):
        await marta.post(f"/api/conversations/{conversation_id}/macros/{macro['id']}")

    fila = next(
        row
        for row in (await marta.get("/api/conversations?status=")).json()
        if row["id"] == conversation_id
    )
    assert [row["name"] for row in fila["labels"]] == ["Una sola vez"]


# --------------------------------------------------------------------------- #
# Objetivo de primera respuesta
# --------------------------------------------------------------------------- #
async def _department_with_target(admin, nombre: str, minutos: int) -> dict[str, Any]:
    """Departamento con objetivo y sin horario: el reloj corre de corrido."""
    department = (await admin.post("/api/departments", json={"name": nombre})).json()
    await admin.put(
        f"/api/departments/{department['id']}/business-hours",
        json={"business_hours": {}, "first_response_target_minutes": minutos},
    )
    return department


async def _sla_of(client, conversation_id: str) -> str | None:
    fila = next(
        row
        for row in (await client.get("/api/conversations?status=")).json()
        if row["id"] == conversation_id
    )
    return fila["sla_status"]


async def test_a_department_without_a_target_does_not_start_the_clock(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    department = (await admin.post("/api/departments", json={"name": "SLA sin objetivo"})).json()

    conversation_id = await arrive(anonymous, "web-sla-0")
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    await arrive(anonymous, "web-sla-0", "Hola")

    assert await _sla_of(marta, conversation_id) is None


async def test_the_clock_starts_when_the_customer_is_left_waiting(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    department = await _department_with_target(admin, "SLA pendiente", 60)

    conversation_id = await arrive(anonymous, "web-sla-1")
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    await arrive(anonymous, "web-sla-1", "¿Me ayudan?")

    assert await _sla_of(marta, conversation_id) == "pending"


async def test_a_human_reply_meets_the_target(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    department = await _department_with_target(admin, "SLA cumplido", 60)

    conversation_id = await arrive(anonymous, "web-sla-2")
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    await arrive(anonymous, "web-sla-2", "¿Me ayudan?")
    assert await _sla_of(marta, conversation_id) == "pending"

    await marta.post(
        f"/api/conversations/{conversation_id}/reply", json={"text": "Le ayudo yo."}
    )
    assert await _sla_of(marta, conversation_id) == "met"


async def test_the_assistant_reply_does_not_count_as_first_response(
    anonymous, as_agent, team
):
    """El eco del asistente contesta al instante; si contara, el objetivo se
    cumpliría siempre y la medición no diría nada del equipo."""
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    department = await _department_with_target(admin, "SLA solo humanos", 60)

    conversation_id = await arrive(anonymous, "web-sla-3")
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    # El asistente responde a este turno, pero el objetivo sigue pendiente.
    await arrive(anonymous, "web-sla-3", "¿Hay alguien?")

    mensajes = (await marta.get(f"/api/conversations/{conversation_id}/messages")).json()
    assert any(row["author_type"] == "bot" for row in mensajes)
    assert await _sla_of(marta, conversation_id) == "pending"


async def test_an_overdue_conversation_shows_as_breached(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    # Un minuto de objetivo, y luego se atrasa el vencimiento a mano para no
    # tener que esperar en la prueba.
    department = await _department_with_target(admin, "SLA vencido", 1)

    conversation_id = await arrive(anonymous, "web-sla-4")
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    await arrive(anonymous, "web-sla-4", "¿Me ayudan?")

    async with session_scope() as session:
        conversation = await session.get(Conversation, uuid.UUID(conversation_id))
        conversation.first_response_due_at = datetime.now(UTC) - timedelta(minutes=5)

    assert await _sla_of(marta, conversation_id) == "breached"

    # El repaso periódico lo deja anotado en la fila, no solo en la vista.
    async with session_scope() as session:
        assert await repo.breach_overdue_first_responses(session) >= 1
        conversation = await session.get(Conversation, uuid.UUID(conversation_id))
        assert conversation.sla_breached_at is not None


async def test_the_common_queue_is_measured_with_the_tenant_default(
    anonymous, as_agent, team
):
    """Una conversación sin derivar no tiene departamento del que heredar."""
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    await admin.put(
        "/api/admin/service-defaults",
        json={"business_hours": {}, "first_response_target_minutes": 30},
    )

    conversation_id = await arrive(anonymous, "web-sla-comun")
    assert await _sla_of(marta, conversation_id) == "pending"

    await marta.post(
        f"/api/conversations/{conversation_id}/reply", json={"text": "La tomo yo."}
    )
    assert await _sla_of(marta, conversation_id) == "met"


async def test_deriving_does_not_restart_the_clock_already_running(
    anonymous, as_agent, team
):
    """El cliente espera desde su primer mensaje: derivar no regala tiempo.

    Aunque el departamento de destino tenga su propio objetivo, el reloj ya
    venía corriendo con el del inquilino y se respeta ese vencimiento; si no,
    bastaría con pasarse el hilo entre departamentos para no vencer nunca.
    """
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    await admin.put(
        "/api/admin/service-defaults",
        json={"business_hours": {}, "first_response_target_minutes": 600},
    )
    department = await _department_with_target(admin, "SLA propio manda", 5)

    async def vencimiento() -> str:
        fila = next(
            row
            for row in (await marta.get("/api/conversations?status=")).json()
            if row["id"] == conversation_id
        )
        return fila["sla_due_at"]

    conversation_id = await arrive(anonymous, "web-sla-herencia")
    original = await vencimiento()

    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    await arrive(anonymous, "web-sla-herencia", "¿Me ayudan?")

    assert await vencimiento() == original


async def test_without_a_tenant_default_the_common_queue_has_no_clock(
    anonymous, as_agent, team
):
    """Sin nada configurado nada cambia: no aparecen vencimientos de la nada."""
    marta = await as_agent(team["marta"]["email"])
    conversation_id = await arrive(anonymous, "web-sla-sin-defecto")
    assert await _sla_of(marta, conversation_id) is None


async def test_the_tenant_default_rejects_an_unknown_timezone(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    response = await admin.put(
        "/api/admin/service-defaults",
        json={"business_hours": {}, "timezone": "Marte/Olympus"},
    )
    assert response.status_code == 422


async def test_a_second_message_does_not_extend_the_deadline(anonymous, as_agent, team):
    """Que el cliente insista no le regala al equipo un plazo nuevo."""
    admin = await as_agent(team["admin"]["email"])
    marta = await as_agent(team["marta"]["email"])
    department = await _department_with_target(admin, "SLA sin prórroga", 60)

    conversation_id = await arrive(anonymous, "web-sla-5")
    await marta.post(
        f"/api/conversations/{conversation_id}/transfer",
        json={"to_department_id": department["id"]},
    )
    await arrive(anonymous, "web-sla-5", "Primera")

    async def vencimiento() -> str:
        fila = next(
            row
            for row in (await marta.get("/api/conversations?status=")).json()
            if row["id"] == conversation_id
        )
        return fila["sla_due_at"]

    primero = await vencimiento()
    await arrive(anonymous, "web-sla-5", "Segunda")
    assert await vencimiento() == primero


# --------------------------------------------------------------------------- #
# Vistas guardadas
# --------------------------------------------------------------------------- #
async def test_a_personal_view_is_not_visible_to_a_colleague(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])

    created = await ana.post(
        "/api/saved-views",
        json={"name": "Mis urgentes", "filters": {"scope": "mine", "status": "open"}},
    )
    assert created.status_code == 201
    assert created.json()["shared"] is False

    assert "Mis urgentes" in {row["name"] for row in (await ana.get("/api/saved-views")).json()}
    # La cartera de un compañero no se ve, y sus vistas tampoco.
    de_luis = {row["name"] for row in (await luis.get("/api/saved-views")).json()}
    assert "Mis urgentes" not in de_luis


async def test_a_shared_view_reaches_the_whole_team(as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    ana = await as_agent(team["ana"]["email"])

    shared = await marta.post(
        "/api/saved-views",
        json={"name": "Sin atender", "filters": {"scope": "unassigned"}, "shared": True},
    )
    assert shared.status_code == 201
    assert shared.json()["shared"] is True
    assert "Sin atender" in {row["name"] for row in (await ana.get("/api/saved-views")).json()}


async def test_only_supervision_shares_a_view_with_the_team(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    response = await ana.post(
        "/api/saved-views",
        json={"name": "Intento", "filters": {}, "shared": True},
    )
    assert response.status_code == 403


async def test_a_repeated_view_name_is_rejected_within_the_same_scope(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    body = {"name": "Repetida", "filters": {"scope": "mine"}}

    assert (await ana.post("/api/saved-views", json=body)).status_code == 201
    assert (await ana.post("/api/saved-views", json=body)).status_code == 409
    # Para otra persona ese nombre sigue libre: los alcances no se pisan.
    assert (await luis.post("/api/saved-views", json=body)).status_code == 201


async def test_an_agent_cannot_delete_someone_elses_view(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    luis = await as_agent(team["luis"]["email"])
    view_id = (
        await ana.post("/api/saved-views", json={"name": "Solo mia", "filters": {}})
    ).json()["id"]

    # 404 y no 403: que exista tampoco es asunto de un compañero.
    assert (await luis.delete(f"/api/saved-views/{view_id}")).status_code == 404
    assert (await ana.delete(f"/api/saved-views/{view_id}")).status_code == 204


async def test_only_supervision_deletes_a_shared_view(as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    ana = await as_agent(team["ana"]["email"])
    view_id = (
        await marta.post(
            "/api/saved-views",
            json={"name": "Del equipo", "filters": {}, "shared": True},
        )
    ).json()["id"]

    assert (await ana.delete(f"/api/saved-views/{view_id}")).status_code == 403
    assert (await marta.delete(f"/api/saved-views/{view_id}")).status_code == 204


async def test_a_saved_view_keeps_only_the_filters_that_carry_a_value(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    created = await ana.post(
        "/api/saved-views",
        json={"name": "Solo canal", "filters": {"channel": "whatsapp"}},
    )
    assert created.status_code == 201
    assert created.json()["filters"] == {"channel": "whatsapp"}


async def test_tagging_a_conversation_with_an_unknown_label_fails(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "web-etiqueta-3")
    ana = await as_agent(team["ana"]["email"])
    response = await ana.put(
        f"/api/conversations/{conversation_id}/labels",
        json={"label_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Módulo Hotel
# --------------------------------------------------------------------------- #
async def _hotel_department(admin: httpx.AsyncClient, name: str, *, enabled: bool = True) -> dict:
    """Departamento nuevo, con el módulo de hotel activo salvo que se pida lo contrario."""
    department = (await admin.post("/api/departments", json={"name": name})).json()
    await admin.put(
        f"/api/departments/{department['id']}/hotel/module", json={"enabled": enabled}
    )
    return department


async def _hotel_room(
    admin: httpx.AsyncClient, department_id: str, *, code: str = "101", capacity: int = 2
) -> dict:
    """Categoría con capacidad, tarifa base y una habitación, listas para reservar."""
    room_type = (
        await admin.post(
            f"/api/departments/{department_id}/hotel/room-types",
            json={"name": f"Doble {code}", "capacity": capacity},
        )
    ).json()
    await admin.post(
        f"/api/departments/{department_id}/hotel/rate-plans",
        json={
            "room_type_id": room_type["id"],
            "name": "Tarifa base",
            "nightly_price_cents": 5_000,
            "currency": "USD",
        },
    )
    return (
        await admin.post(
            f"/api/departments/{department_id}/hotel/rooms",
            json={"room_type_id": room_type["id"], "code": code},
        )
    ).json()


async def test_the_hotel_module_is_off_by_default_and_only_the_admin_turns_it_on(
    as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    ana = await as_agent(team["ana"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Hotel 1"})).json()

    status_before = await admin.get(f"/api/departments/{department['id']}/hotel/module")
    assert status_before.json() == {"enabled": False}

    # Sin el módulo activo, el resto de la API de hotel responde con conflicto.
    blocked = await admin.get(f"/api/departments/{department['id']}/hotel/room-types")
    assert blocked.status_code == 409

    assert (
        await ana.put(
            f"/api/departments/{department['id']}/hotel/module", json={"enabled": True}
        )
    ).status_code == 403

    turned_on = await admin.put(
        f"/api/departments/{department['id']}/hotel/module", json={"enabled": True}
    )
    assert turned_on.status_code == 200
    assert turned_on.json() == {"enabled": True}
    assert (
        await admin.get(f"/api/departments/{department['id']}/hotel/room-types")
    ).status_code == 200


async def test_only_the_admin_sets_up_room_types_rooms_and_rates(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    ana = await as_agent(team["ana"]["email"])
    department = await _hotel_department(admin, "Hotel 2")

    assert (
        await ana.post(
            f"/api/departments/{department['id']}/hotel/room-types",
            json={"name": "Suite"},
        )
    ).status_code == 403

    room_type = await admin.post(
        f"/api/departments/{department['id']}/hotel/room-types",
        json={"name": "Suite", "capacity": 3},
    )
    assert room_type.status_code == 201

    assert (
        await ana.post(
            f"/api/departments/{department['id']}/hotel/rooms",
            json={"room_type_id": room_type.json()["id"], "code": "301"},
        )
    ).status_code == 403
    assert (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/rooms",
            json={"room_type_id": room_type.json()["id"], "code": "301"},
        )
    ).status_code == 201


async def test_booking_a_room_blocks_it_only_for_the_overlapping_dates(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 3")
    room = await _hotel_room(admin, department["id"])

    free = await admin.get(
        f"/api/departments/{department['id']}/hotel/availability",
        params={"check_in": "2026-10-01", "check_out": "2026-10-05"},
    )
    assert room["id"] in ids(free.json())

    booked = await admin.post(
        f"/api/departments/{department['id']}/hotel/reservations",
        json={
            "room_id": room["id"],
            "guest_name": "Persona Huésped",
            "check_in": "2026-10-01",
            "check_out": "2026-10-05",
        },
    )
    assert booked.status_code == 201
    reservation = booked.json()
    # Sin precio explícito, toma el de la tarifa cargada para la categoría.
    assert reservation["nightly_price_cents"] == 5_000
    assert reservation["status"] == "confirmed"

    # La misma habitación ya no aparece disponible en fechas que se solapan…
    overlapping = await admin.get(
        f"/api/departments/{department['id']}/hotel/availability",
        params={"check_in": "2026-10-03", "check_out": "2026-10-06"},
    )
    assert room["id"] not in ids(overlapping.json())

    # …y una segunda reserva para esas fechas se rechaza.
    conflict = await admin.post(
        f"/api/departments/{department['id']}/hotel/reservations",
        json={
            "room_id": room["id"],
            "guest_name": "Otra Persona",
            "check_in": "2026-10-03",
            "check_out": "2026-10-06",
        },
    )
    assert conflict.status_code == 409

    # Pero sigue libre para fechas que no se tocan con la reserva ya hecha.
    later = await admin.get(
        f"/api/departments/{department['id']}/hotel/availability",
        params={"check_in": "2026-10-05", "check_out": "2026-10-08"},
    )
    assert room["id"] in ids(later.json())


async def test_a_cancelled_reservation_frees_the_room_again(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 4")
    room = await _hotel_room(admin, department["id"])

    reservation = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/reservations",
            json={
                "room_id": room["id"],
                "guest_name": "Persona Huésped",
                "check_in": "2026-11-01",
                "check_out": "2026-11-03",
            },
        )
    ).json()

    cancelled = await admin.put(
        f"/api/departments/{department['id']}/hotel/reservations/{reservation['id']}/status",
        json={"status": "cancelled"},
    )
    assert cancelled.status_code == 200

    free_again = await admin.get(
        f"/api/departments/{department['id']}/hotel/availability",
        params={"check_in": "2026-11-01", "check_out": "2026-11-03"},
    )
    assert room["id"] in ids(free_again.json())


async def test_a_reservation_only_follows_the_allowed_status_transitions(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 5")
    room = await _hotel_room(admin, department["id"])

    reservation = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/reservations",
            json={
                "room_id": room["id"],
                "guest_name": "Persona Huésped",
                "check_in": "2026-12-01",
                "check_out": "2026-12-03",
            },
        )
    ).json()
    reservation_url = (
        f"/api/departments/{department['id']}/hotel/reservations/{reservation['id']}/status"
    )

    # De "confirmed" no se puede saltar directo a "checked_out".
    assert (await admin.put(reservation_url, json={"status": "checked_out"})).status_code == 409

    checked_in = await admin.put(reservation_url, json={"status": "checked_in"})
    assert checked_in.status_code == 200

    # Ya en "checked_in", cancelar tampoco es un salto válido.
    assert (await admin.put(reservation_url, json={"status": "cancelled"})).status_code == 409

    checked_out = await admin.put(reservation_url, json={"status": "checked_out"})
    assert checked_out.status_code == 200


async def test_an_agent_without_department_access_cannot_use_its_hotel_module(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 6")

    ana = await as_agent(team["ana"]["email"])
    response = await ana.get(f"/api/departments/{department['id']}/hotel/room-types")
    assert response.status_code == 404

    await admin.put(
        f"/api/agents/{team['ana']['id']}/departments",
        json={"department_id": department["id"], "extra_department_ids": []},
    )
    assert (
        await ana.get(f"/api/departments/{department['id']}/hotel/room-types")
    ).status_code == 200


async def test_a_seasonal_rate_wins_over_the_default_when_it_applies(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 7")
    room_type = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/room-types",
            json={"name": "Doble", "capacity": 2},
        )
    ).json()
    await admin.post(
        f"/api/departments/{department['id']}/hotel/rate-plans",
        json={
            "room_type_id": room_type["id"],
            "name": "Tarifa base",
            "nightly_price_cents": 5_000,
        },
    )
    await admin.post(
        f"/api/departments/{department['id']}/hotel/rate-plans",
        json={
            "room_type_id": room_type["id"],
            "name": "Temporada alta",
            "starts_on": "2026-12-20",
            "ends_on": "2027-01-05",
            "nightly_price_cents": 9_000,
        },
    )

    # Entra en temporada alta: se aplica esa tarifa, no la base.
    room_a = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/rooms",
            json={"room_type_id": room_type["id"], "code": "501"},
        )
    ).json()
    high_season = await admin.post(
        f"/api/departments/{department['id']}/hotel/reservations",
        json={
            "room_id": room_a["id"],
            "guest_name": "Persona Huésped",
            "check_in": "2026-12-24",
            "check_out": "2026-12-27",
        },
    )
    assert high_season.status_code == 201
    assert high_season.json()["nightly_price_cents"] == 9_000

    # Fuera de temporada alta, en la misma categoría: se aplica la tarifa base.
    room_b = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/rooms",
            json={"room_type_id": room_type["id"], "code": "502"},
        )
    ).json()
    low_season = await admin.post(
        f"/api/departments/{department['id']}/hotel/reservations",
        json={
            "room_id": room_b["id"],
            "guest_name": "Otra Persona",
            "check_in": "2026-03-01",
            "check_out": "2026-03-03",
        },
    )
    assert low_season.status_code == 201
    assert low_season.json()["nightly_price_cents"] == 5_000


async def test_a_retired_room_type_rejects_new_rooms_and_new_reservations(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 8")
    room = await _hotel_room(admin, department["id"])
    room_type_id = room["room_type_id"]

    retired = await admin.patch(
        f"/api/departments/{department['id']}/hotel/room-types/{room_type_id}",
        json={"is_active": False},
    )
    assert retired.status_code == 200

    rejected_room = await admin.post(
        f"/api/departments/{department['id']}/hotel/rooms",
        json={"room_type_id": room_type_id, "code": "999"},
    )
    assert rejected_room.status_code == 409

    rejected_reservation = await admin.post(
        f"/api/departments/{department['id']}/hotel/reservations",
        json={
            "room_id": room["id"],
            "guest_name": "Persona Huésped",
            "check_in": "2027-01-10",
            "check_out": "2027-01-12",
        },
    )
    assert rejected_reservation.status_code == 409


async def test_hotel_operations_are_rejected_on_an_inactive_department(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 9")

    async with session_scope() as session:
        row = await repo.get_department(session, uuid.UUID(department["id"]))
        row.is_active = False

    response = await admin.get(f"/api/departments/{department['id']}/hotel/room-types")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Módulo Hotel: herramientas de IA para autoservicio
# --------------------------------------------------------------------------- #
async def _put_conversation_in_department(conversation_id: str, department_id: str) -> None:
    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        conversation.department_id = uuid.UUID(department_id)
        await session.flush()


def _tool_ctx(session: Any, conversation: Any, *, contact: Any = None) -> Any:
    """Contexto mínimo: hotel_booking.dispatch solo mira estos cuatro campos."""
    return SimpleNamespace(
        session=session,
        tenant=SimpleNamespace(id=conversation.tenant_id, slug="default"),
        conversation=conversation,
        contact=contact,
    )


async def test_the_ai_tool_lists_availability_with_price_and_capacity(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel IA 1")
    await _hotel_room(admin, department["id"], code="101")

    conversation_id = await arrive(anonymous, "web-hotel-ia-1")
    await _put_conversation_in_department(conversation_id, department["id"])

    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        result = await hotel_booking.dispatch(
            _tool_ctx(session, conversation),
            "consultar_disponibilidad_hotel",
            {"check_in": "2026-10-01", "check_out": "2026-10-03"},
        )
    assert "Doble 101" in result
    assert "1 libres" in result
    assert "50.00 USD/noche" in result


async def test_the_ai_tool_creates_a_pending_reservation_and_blocks_the_room(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel IA 2")
    room = await _hotel_room(admin, department["id"], code="201")

    conversation_id = await arrive(anonymous, "web-hotel-ia-2")
    await _put_conversation_in_department(conversation_id, department["id"])

    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        result = await hotel_booking.dispatch(
            _tool_ctx(session, conversation),
            "crear_reserva_hotel",
            {
                "room_type_name": "Doble 201",
                "check_in": "2026-10-10",
                "check_out": "2026-10-12",
                "guest_name": "Persona Huésped",
                "guests": 2,
            },
        )
    assert "pendiente" in result.lower()

    async with session_scope() as session:
        reservations = await repo.list_hotel_reservations(
            session, department_id=uuid.UUID(department["id"])
        )
        assert len(reservations) == 1
        assert reservations[0].status == "pending"
        assert reservations[0].nightly_price_cents == 5_000
        assert reservations[0].conversation_id == uuid.UUID(conversation_id)

    # La reserva pendiente ya bloquea la fecha, también para un agente humano.
    conflict = await admin.post(
        f"/api/departments/{department['id']}/hotel/reservations",
        json={
            "room_id": room["id"],
            "guest_name": "Otra Persona",
            "check_in": "2026-10-10",
            "check_out": "2026-10-12",
        },
    )
    assert conflict.status_code == 409


async def test_the_ai_tool_rejects_an_unknown_room_type(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel IA 3")

    conversation_id = await arrive(anonymous, "web-hotel-ia-3")
    await _put_conversation_in_department(conversation_id, department["id"])

    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        result = await hotel_booking.dispatch(
            _tool_ctx(session, conversation),
            "crear_reserva_hotel",
            {
                "room_type_name": "Categoría Inexistente",
                "check_in": "2026-10-10",
                "check_out": "2026-10-12",
                "guest_name": "Persona Huésped",
                "guests": 1,
            },
        )
    assert "no existe" in result.lower()

    async with session_scope() as session:
        reservations = await repo.list_hotel_reservations(
            session, department_id=uuid.UUID(department["id"])
        )
        assert reservations == []


async def test_hotel_dispatch_ignores_tools_it_does_not_own(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel IA 4")
    conversation_id = await arrive(anonymous, "web-hotel-ia-4")
    await _put_conversation_in_department(conversation_id, department["id"])

    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        result = await hotel_booking.dispatch(
            _tool_ctx(session, conversation),
            "derivar_a_agente",
            {"motivo": "x", "urgencia": "baja"},
        )
    assert result is None


async def test_hotel_tools_are_only_offered_once_the_module_is_active(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = (await admin.post("/api/departments", json={"name": "Hotel IA 5"})).json()
    conversation_id = await arrive(anonymous, "web-hotel-ia-5")
    handler = AIHandler(settings=get_settings())

    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        # Todavía en la cola común, sin departamento: ninguna herramienta extra.
        assert await handler._available_tools(_tool_ctx(session, conversation)) == []

        conversation.department_id = uuid.UUID(department["id"])
        await session.flush()
        # Con departamento pero sin el módulo activo: tampoco.
        assert await handler._available_tools(_tool_ctx(session, conversation)) == []

    await admin.put(f"/api/departments/{department['id']}/hotel/module", json={"enabled": True})

    async with session_scope() as session:
        conversation = await repo.get_conversation(session, uuid.UUID(conversation_id))
        assert (
            await handler._available_tools(_tool_ctx(session, conversation))
            == hotel_booking.HOTEL_TOOLS
        )


# --------------------------------------------------------------------------- #
# Módulo Hotel: edición, aviso de pendientes, contactos y reporte
# --------------------------------------------------------------------------- #
async def _create_hotel_reservation(
    admin: httpx.AsyncClient,
    department_id: str,
    *,
    room_id: str,
    check_in: str,
    check_out: str,
    guest_name: str = "Persona Huésped",
    **extra,
) -> dict:
    body = {
        "room_id": room_id,
        "guest_name": guest_name,
        "check_in": check_in,
        "check_out": check_out,
        **extra,
    }
    response = await admin.post(f"/api/departments/{department_id}/hotel/reservations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_editing_a_reservation_changes_its_dates_and_room(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 10")
    room_a = await _hotel_room(admin, department["id"], code="a1")
    room_b = await _hotel_room(admin, department["id"], code="b1")

    reservation = await _create_hotel_reservation(
        admin, department["id"], room_id=room_a["id"], check_in="2026-06-01", check_out="2026-06-03"
    )

    edited = await admin.patch(
        f"/api/departments/{department['id']}/hotel/reservations/{reservation['id']}",
        json={"room_id": room_b["id"], "check_in": "2026-06-05", "check_out": "2026-06-08"},
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["room_id"] == room_b["id"]
    assert body["check_in"] == "2026-06-05"
    assert body["check_out"] == "2026-06-08"

    # La habitación original quedó libre en las fechas de antes.
    free = await admin.get(
        f"/api/departments/{department['id']}/hotel/availability",
        params={"check_in": "2026-06-01", "check_out": "2026-06-03"},
    )
    assert room_a["id"] in ids(free.json())


async def test_editing_a_reservation_does_not_reject_itself_as_an_overlap(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 11")
    room = await _hotel_room(admin, department["id"])

    reservation = await _create_hotel_reservation(
        admin, department["id"], room_id=room["id"], check_in="2026-07-01", check_out="2026-07-04"
    )

    # Mismo cuarto, fechas que se solapan con las que ya tenía esta misma
    # reserva: no debería chocar contra sí misma.
    edited = await admin.patch(
        f"/api/departments/{department['id']}/hotel/reservations/{reservation['id']}",
        json={"check_in": "2026-07-02", "check_out": "2026-07-05"},
    )
    assert edited.status_code == 200, edited.text


async def test_editing_a_reservation_rejects_an_overlap_with_another_reservation(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 12")
    room = await _hotel_room(admin, department["id"])

    await _create_hotel_reservation(
        admin, department["id"], room_id=room["id"], check_in="2026-08-01", check_out="2026-08-05"
    )
    other = await _create_hotel_reservation(
        admin, department["id"], room_id=room["id"], check_in="2026-08-10", check_out="2026-08-12"
    )

    edited = await admin.patch(
        f"/api/departments/{department['id']}/hotel/reservations/{other['id']}",
        json={"check_in": "2026-08-02", "check_out": "2026-08-04"},
    )
    assert edited.status_code == 409


async def test_a_checked_out_reservation_cannot_be_edited(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 13")
    room = await _hotel_room(admin, department["id"])
    reservation = await _create_hotel_reservation(
        admin, department["id"], room_id=room["id"], check_in="2026-09-01", check_out="2026-09-03"
    )
    status_url = (
        f"/api/departments/{department['id']}/hotel/reservations/{reservation['id']}/status"
    )
    await admin.put(status_url, json={"status": "checked_in"})
    await admin.put(status_url, json={"status": "checked_out"})

    edited = await admin.patch(
        f"/api/departments/{department['id']}/hotel/reservations/{reservation['id']}",
        json={"check_in": "2026-09-10", "check_out": "2026-09-12"},
    )
    assert edited.status_code == 409


async def test_editing_a_rate_plan_changes_its_price(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 14")
    room_type = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/room-types", json={"name": "Doble"}
        )
    ).json()
    rate_plan = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/rate-plans",
            json={"room_type_id": room_type["id"], "name": "Base", "nightly_price_cents": 5_000},
        )
    ).json()

    edited = await admin.patch(
        f"/api/departments/{department['id']}/hotel/rate-plans/{rate_plan['id']}",
        json={"nightly_price_cents": 6_500},
    )
    assert edited.status_code == 200
    assert edited.json()["nightly_price_cents"] == 6_500

    listed = (await admin.get(f"/api/departments/{department['id']}/hotel/rate-plans")).json()
    assert listed[0]["nightly_price_cents"] == 6_500


async def test_editing_a_rate_plan_rejects_an_invalid_date_range(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 15")
    room_type = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/room-types", json={"name": "Doble"}
        )
    ).json()
    rate_plan = (
        await admin.post(
            f"/api/departments/{department['id']}/hotel/rate-plans",
            json={
                "room_type_id": room_type["id"],
                "name": "Temporada",
                "starts_on": "2026-12-01",
                "ends_on": "2026-12-20",
                "nightly_price_cents": 8_000,
            },
        )
    ).json()

    edited = await admin.patch(
        f"/api/departments/{department['id']}/hotel/rate-plans/{rate_plan['id']}",
        json={"ends_on": "2026-11-01"},
    )
    assert edited.status_code == 422


async def test_searching_hotel_contacts_finds_an_existing_one_by_email(
    anonymous, as_agent, team
):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 16")
    await arrive(anonymous, "web-hotel-contacto-1")

    found = await admin.get(
        f"/api/departments/{department['id']}/hotel/contacts",
        params={"q": "web-hotel-contacto-1"},
    )
    assert found.status_code == 200
    matches = found.json()
    assert len(matches) == 1
    assert matches[0]["primary_email"] == "web-hotel-contacto-1@clientes.local"


async def test_a_reservation_can_be_linked_to_an_existing_contact(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 17")
    room = await _hotel_room(admin, department["id"])
    await arrive(anonymous, "web-hotel-contacto-2")
    contacts = (
        await admin.get(
            f"/api/departments/{department['id']}/hotel/contacts",
            params={"q": "web-hotel-contacto-2"},
        )
    ).json()
    contact_id = contacts[0]["id"]

    reservation = await _create_hotel_reservation(
        admin,
        department["id"],
        room_id=room["id"],
        check_in="2026-10-01",
        check_out="2026-10-03",
        contact_id=contact_id,
    )
    assert reservation["contact_id"] == contact_id


async def test_hotel_report_counts_arrivals_departures_occupancy_and_revenue(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 18")
    departing = await _hotel_room(admin, department["id"], code="dep")
    arriving = await _hotel_room(admin, department["id"], code="arr")
    staying = await _hotel_room(admin, department["id"], code="sty")

    today = date.today()
    await _create_hotel_reservation(
        admin,
        department["id"],
        room_id=departing["id"],
        check_in=(today - timedelta(days=2)).isoformat(),
        check_out=today.isoformat(),
        nightly_price_cents=5_000,
    )
    await _create_hotel_reservation(
        admin,
        department["id"],
        room_id=arriving["id"],
        check_in=today.isoformat(),
        check_out=(today + timedelta(days=3)).isoformat(),
        nightly_price_cents=5_000,
    )
    await _create_hotel_reservation(
        admin,
        department["id"],
        room_id=staying["id"],
        check_in=(today - timedelta(days=1)).isoformat(),
        check_out=(today + timedelta(days=1)).isoformat(),
        nightly_price_cents=5_000,
    )

    report = await admin.get(f"/api/departments/{department['id']}/hotel/report")
    assert report.status_code == 200
    body = report.json()
    assert body["total_rooms"] == 3
    assert body["arrivals_today"] == 1
    assert body["departures_today"] == 1
    assert body["occupied_rooms"] == 2
    assert body["pending_count"] == 0
    # arr: 3 noches dentro de la ventana; sty: 1 noche; dep: 0 (ya terminó).
    assert body["revenue_next_30_days"] == [{"currency": "USD", "total_cents": 20_000}]


async def test_hotel_report_counts_pending_reservations(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department = await _hotel_department(admin, "Hotel 19")
    room = await _hotel_room(admin, department["id"])
    reservation = await _create_hotel_reservation(
        admin, department["id"], room_id=room["id"], check_in="2026-05-01", check_out="2026-05-03"
    )

    async with session_scope() as session:
        row = await repo.get_hotel_reservation(session, uuid.UUID(reservation["id"]))
        row.status = "pending"

    report = await admin.get(f"/api/departments/{department['id']}/hotel/report")
    assert report.json()["pending_count"] == 1
