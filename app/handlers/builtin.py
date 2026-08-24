"""Handlers básicos: control de aforo, comandos, derivación y red de seguridad."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import ClassVar

from app.core.envelope import ContentType, Direction
from app.core.hub import hub, inbox_topic
from app.core.pipeline import Handler, NextFn, TurnContext
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)


class RateLimitHandler(Handler):
    """Ventana deslizante por conversación.

    Protege al modelo y a las cuotas del proveedor frente a ráfagas, ya sean
    accidentales o malintencionadas.
    """

    name: ClassVar[str] = "rate_limit"

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._events: dict[str, deque[float]] = defaultdict(deque)

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        key = str(ctx.conversation.id)
        now = time.monotonic()
        window = self._events[key]
        while window and now - window[0] > 60:
            window.popleft()

        if len(window) >= self._max:
            log.warning("rate_limited", conversation_id=key, limit=self._max)
            ctx.suppress_reply = True
            return

        window.append(now)
        await next_()


class HumanControlHandler(Handler):
    """Silencia al bot cuando un agente humano tomó el control del hilo."""

    name: ClassVar[str] = "human_control"

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        if ctx.conversation.control == "human":
            await hub.publish(
                inbox_topic(ctx.tenant.slug),
                {
                    "type": "inbound_while_human",
                    "conversation_id": str(ctx.conversation.id),
                    "text": ctx.text,
                },
            )
            ctx.suppress_reply = True
            return
        await next_()


class CommandHandler(Handler):
    """Comandos explícitos del usuario. Resuelve el turno sin invocar al modelo."""

    name: ClassVar[str] = "commands"

    HELP_TEXT = (
        "Comandos disponibles:\n"
        "• /ayuda — muestra este mensaje\n"
        "• /agente — solicita atención de una persona\n"
        "• /bot — devuelve la conversación al asistente automático\n"
        "• /reiniciar — olvida el contexto de la conversación"
    )

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        text = ctx.text.lower()
        if not text.startswith("/"):
            await next_()
            return

        command = text.split()[0]
        if command in {"/ayuda", "/help", "/start"}:
            ctx.reply(self.HELP_TEXT)
            return

        if command in {"/agente", "/humano", "/agent"}:
            await self._request_handoff(ctx)
            return

        if command == "/bot":
            await repo.set_conversation_control(ctx.session, ctx.conversation.id, "bot")
            ctx.conversation.control = "bot"
            ctx.reply("De acuerdo, continúo yo. ¿En qué puedo ayudarle?")
            return

        if command in {"/reiniciar", "/reset"}:
            ctx.clear_state()
            ctx.scratch["skip_history"] = True
            ctx.reply("He borrado el contexto. Comencemos de nuevo.")
            return

        ctx.reply(f"No reconozco el comando «{command}».\n\n{self.HELP_TEXT}")

    async def _request_handoff(self, ctx: TurnContext) -> None:
        await repo.set_conversation_control(ctx.session, ctx.conversation.id, "human")
        ctx.conversation.control = "human"
        await repo.record_audit(
            ctx.session,
            tenant_id=ctx.tenant.id,
            actor=f"contact:{ctx.contact.id if ctx.contact else 'anónimo'}",
            action="handoff_requested",
            subject_type="conversation",
            subject_id=str(ctx.conversation.id),
        )
        await hub.publish(
            inbox_topic(ctx.tenant.slug),
            {
                "type": "handoff_requested",
                "conversation_id": str(ctx.conversation.id),
                "channel": str(ctx.conversation.channel),
            },
        )
        ctx.reply(
            "Le pongo en contacto con una persona del equipo. "
            "Permanezca en la conversación; le responderemos en breve."
        )


class UnsupportedContentHandler(Handler):
    """Responde con cortesía a contenidos que la lógica actual no interpreta."""

    name: ClassVar[str] = "unsupported_content"

    HANDLED: ClassVar[frozenset[ContentType]] = frozenset(
        {ContentType.TEXT, ContentType.INTERACTIVE}
    )

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        content_type = ctx.inbound.content_type
        if content_type in self.HANDLED or ctx.text:
            await next_()
            return

        if content_type is ContentType.REACTION:
            ctx.suppress_reply = True
            return

        ctx.reply(
            "He recibido su archivo, aunque todavía no puedo interpretar este tipo "
            "de contenido. ¿Podría describir su consulta con palabras?"
        )


class FallbackHandler(Handler):
    """Último eslabón: garantiza una respuesta, pero solo la primera vez.

    A partir del segundo mensaje sin resolver se prefiere el silencio a repetir
    la misma disculpa en cada turno: el cliente ya sabe que puede escribir
    ``/agente`` si lo necesita, y machacarlo con el mismo texto no ayuda.
    """

    name: ClassVar[str] = "fallback"

    #: Texto por omisión. La administración puede sustituirlo por inquilino
    #: desde la consola (``Tenant.settings["fallback_message"]``), sin tocar
    #: código; este valor solo se usa mientras no haya uno configurado.
    MESSAGE = (
        "Disculpe, no he logrado procesar su mensaje. "
        "Puede reformularlo o escribir /agente para hablar con una persona."
    )

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        await next_()
        if ctx.replies or ctx.suppress_reply:
            return

        previous_inbound = sum(
            1 for message in ctx.history if message.direction is Direction.INBOUND
        )
        if previous_inbound > 1:
            return

        log.info("fallback_reply", conversation_id=str(ctx.conversation.id))
        message = ctx.tenant.settings.get("fallback_message") or self.MESSAGE
        ctx.reply(message)
