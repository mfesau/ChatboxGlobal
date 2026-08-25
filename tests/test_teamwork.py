"""Pruebas del trabajo en equipo: acceso, alcance por rol, derivación y notas.

Escenario compartido: dos agentes —Ana y Luis— y una supervisora, Marta. Todas
las conversaciones entran por la cola común, sea cual sea el canal.
"""

from __future__ import annotations

import urllib.parse
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, ClassVar

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.channels.base import ChannelRegistry
from app.config import get_settings
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
from app.db.models import ROLE_ADMIN, ROLE_AGENT, Assignment, Conversation, InternalNote, Message
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
        pipeline=Pipeline([EchoHandler()]),
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
    assert response.json() == {"available": False}


async def test_saml_routes_are_hidden_when_not_configured(anonymous):
    assert (await anonymous.get("/saml/metadata")).status_code == 404
    assert (await anonymous.get("/saml/login")).status_code == 404
    assert (await anonymous.post("/saml/acs")).status_code == 404


async def test_sso_status_reports_available_once_configured(anonymous, monkeypatch):
    _configure_saml(monkeypatch, get_settings())
    response = await anonymous.get("/api/auth/sso")
    assert response.json() == {"available": True}


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
