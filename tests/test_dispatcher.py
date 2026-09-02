"""Pruebas de la cola de salida: entrega, reintentos y descarte definitivo."""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import select

from app.channels.base import ChannelAdapter, ChannelRegistry
from app.config import get_settings
from app.core.dispatcher import OutboxDispatcher
from app.core.envelope import (
    ChannelKind,
    ConversationRef,
    DeliveryReceipt,
    DeliveryStatus,
    OutboundMessage,
)
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import Message, MessageEvent, OutboxItem


class ScriptedAdapter(ChannelAdapter):
    """Adaptador de prueba que devuelve acuses predefinidos."""

    kind: ClassVar[ChannelKind] = ChannelKind.WEB

    def __init__(self, settings, receipts: list[DeliveryReceipt]) -> None:
        super().__init__(settings)
        self._receipts = receipts
        self.sent: list[OutboundMessage] = []

    async def parse(self, *, payload, headers):  # pragma: no cover - no se usa
        return []

    async def send(self, *, ref: ConversationRef, message: OutboundMessage) -> DeliveryReceipt:
        self.sent.append(message)
        if self._receipts:
            return self._receipts.pop(0)
        return DeliveryReceipt.sent("provider-final")


class CrashingAdapter(ScriptedAdapter):
    async def send(self, *, ref, message):
        raise RuntimeError("el proveedor cerró la conexión")


def build_dispatcher(adapter: ChannelAdapter) -> OutboxDispatcher:
    settings = get_settings()
    registry = ChannelRegistry(settings)
    # Se inyecta la instancia para no invocar al proveedor real.
    registry._instances[ChannelKind.WEB] = adapter
    return OutboxDispatcher(settings=settings, registry=registry)


async def seed_outbound(text: str = "respuesta") -> tuple[OutboxItem, Message]:
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        conversation = await repo.resolve_conversation(
            session,
            tenant_id=tenant.id,
            ref=ConversationRef(
                channel=ChannelKind.WEB, channel_conversation_id="hilo-salida"
            ),
            contact_id=None,
        )
        outbound = OutboundMessage(text=text)
        message = await repo.record_outbound(
            session, conversation=conversation, outbound=outbound
        )
        item = await repo.enqueue_outbound(
            session,
            conversation=conversation,
            message=message,
            ref=ConversationRef.from_dict(conversation.conversation_ref),
            outbound=outbound,
        )
        return item, message


# --------------------------------------------------------------------------- #
# Entrega correcta
# --------------------------------------------------------------------------- #
async def test_successful_delivery_marks_message_as_sent():
    settings = get_settings()
    adapter = ScriptedAdapter(settings, [DeliveryReceipt.sent("prov-1")])
    dispatcher = build_dispatcher(adapter)

    await seed_outbound("hola desde la cola")
    assert await dispatcher._drain_once("worker-test") == 1

    async with session_scope() as session:
        item = (await session.execute(select(OutboxItem))).scalar_one()
        message = (await session.execute(select(Message))).scalar_one()

    assert item.status == "sent"
    assert item.attempts == 1
    assert item.locked_by is None
    assert message.status is DeliveryStatus.SENT
    assert message.provider_message_id == "prov-1"
    assert message.sent_at is not None
    assert adapter.sent[0].text == "hola desde la cola"


async def test_empty_queue_reports_no_work():
    dispatcher = build_dispatcher(ScriptedAdapter(get_settings(), []))
    assert await dispatcher._drain_once("worker-test") == 0


# --------------------------------------------------------------------------- #
# Reintentos
# --------------------------------------------------------------------------- #
async def test_retryable_failure_is_rescheduled_with_backoff():
    adapter = ScriptedAdapter(
        get_settings(),
        [DeliveryReceipt.failed("http_503", "servicio no disponible", retryable=True)],
    )
    dispatcher = build_dispatcher(adapter)
    await seed_outbound()

    await dispatcher._drain_once("worker-test")

    async with session_scope() as session:
        item = (await session.execute(select(OutboxItem))).scalar_one()
        message = (await session.execute(select(Message))).scalar_one()

    assert item.status == "pending"
    assert item.attempts == 1
    assert item.next_attempt_at > item.created_at
    assert "http_503" in (item.last_error or "")
    # El mensaje sigue pendiente: aún no se ha agotado la política de reintentos.
    assert message.status is DeliveryStatus.PENDING


