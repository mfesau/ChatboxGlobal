"""Estado del sistema: las cifras que delatan un fallo antes que un cliente."""

from __future__ import annotations

import uuid
from datetime import timedelta

from app.core import health
from app.core.envelope import ChannelKind, utcnow
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import AIRun, OutboxItem
from tests.test_teamwork import arrive


async def _conversation(anonymous, nombre: str) -> tuple[uuid.UUID, uuid.UUID]:
    conversation_id = await arrive(anonymous, nombre)
    async with session_scope() as session:
        fila = await repo.get_conversation(session, uuid.UUID(conversation_id))
        return fila.tenant_id, fila.id


async def test_an_admin_sees_the_system_status(anonymous, as_agent, team):
    tenant_id, conversation_id = await _conversation(anonymous, "estado-1")
    async with session_scope() as session:
        for error in (None, None, "quota_exceeded: límite de 20"):
            session.add(
                AIRun(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    model="m",
                    handler="ai",
                    error=error,
                )
            )
        session.add(
            OutboxItem(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                channel=ChannelKind.WHATSAPP,
                status="dead",
                payload={},
                last_error="HTTP 401: token caducado",
            )
        )

    admin = await as_agent(team["admin"]["email"])
    respuesta = await admin.get("/api/admin/system")
    assert respuesta.status_code == 200, respuesta.text
    estado = respuesta.json()

    assert estado["ai"]["runs_24h"] == 3
    assert estado["ai"]["errors_24h"] == 1
    assert "quota_exceeded" in estado["ai"]["last_error"]["text"]
    whatsapp = next(fila for fila in estado["channels"] if fila["channel"] == "whatsapp")
    assert whatsapp["failures_24h"] == 1
    assert "token caducado" in whatsapp["last_error"]["text"]
    assert whatsapp["status"] == health.WARN
    web = next(fila for fila in estado["channels"] if fila["channel"] == "web")
    assert web["last_inbound_at"] is not None
    assert estado["outbox"]["failed_24h"] == 1
    assert estado["disk"]["total_bytes"] > 0
    assert estado["status"] in {health.WARN, health.ERROR}


async def test_a_message_stuck_in_the_outbox_turns_it_red(anonymous, as_agent, team):
    tenant_id, conversation_id = await _conversation(anonymous, "estado-2")
    async with session_scope() as session:
        atascado = OutboxItem(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            channel=ChannelKind.WEB,
            status="pending",
            payload={},
        )
        session.add(atascado)
        await session.flush()
        atascado.created_at = utcnow() - timedelta(hours=2)

    admin = await as_agent(team["admin"]["email"])
    outbox = (await admin.get("/api/admin/system")).json()["outbox"]

    assert outbox["pending"] >= 1
    assert outbox["oldest_pending_seconds"] >= 2 * 3600 - 60
    assert outbox["status"] == health.ERROR


async def test_many_ai_failures_turn_it_red(anonymous, as_agent, team):
    tenant_id, conversation_id = await _conversation(anonymous, "estado-3")
    async with session_scope() as session:
        for _ in range(health.AI_MIN_RUNS):
            session.add(
                AIRun(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    model="m",
                    handler="ai",
                    error="api_error",
                )
            )

    admin = await as_agent(team["admin"]["email"])
    ai = (await admin.get("/api/admin/system")).json()["ai"]
    assert ai["status"] == health.ERROR


async def test_only_administration_sees_the_system_status(as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    assert (await marta.get("/api/admin/system")).status_code == 403
