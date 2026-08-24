"""Pruebas del trabajo en equipo: acceso, alcance por rol, derivación y notas.

Escenario compartido: dos agentes —Ana y Luis— y una supervisora, Marta. Todas
las conversaciones entran por la cola común, sea cual sea el canal.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, ClassVar

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.channels.base import ChannelRegistry
from app.config import get_settings
from app.core.orchestrator import Orchestrator
from app.core.pipeline import Handler, NextFn, Pipeline, TurnContext
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
from app.db.models import Assignment, Conversation, InternalNote, Message
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
