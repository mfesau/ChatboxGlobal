"""Pruebas de integración de la capa de orquestación sobre base de datos real.

Cubren el recorrido completo: recepción, normalización, idempotencia,
persistencia, ejecución de la cadena de handlers y encolado de la salida.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from sqlalchemy import func, select

from app.channels.base import ChannelRegistry
from app.config import get_settings
from app.core.envelope import (
    ChannelKind,
    ContentType,
    ConversationRef,
    DeliveryStatus,
    Direction,
    InboundMessage,
    Party,
)
from app.core.orchestrator import Orchestrator
from app.core.pipeline import Handler, NextFn, Pipeline, TurnContext
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import Contact, Conversation, Message, OutboxItem


class EchoHandler(Handler):
    """Handler determinista: evita depender del modelo en las pruebas."""

    name: ClassVar[str] = "echo"

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        if ctx.text:
            ctx.reply(f"eco: {ctx.text}")
        else:
            await next_()


def build_orchestrator(handlers: list[Handler] | None = None) -> Orchestrator:
    settings = get_settings()
    return Orchestrator(
        settings=settings,
        registry=ChannelRegistry(settings),
        pipeline=Pipeline(handlers if handlers is not None else [EchoHandler()]),
    )


def web_message(text: str, *, message_id: str, session_id: str = "web-sesion-1") -> InboundMessage:
    return InboundMessage(
        channel=ChannelKind.WEB,
        conversation=ConversationRef(
            channel=ChannelKind.WEB,
            channel_conversation_id=session_id,
            channel_account_id="web",
        ),
        sender=Party(channel_user_id=session_id, display_name="Visitante"),
        provider_message_id=message_id,
        text=text,
    )


async def count(model) -> int:
    async with session_scope() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --------------------------------------------------------------------------- #
# Recorrido completo
# --------------------------------------------------------------------------- #
async def test_inbound_creates_thread_persists_and_enqueues_reply():
    orchestrator = build_orchestrator()

    assert await orchestrator.process_inbound(web_message("hola", message_id="m-1")) is True

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        assert conversation.channel is ChannelKind.WEB
        assert conversation.channel_conversation_id == "web-sesion-1"
        assert conversation.contact_id is not None
        assert conversation.last_message_at is not None

        messages = await repo.recent_messages(session, conversation.id, limit=10)
        assert [m.direction for m in messages] == [Direction.INBOUND, Direction.OUTBOUND]
        assert messages[0].text == "hola"
        assert messages[1].text == "eco: hola"
        assert messages[1].status is DeliveryStatus.PENDING
        assert messages[1].author_type == "bot"

        outbox = (await session.execute(select(OutboxItem))).scalar_one()
        assert outbox.status == "pending"
        assert outbox.payload["message"]["text"] == "eco: hola"
        assert outbox.payload["ref"]["channel_conversation_id"] == "web-sesion-1"


async def test_duplicate_provider_id_is_dropped():
    """Los proveedores reintentan la entrega; el segundo intento no debe responder."""
    orchestrator = build_orchestrator()

    assert await orchestrator.process_inbound(web_message("hola", message_id="m-1")) is True
    assert await orchestrator.process_inbound(web_message("hola", message_id="m-1")) is False

    assert await count(Message) == 2
    assert await count(OutboxItem) == 1


async def test_second_message_reuses_thread_and_contact():
    orchestrator = build_orchestrator()

    await orchestrator.process_inbound(web_message("primero", message_id="m-1"))
    await orchestrator.process_inbound(web_message("segundo", message_id="m-2"))

    assert await count(Conversation) == 1
    assert await count(Message) == 4


async def test_distinct_sessions_create_distinct_threads():
    orchestrator = build_orchestrator()

    await orchestrator.process_inbound(web_message("hola", message_id="m-1", session_id="ses-a"))
    await orchestrator.process_inbound(web_message("hola", message_id="m-2", session_id="ses-b"))

    assert await count(Conversation) == 2


# --------------------------------------------------------------------------- #
# Control humano y supresión de respuesta
# --------------------------------------------------------------------------- #
async def test_human_control_suppresses_automatic_reply():
    from app.handlers.builtin import HumanControlHandler

    orchestrator = build_orchestrator([HumanControlHandler(), EchoHandler()])
    await orchestrator.process_inbound(web_message("hola", message_id="m-1"))

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        await repo.set_conversation_control(session, conversation.id, "human")

    await orchestrator.process_inbound(web_message("¿hay alguien?", message_id="m-2"))

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        messages = await repo.recent_messages(session, conversation.id, limit=10)

    # Primer turno: entrante y respuesta. Segundo turno: solo el entrante.
    assert [m.direction for m in messages] == [
        Direction.INBOUND,
        Direction.OUTBOUND,
        Direction.INBOUND,
    ]


async def test_blocked_contact_is_ignored():
    orchestrator = build_orchestrator()
    await orchestrator.process_inbound(web_message("hola", message_id="m-1"))

    async with session_scope() as session:
        contact = (await session.execute(select(Contact))).scalar_one()
        contact.is_blocked = True

    assert await orchestrator.process_inbound(web_message("otra vez", message_id="m-2")) is False
    assert await count(Message) == 2


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #
async def test_handoff_command_transfers_control_to_a_person():
    from app.handlers.builtin import CommandHandler

    orchestrator = build_orchestrator([CommandHandler(), EchoHandler()])
    await orchestrator.process_inbound(web_message("/agente", message_id="m-1"))

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        assert conversation.control == "human"
        messages = await repo.recent_messages(session, conversation.id, limit=10)

    assert "una persona del equipo" in (messages[-1].text or "")


async def test_fallback_guarantees_a_reply_when_no_handler_answers():
    from app.handlers.builtin import FallbackHandler

    class SilentHandler(Handler):
        name: ClassVar[str] = "silent"

        async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
            await next_()

    orchestrator = build_orchestrator([FallbackHandler(), SilentHandler()])
    await orchestrator.process_inbound(web_message("consulta suelta", message_id="m-1"))

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        messages = await repo.recent_messages(session, conversation.id, limit=10)

    assert messages[-1].direction is Direction.OUTBOUND
    assert "/agente" in (messages[-1].text or "")


async def test_fallback_only_replies_to_the_first_unresolved_message():
    from app.handlers.builtin import FallbackHandler

    class SilentHandler(Handler):
        name: ClassVar[str] = "silent"

        async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
            await next_()

    orchestrator = build_orchestrator([FallbackHandler(), SilentHandler()])
    await orchestrator.process_inbound(web_message("primera consulta suelta", message_id="m-1"))
    await orchestrator.process_inbound(web_message("segunda consulta suelta", message_id="m-2"))

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        messages = await repo.recent_messages(session, conversation.id, limit=10)

    outbound = [m for m in messages if m.direction is Direction.OUTBOUND]
    assert len(outbound) == 1
    assert "/agente" in (outbound[0].text or "")


async def test_fallback_uses_the_tenants_configured_message():
    from app.handlers.builtin import FallbackHandler

    class SilentHandler(Handler):
        name: ClassVar[str] = "silent"

        async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
            await next_()

    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        await repo.update_tenant_settings(
            session, tenant, fallback_message="Un momento, por favor."
        )

    orchestrator = build_orchestrator([FallbackHandler(), SilentHandler()])
    await orchestrator.process_inbound(web_message("consulta suelta", message_id="m-1"))

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        messages = await repo.recent_messages(session, conversation.id, limit=10)

    assert messages[-1].text == "Un momento, por favor."


async def test_failing_handler_does_not_break_the_turn():
    class BrokenHandler(Handler):
        name: ClassVar[str] = "broken"

        async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
            raise RuntimeError("fallo simulado en la lógica de negocio")

    orchestrator = build_orchestrator([BrokenHandler(), EchoHandler()])

    assert await orchestrator.process_inbound(web_message("hola", message_id="m-1")) is True

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        messages = await repo.recent_messages(session, conversation.id, limit=10)

    assert messages[-1].text == "eco: hola"


# --------------------------------------------------------------------------- #
# Acuses de recibo
# --------------------------------------------------------------------------- #
async def test_delivery_status_event_updates_message_state():
    orchestrator = build_orchestrator()
    await orchestrator.process_inbound(web_message("hola", message_id="m-1"))

    async with session_scope() as session:
        outbound = (
            await session.execute(
                select(Message).where(Message.direction == Direction.OUTBOUND)
            )
        ).scalar_one()
        outbound.provider_message_id = "prov-777"
        outbound.status = DeliveryStatus.SENT

    status_event = InboundMessage(
        channel=ChannelKind.WEB,
        conversation=ConversationRef(
            channel=ChannelKind.WEB, channel_conversation_id="web-sesion-1"
        ),
        sender=Party(channel_user_id="web-sesion-1"),
        provider_message_id="status:prov-777:read",
        content_type=ContentType.SYSTEM,
        action={
            "kind": "delivery_status",
            "target_provider_message_id": "prov-777",
            "status": "read",
            "provider_status": "read",
        },
    )
    assert await orchestrator.process_inbound(status_event) is True

    async with session_scope() as session:
        outbound = (
            await session.execute(
                select(Message).where(Message.direction == Direction.OUTBOUND)
            )
        ).scalar_one()
        assert outbound.status is DeliveryStatus.READ


async def test_delivery_status_never_regresses():
    """Los acuses pueden llegar desordenados; el estado solo avanza."""
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, "default")
        conversation = await repo.resolve_conversation(
            session,
            tenant_id=tenant.id,
            ref=ConversationRef(
                channel=ChannelKind.WEB, channel_conversation_id="hilo-orden"
            ),
            contact_id=None,
        )
        message = await repo.record_outbound(
            session,
            conversation=conversation,
            outbound=__import__(
                "app.core.envelope", fromlist=["OutboundMessage"]
            ).OutboundMessage(text="salida"),
        )
        message.provider_message_id = "prov-1"

    async with session_scope() as session:
        await repo.apply_delivery_update(
            session,
            channel=ChannelKind.WEB,
            provider_message_id="prov-1",
            status=DeliveryStatus.READ,
        )
    async with session_scope() as session:
        await repo.apply_delivery_update(
            session,
            channel=ChannelKind.WEB,
            provider_message_id="prov-1",
            status=DeliveryStatus.SENT,
        )

    async with session_scope() as session:
        message = (await session.execute(select(Message))).scalar_one()
        assert message.status is DeliveryStatus.READ
        assert len(await _events(session, message.id)) == 2


async def _events(session, message_id):
    from app.db.models import MessageEvent

    return list(
        (
            await session.execute(
                select(MessageEvent).where(MessageEvent.message_id == message_id)
            )
        ).scalars()
    )


# --------------------------------------------------------------------------- #
# Respuesta desde la consola
# --------------------------------------------------------------------------- #
async def test_agent_reply_is_recorded_and_queued():
    from app.core.envelope import OutboundMessage

    orchestrator = build_orchestrator()
    await orchestrator.process_inbound(web_message("hola", message_id="m-1"))

    async with session_scope() as session:
        conversation = (await session.execute(select(Conversation))).scalar_one()
        conversation_id = conversation.id

    outbox_id = await orchestrator.send_from_agent(
        conversation_id=conversation_id,
        outbound=OutboundMessage(text="Le atiende Marta del equipo de soporte."),
    )
    assert outbox_id is not None

    async with session_scope() as session:
        messages = await repo.recent_messages(session, conversation_id, limit=10)

    assert messages[-1].author_type == "agent"
    assert messages[-1].text == "Le atiende Marta del equipo de soporte."


async def test_agent_reply_to_unknown_conversation_returns_none():
    import uuid

    from app.core.envelope import OutboundMessage

    orchestrator = build_orchestrator()
    result = await orchestrator.send_from_agent(
        conversation_id=uuid.uuid4(), outbound=OutboundMessage(text="hola")
    )
    assert result is None


# --------------------------------------------------------------------------- #
# Límite de aforo
# --------------------------------------------------------------------------- #
async def test_rate_limit_stops_answering_after_the_threshold():
    from app.handlers.builtin import RateLimitHandler

    orchestrator = build_orchestrator([RateLimitHandler(2), EchoHandler()])

    for index in range(4):
        await orchestrator.process_inbound(web_message("hola", message_id=f"m-{index}"))

    # Cuatro entrantes, pero solo las dos primeras generan respuesta.
    assert await count(Message) == 6


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"session_id": "web-abc", "text": "hola"}, 1),
        ({"text": "sin sesión"}, 0),
        ({"session_id": "   ", "text": "sesión vacía"}, 0),
    ],
)
async def test_web_adapter_requires_a_session_id(payload, expected):
    orchestrator = build_orchestrator()
    result = await orchestrator.handle_event(
        ChannelKind.WEB, payload=payload, headers={}, verify=False
    )
    assert result["parsed"] == expected
