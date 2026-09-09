"""El hilo del chatbox: cierre visible para el cliente y conversación nueva.

Hasta aquí el hilo era el propio contacto y se reabría solo al escribir, de
modo que dar una conversación por resuelta no cambiaba nada de lo que el
cliente veía.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core import web_thread
from app.core.envelope import ChannelKind
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import Conversation
from tests.test_teamwork import CLIENT_PASSWORD, arrive


async def _contact_client(anonymous, session_id: str = "cliente-hilo"):
    """Deja un cliente registrado, con su conversación empezada y su sesión.

    ``arrive`` levanta un cliente propio para registrar y escribir, así que la
    cookie se queda allí dentro; aquí hace falta también en el cliente de la
    prueba, que es el que va a pedir la conversación nueva.
    """
    await arrive(anonymous, session_id, "Buenas, tengo una consulta")
    login = await anonymous.post(
        "/api/contact/login",
        json={"email": f"{session_id}@clientes.local", "password": CLIENT_PASSWORD},
    )
    assert login.status_code == 200, login.text
    return anonymous


async def _conversations_of(contact_id: uuid.UUID) -> list[Conversation]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(Conversation)
                    .where(Conversation.contact_id == contact_id)
                    .order_by(Conversation.created_at)
                )
            ).scalars()
        )


async def _contact_id(email: str) -> uuid.UUID:
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        contact = await repo.find_contact_by_email(session, tenant_id=tenant.id, email=email)
        return contact.id


# --------------------------------------------------------------------------- #
# Identificador del hilo
# --------------------------------------------------------------------------- #
def test_a_new_thread_id_is_not_guessable_from_the_previous_one():
    """Dos pestañas pidiendo hilo nuevo a la vez no deben chocar."""
    from types import SimpleNamespace

    contacto = SimpleNamespace(id=uuid.uuid4())
    primero = web_thread.first_thread_id(contacto)
    nuevos = {web_thread.new_thread_id(contacto) for _ in range(20)}

    assert primero == str(contacto.id)
    assert len(nuevos) == 20
    assert primero not in nuevos
    assert all(n.startswith(f"{contacto.id}:") for n in nuevos)


# --------------------------------------------------------------------------- #
# El cierre se ve desde el chatbox
# --------------------------------------------------------------------------- #
async def test_closing_a_conversation_is_visible_to_the_client(anonymous, as_agent, team):
    await _contact_client(anonymous)
    admin = await as_agent(team["admin"]["email"])

    hilos = (await admin.get("/api/conversations?scope=all")).json()
    conversation_id = next(row["id"] for row in hilos if row["channel"] == "web")

    cerrada = await admin.post(
        f"/api/conversations/{conversation_id}/state", json={"state": "solved"}
    )
    assert cerrada.status_code == 200

    # Lo que el chatbox recibe al abrir el socket lleva ya el estado.
    from app.api.ws import _thread_snapshot

    async with session_scope() as session:
        conversation = await session.get(Conversation, uuid.UUID(conversation_id))
        hilo = conversation.channel_conversation_id
    foto = await _thread_snapshot(hilo, "default")
    assert foto["status"] == "closed"


# --------------------------------------------------------------------------- #
# Conversación nueva
# --------------------------------------------------------------------------- #
async def test_a_new_conversation_needs_the_previous_one_closed(anonymous, as_agent, team):
    """Abandonar a medias dejaría al agente hablando solo."""
    cliente = await _contact_client(anonymous)

    negado = await cliente.post("/api/contact/conversations")
    assert negado.status_code == 409


async def test_a_closed_conversation_can_be_followed_by_a_new_one(anonymous, as_agent, team):
    cliente = await _contact_client(anonymous)
    admin = await as_agent(team["admin"]["email"])

    hilos = (await admin.get("/api/conversations?scope=all")).json()
    conversation_id = next(row["id"] for row in hilos if row["channel"] == "web")
    await admin.post(f"/api/conversations/{conversation_id}/state", json={"state": "solved"})

    nueva = await cliente.post("/api/contact/conversations")
    assert nueva.status_code == 201, nueva.text

    contact_id = await _contact_id("cliente-hilo@clientes.local")
    hilos = await _conversations_of(contact_id)
    assert len(hilos) == 2
    # La vieja se queda cerrada, con su historial; la nueva nace vacía.
    assert hilos[0].status == "closed"
    assert hilos[1].status == "open"
    assert hilos[1].channel_conversation_id != hilos[0].channel_conversation_id

    async with session_scope() as session:
        assert await repo.recent_messages(session, hilos[1].id) == []


async def test_the_new_conversation_inherits_the_branch(anonymous, as_agent, team):
    """Quien ya eligió rama no tiene que volver a elegir para otra consulta."""
    admin = await as_agent(team["admin"]["email"])
    creado = await admin.post("/api/departments", json={"name": "Soporte"})
    department_id = uuid.UUID(creado.json()["id"])

    cliente = await _contact_client(anonymous)
    contact_id = await _contact_id("cliente-hilo@clientes.local")

    async with session_scope() as session:
        [hilo] = list(
            (
                await session.execute(
                    select(Conversation).where(Conversation.contact_id == contact_id)
                )
            ).scalars()
        )
        hilo.department_id = department_id
        hilo.status = "closed"

    assert (await cliente.post("/api/contact/conversations")).status_code == 201

    hilos = await _conversations_of(contact_id)
    assert hilos[-1].department_id == department_id


async def test_writing_again_lands_in_the_new_thread(anonymous, as_agent, team):
    """El socket y el REST tienen que coincidir en cuál es el hilo vigente."""
    cliente = await _contact_client(anonymous)
    contact_id = await _contact_id("cliente-hilo@clientes.local")

    async with session_scope() as session:
        conversation = await web_thread.current(session, await _contact(session, contact_id))
        conversation.status = "closed"

    assert (await cliente.post("/api/contact/conversations")).status_code == 201

    respuesta = await cliente.post("/api/web/messages", json={"text": "Otra consulta distinta"})
    assert respuesta.status_code == 200, respuesta.text

    hilos = await _conversations_of(contact_id)
    assert len(hilos) == 2
    async with session_scope() as session:
        viejos = await repo.recent_messages(session, hilos[0].id)
        nuevos = await repo.recent_messages(session, hilos[1].id)
    assert not any("Otra consulta distinta" in (m.text or "") for m in viejos)
    assert any("Otra consulta distinta" in (m.text or "") for m in nuevos)


async def _contact(session, contact_id: uuid.UUID):
    return await repo.get_contact(session, contact_id)


# --------------------------------------------------------------------------- #
# Compatibilidad
# --------------------------------------------------------------------------- #
async def test_an_existing_conversation_keeps_its_identifier(anonymous):
    """Las que ya existían siguen siendo las suyas sin migrar nada."""
    await _contact_client(anonymous)
    contact_id = await _contact_id("cliente-hilo@clientes.local")

    [hilo] = await _conversations_of(contact_id)
    assert hilo.channel_conversation_id == str(contact_id)

    async with session_scope() as session:
        contacto = await repo.get_contact(session, contact_id)
        assert await web_thread.current_thread_id(session, contacto) == str(contact_id)


async def test_a_contact_that_never_wrote_gets_the_first_identifier(anonymous):
    from app.core.security import hash_password

    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        contacto = await repo.create_contact_account(
            session,
            tenant_id=tenant.id,
            email="nunca.escribio@ejemplo.local",
            display_name="Nunca",
            password_hash=hash_password(CLIENT_PASSWORD),
        )
        assert await web_thread.current_thread_id(session, contacto) == str(contacto.id)
        assert await web_thread.current(session, contacto) is None


@pytest.mark.parametrize("kind", [ChannelKind.WEB])
def test_the_reference_carries_the_web_account(kind):
    ref = web_thread.ref_for("un-hilo")
    assert ref.channel is kind
    assert ref.channel_conversation_id == "un-hilo"
    assert ref.channel_account_id == web_thread.WEB_ACCOUNT
