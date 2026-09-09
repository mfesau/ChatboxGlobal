"""Traza de auditoría y derechos del interesado (RGPD).

Se apoya en el andamiaje de ``tests/test_teamwork.py``: el mismo equipo, los
mismos clientes autenticados y el mismo transporte ASGI en memoria.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.core.envelope import ChannelKind, ConversationRef
from app.core.privacy import ANONYMISED_NAME
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import ContactIdentity, Message


async def _a_contact_with_a_conversation(team) -> tuple[str, str]:
    """Deja un contacto con una conversación y un mensaje con adjunto.

    Devuelve ``(contact_id, ruta_del_fichero)``. El fichero se escribe de
    verdad: el borrado tiene que llevárselo del disco, no solo de la fila.
    """
    settings = get_settings()
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        contact = await repo.create_contact_account(
            session,
            tenant_id=tenant.id,
            email="cliente.rgpd@ejemplo.local",
            display_name="Cliente RGPD",
            password_hash="",
        )
        session.add(
            ContactIdentity(
                tenant_id=tenant.id,
                contact_id=contact.id,
                channel=ChannelKind.WHATSAPP,
                channel_user_id="34600111222",
            )
        )
        conversation = await repo.resolve_conversation(
            session,
            tenant_id=tenant.id,
            ref=ConversationRef(
                channel=ChannelKind.WHATSAPP, channel_conversation_id="34600111222"
            ),
            contact_id=contact.id,
        )

        directory = Path(settings.uploads_dir) / str(tenant.id)
        directory.mkdir(parents=True, exist_ok=True)
        stored = directory / f"{'a' * 32}.jpg"
        stored.write_bytes(b"contenido de prueba")

        session.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                channel=ChannelKind.WHATSAPP,
                direction="inbound",
                text="Mi número es el 600 111 222",
                attachments=[{"url": f"/uploads/{tenant.id}/{stored.name}"}],
                author_type="contact",
                author_contact_id=contact.id,
                provider_message_id="wamid.rgpd.1",
            )
        )
        return str(contact.id), str(stored)


# --------------------------------------------------------------------------- #
# Auditoría
# --------------------------------------------------------------------------- #
async def test_the_audit_trail_can_finally_be_read(as_agent, team):
    """Se venía escribiendo desde media aplicación sin forma de consultarla."""
    admin = await as_agent(team["admin"]["email"])
    # Una acción cualquiera que deje traza.
    await admin.patch(f"/api/agents/{team['ana']['id']}", json={"display_name": "Ana R."})

    listed = await admin.get("/api/audit")
    assert listed.status_code == 200
    acciones = [row["action"] for row in listed.json()]
    assert "agent_updated" in acciones

    # Y el desplegable del filtro se puebla con lo que hay de verdad.
    actions = await admin.get("/api/audit/actions")
    assert actions.status_code == 200
    assert "agent_updated" in actions.json()


async def test_the_audit_trail_filters_by_actor_and_action(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    await admin.patch(f"/api/agents/{team['ana']['id']}", json={"display_name": "Ana R."})

    # El actor se guarda con prefijo ("agent:admin@…"); se busca por el correo.
    por_actor = await admin.get(f"/api/audit?actor={team['admin']['email']}")
    assert por_actor.status_code == 200
    assert por_actor.json()
    assert all(team["admin"]["email"] in row["actor"] for row in por_actor.json())

    ninguno = await admin.get("/api/audit?action=accion_que_no_existe")
    assert ninguno.json() == []


async def test_an_agent_cannot_read_the_audit_trail(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    assert (await ana.get("/api/audit")).status_code == 403


async def test_supervision_can_read_the_audit_trail(as_agent, team):
    """No es privilegio de administración: quien responde de un turno la necesita."""
    marta = await as_agent(team["marta"]["email"])
    assert (await marta.get("/api/audit")).status_code == 200


# --------------------------------------------------------------------------- #
# Exportación
# --------------------------------------------------------------------------- #
async def test_the_export_gathers_everything_held_about_a_contact(as_agent, team):
    contact_id, _ = await _a_contact_with_a_conversation(team)
    marta = await as_agent(team["marta"]["email"])

    response = await marta.get(f"/api/contacts/{contact_id}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"

    bundle = json.loads(response.text)
    assert bundle["contact"]["primary_email"] == "cliente.rgpd@ejemplo.local"
    assert bundle["identities"][0]["channel_user_id"] == "34600111222"
    assert len(bundle["conversations"]) == 1
    assert bundle["messages"][0]["text"] == "Mi número es el 600 111 222"


async def test_an_agent_cannot_export_a_contact(as_agent, team):
    contact_id, _ = await _a_contact_with_a_conversation(team)
    ana = await as_agent(team["ana"]["email"])
    assert (await ana.get(f"/api/contacts/{contact_id}/export")).status_code == 403


# --------------------------------------------------------------------------- #
# Borrado
# --------------------------------------------------------------------------- #
async def test_erasing_needs_an_explicit_confirmation(as_agent, team):
    contact_id, _ = await _a_contact_with_a_conversation(team)
    admin = await as_agent(team["admin"]["email"])

    sin_confirmar = await admin.post(f"/api/contacts/{contact_id}/erase")
    assert sin_confirmar.status_code == 409

    async with session_scope() as session:
        contact = await repo.get_contact(session, uuid.UUID(contact_id))
        assert contact.display_name == "Cliente RGPD"


async def test_supervision_cannot_erase(as_agent, team):
    """El borrado no se deshace: se reserva a administración."""
    contact_id, _ = await _a_contact_with_a_conversation(team)
    marta = await as_agent(team["marta"]["email"])
    assert (
        await marta.post(f"/api/contacts/{contact_id}/erase?confirm=true")
    ).status_code == 403


async def test_erasing_removes_the_personal_data_and_keeps_the_skeleton(as_agent, team):
    contact_id, stored_path = await _a_contact_with_a_conversation(team)
    admin = await as_agent(team["admin"]["email"])

    response = await admin.post(f"/api/contacts/{contact_id}/erase?confirm=true")
    assert response.status_code == 200
    assert response.json()["messages"] == 1
    assert response.json()["files"] == 1

    # El fichero se fue del disco, no solo de la fila.
    assert not Path(stored_path).exists()

    async with session_scope() as session:
        contact = await repo.get_contact(session, uuid.UUID(contact_id))
        assert contact.display_name == ANONYMISED_NAME
        assert contact.primary_email is None
        assert contact.primary_phone is None
        assert contact.password_hash is None

        # El esqueleto sigue ahí: los informes no se falsean.
        conversations = await repo.list_conversations_for_contact(session, contact.id)
        assert len(conversations) == 1

        messages = await repo.recent_messages(session, conversations[0].id)
        assert len(messages) == 1
        assert messages[0].text is None
        assert messages[0].attachments == []

        identities = list(
            (
                await session.execute(
                    select(ContactIdentity).where(ContactIdentity.contact_id == contact.id)
                )
            ).scalars()
        )
        assert identities[0].channel_user_id.startswith("anon-")


async def test_erasing_leaves_its_own_trace(as_agent, team):
    contact_id, _ = await _a_contact_with_a_conversation(team)
    admin = await as_agent(team["admin"]["email"])
    await admin.post(f"/api/contacts/{contact_id}/erase?confirm=true")

    trace = await admin.get("/api/audit?action=contact_erased")
    assert trace.status_code == 200
    assert trace.json()[0]["subject_id"] == contact_id


# --------------------------------------------------------------------------- #
# Caducidad del contenido
# --------------------------------------------------------------------------- #
async def test_expiring_a_message_also_removes_its_files(team):
    """Vaciar solo la fila dejaba los adjuntos acumulándose en el disco."""
    from datetime import UTC, datetime, timedelta

    from app.core.privacy import purge_expired_messages

    contact_id, stored_path = await _a_contact_with_a_conversation(team)

    # El mensaje se envejece para que caiga dentro del plazo.
    async with session_scope() as session:
        mensajes = list((await session.execute(select(Message))).scalars())
        for mensaje in mensajes:
            mensaje.created_at = datetime.now(UTC) - timedelta(days=400)

    assert Path(stored_path).exists()

    async with session_scope() as session:
        resumen = await purge_expired_messages(
            session,
            older_than=datetime.now(UTC) - timedelta(days=365),
            settings=get_settings(),
        )

    assert resumen["messages"] == 1
    assert resumen["files"] == 1
    assert not Path(stored_path).exists()

    async with session_scope() as session:
        [mensaje] = list((await session.execute(select(Message))).scalars())
        assert mensaje.text is None
        assert mensaje.attachments == []
        # La fila sigue ahí: de ella salen los recuentos de los informes.
        assert mensaje.conversation_id is not None


async def test_a_recent_message_is_left_alone(team):
    from datetime import UTC, datetime, timedelta

    from app.core.privacy import purge_expired_messages

    contact_id, stored_path = await _a_contact_with_a_conversation(team)

    async with session_scope() as session:
        resumen = await purge_expired_messages(
            session,
            older_than=datetime.now(UTC) - timedelta(days=365),
            settings=get_settings(),
        )

    assert resumen == {"messages": 0, "files": 0}
    assert Path(stored_path).exists()


async def test_sweeping_twice_does_not_rewrite_anything(team):
    """Solo toca lo que aún tiene algo dentro."""
    from datetime import UTC, datetime, timedelta

    from app.core.privacy import purge_expired_messages

    await _a_contact_with_a_conversation(team)
    async with session_scope() as session:
        for mensaje in (await session.execute(select(Message))).scalars():
            mensaje.created_at = datetime.now(UTC) - timedelta(days=400)

    limite = datetime.now(UTC) - timedelta(days=365)
    async with session_scope() as session:
        primera = await purge_expired_messages(session, older_than=limite, settings=get_settings())
    async with session_scope() as session:
        segunda = await purge_expired_messages(session, older_than=limite, settings=get_settings())

    assert primera["messages"] == 1
    assert segunda == {"messages": 0, "files": 0}