async def test_permanent_failure_stops_immediately():
    adapter = ScriptedAdapter(
        get_settings(),
        [DeliveryReceipt.failed("http_400", "número no válido", retryable=False)],
    )
    dispatcher = build_dispatcher(adapter)
    await seed_outbound()

    await dispatcher._drain_once("worker-test")

    async with session_scope() as session:
        item = (await session.execute(select(OutboxItem))).scalar_one()
        message = (await session.execute(select(Message))).scalar_one()
        events = list((await session.execute(select(MessageEvent))).scalars())

    assert item.status == "failed"
    assert item.attempts == 1
    assert message.status is DeliveryStatus.FAILED
    assert events[-1].status is DeliveryStatus.FAILED


async def test_retries_exhaust_into_the_dead_queue():
    settings = get_settings()
    attempts = settings.outbox_max_attempts
    adapter = ScriptedAdapter(
        settings,
        [
            DeliveryReceipt.failed("http_500", "error interno", retryable=True)
            for _ in range(attempts)
        ],
    )
    dispatcher = build_dispatcher(adapter)
    await seed_outbound()

    for _ in range(attempts):
        # Se adelanta el reloj de la cola para no esperar el retroceso real.
        async with session_scope() as session:
            item = (await session.execute(select(OutboxItem))).scalar_one()
            item.next_attempt_at = item.created_at
        await dispatcher._drain_once("worker-test")

    async with session_scope() as session:
        item = (await session.execute(select(OutboxItem))).scalar_one()
        message = (await session.execute(select(Message))).scalar_one()

    assert item.attempts == attempts
    assert item.status == "dead"
    assert message.status is DeliveryStatus.FAILED


async def test_adapter_exception_is_treated_as_retryable():
    dispatcher = build_dispatcher(CrashingAdapter(get_settings(), []))
    await seed_outbound()

    await dispatcher._drain_once("worker-test")

    async with session_scope() as session:
        item = (await session.execute(select(OutboxItem))).scalar_one()

    assert item.status == "pending"
    assert "RuntimeError" in (item.last_error or "")


# --------------------------------------------------------------------------- #
# Mantenimiento
# --------------------------------------------------------------------------- #
async def test_stale_lock_is_requeued():
    """Un trabajador que muere a mitad de envío no debe bloquear el mensaje."""
    from datetime import timedelta

    await seed_outbound()
    async with session_scope() as session:
        item = (await session.execute(select(OutboxItem))).scalar_one()
        item.status = "in_progress"
        item.locked_by = "worker-caido"
        item.locked_at = item.created_at - timedelta(hours=1)

    async with session_scope() as session:
        requeued = await repo.requeue_stale_outbox(session, older_than=timedelta(minutes=5))

    assert requeued == 1
    async with session_scope() as session:
        item = (await session.execute(select(OutboxItem))).scalar_one()
    assert item.status == "pending"
    assert item.locked_by is None


async def test_dedupe_keys_are_purged_by_age():
    from datetime import timedelta

    from app.db.models import InboundDedupe

    async with session_scope() as session:
        assert await repo.claim_dedupe_key(session, "web:antigua", ChannelKind.WEB) is True
        assert await repo.claim_dedupe_key(session, "web:reciente", ChannelKind.WEB) is True

    async with session_scope() as session:
        old = await session.get(InboundDedupe, "web:antigua")
        assert old is not None
        old.received_at = old.received_at - timedelta(days=10)

    async with session_scope() as session:
        purged = await repo.purge_dedupe_keys(session, timedelta(days=3))

    assert purged == 1
    async with session_scope() as session:
        assert await session.get(InboundDedupe, "web:antigua") is None
        assert await session.get(InboundDedupe, "web:reciente") is not None
