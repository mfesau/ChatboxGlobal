"""Pruebas del formato canónico y de la cadena de handlers."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    DeliveryReceipt,
    DeliveryStatus,
    InboundMessage,
    OutboundMessage,
    Party,
)
from app.core.hub import Hub
from app.core.pipeline import Handler, NextFn, Pipeline


# --------------------------------------------------------------------------- #
# Formato canónico
# --------------------------------------------------------------------------- #
def test_dedupe_key_defaults_to_channel_and_provider_id():
    message = InboundMessage(
        channel=ChannelKind.WHATSAPP,
        conversation=ConversationRef(
            channel=ChannelKind.WHATSAPP, channel_conversation_id="595981000111"
        ),
        sender=Party(channel_user_id="595981000111"),
        provider_message_id="wamid.XYZ",
    )
    assert message.dedupe_key == "whatsapp:wamid.XYZ"


def test_explicit_dedupe_key_is_respected():
    message = InboundMessage(
        channel=ChannelKind.WEB,
        conversation=ConversationRef(channel=ChannelKind.WEB, channel_conversation_id="s"),
        sender=Party(channel_user_id="s"),
        provider_message_id="m-1",
        dedupe_key="clave-propia",
    )
    assert message.dedupe_key == "clave-propia"


def test_outbound_message_survives_a_serialisation_round_trip():
    """La cola de salida persiste el mensaje como JSON; nada debe perderse."""
    original = OutboundMessage(
        text="Aquí tiene la factura.",
        quick_replies=[{"id": "ok", "title": "Recibido"}],
        attachments=[
            Attachment(
                content_type=ContentType.DOCUMENT,
                url="https://ejemplo.test/f.pdf",
                mime_type="application/pdf",
                filename="f.pdf",
                size_bytes=1024,
            )
        ],
        channel_data={"template": {"name": "aviso"}},
    )

    restored = OutboundMessage.from_dict(original.to_dict())

    assert restored.text == original.text
    assert restored.quick_replies == original.quick_replies
    assert restored.channel_data == original.channel_data
    assert restored.client_message_id == original.client_message_id
    assert restored.attachments[0].filename == "f.pdf"
    assert restored.attachments[0].content_type is ContentType.DOCUMENT
    assert restored.attachments[0].size_bytes == 1024


def test_conversation_ref_round_trip_preserves_routing_data():
    original = ConversationRef(
        channel=ChannelKind.MSBOT,
        channel_conversation_id="19:hilo",
        channel_account_id="28:bot",
        service_url="https://smba.trafficmanager.net/emea/",
        reply_to_message_id="act-1",
        extra={"channel_id": "msteams"},
    )

    restored = ConversationRef.from_dict(original.to_dict())

    assert restored == original


def test_system_messages_are_not_actionable():
    message = InboundMessage(
        channel=ChannelKind.WHATSAPP,
        conversation=ConversationRef(
            channel=ChannelKind.WHATSAPP, channel_conversation_id="1"
        ),
        sender=Party(channel_user_id="1"),
        provider_message_id="s-1",
        content_type=ContentType.SYSTEM,
    )
    assert message.is_actionable is False


def test_receipt_helpers_set_the_expected_state():
    sent = DeliveryReceipt.sent("prov-1")
    assert sent.ok is True
    assert sent.status is DeliveryStatus.SENT

    failed = DeliveryReceipt.failed("http_429", "cuota agotada", retryable=True)
    assert failed.ok is False
    assert failed.retryable is True


def test_outbound_messages_get_distinct_identifiers():
    first = OutboundMessage(text="a")
    second = OutboundMessage(text="b")
    assert first.client_message_id != second.client_message_id
    assert isinstance(first.client_message_id, uuid.UUID)


# --------------------------------------------------------------------------- #
# Cadena de handlers
# --------------------------------------------------------------------------- #
def stub_context() -> SimpleNamespace:
    """Contexto mínimo: la cadena solo necesita identificar la conversación."""
    return SimpleNamespace(conversation=SimpleNamespace(id="conversacion-de-prueba"))


class Recorder(Handler):
    """Handler que registra el orden de entrada y de salida."""

    def __init__(self, label: str, trace: list[str], *, delegate: bool = True) -> None:
        self.name = label
        self._trace = trace
        self._delegate = delegate

    async def handle(self, ctx, next_: NextFn) -> None:
        self._trace.append(f"entra:{self.name}")
        if self._delegate:
            await next_()
        self._trace.append(f"sale:{self.name}")


async def test_pipeline_runs_handlers_as_nested_middlewares():
    trace: list[str] = []
    pipeline = Pipeline([Recorder("a", trace), Recorder("b", trace)])

    await pipeline.run(stub_context())  # type: ignore[arg-type]

    assert trace == ["entra:a", "entra:b", "sale:b", "sale:a"]


async def test_pipeline_short_circuits_when_a_handler_does_not_delegate():
    trace: list[str] = []
    pipeline = Pipeline(
        [Recorder("a", trace), Recorder("b", trace, delegate=False), Recorder("c", trace)]
    )

    await pipeline.run(stub_context())  # type: ignore[arg-type]

    assert "entra:c" not in trace


async def test_pipeline_continues_after_a_handler_raises():
    trace: list[str] = []

    class Broken(Handler):
        name: ClassVar[str] = "roto"

        async def handle(self, ctx, next_: NextFn) -> None:
            raise RuntimeError("fallo")

    pipeline = Pipeline([Broken(), Recorder("siguiente", trace)])
    await pipeline.run(stub_context())  # type: ignore[arg-type]

    assert trace == ["entra:siguiente", "sale:siguiente"]


async def test_pipeline_does_not_replay_the_tail_when_a_handler_fails_after_delegating():
    """Si el handler delega y luego falla, la cola no debe ejecutarse dos veces."""
    trace: list[str] = []

    class DelegatesThenFails(Handler):
        name: ClassVar[str] = "delega_y_falla"

        async def handle(self, ctx, next_: NextFn) -> None:
            await next_()
            raise RuntimeError("fallo posterior")

    pipeline = Pipeline([DelegatesThenFails(), Recorder("cola", trace)])
    await pipeline.run(stub_context())  # type: ignore[arg-type]

    assert trace.count("entra:cola") == 1


def test_appending_a_handler_extends_the_pipeline():
    trace: list[str] = []
    pipeline = Pipeline([Recorder("a", trace)])
    pipeline.append(Recorder("b", trace))
    assert [handler.name for handler in pipeline.handlers] == ["a", "b"]


# --------------------------------------------------------------------------- #
# Bus de difusión
# --------------------------------------------------------------------------- #
async def test_hub_delivers_to_every_subscriber_of_a_topic():
    hub = Hub()
    received: list[dict] = []

    async def first(event):
        received.append(event)

    async def second(event):
        received.append(event)

    await hub.subscribe("tema", first)
    await hub.subscribe("tema", second)

    delivered = await hub.publish("tema", {"n": 1})

    assert delivered == 2
    assert len(received) == 2


async def test_hub_drops_a_subscriber_that_fails():
    hub = Hub()

    async def broken(event):
        raise ConnectionError("socket cerrado")

    await hub.subscribe("tema", broken)
    assert await hub.publish("tema", {}) == 0
    assert hub.subscriber_count("tema") == 0


async def test_hub_ignores_topics_without_subscribers():
    hub = Hub()
    assert await hub.publish("nadie-escucha", {}) == 0


@pytest.mark.parametrize("kind", list(ChannelKind))
def test_every_channel_kind_serialises_as_its_value(kind: ChannelKind):
    assert str(kind) == kind.value
