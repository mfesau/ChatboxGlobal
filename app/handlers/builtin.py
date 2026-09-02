"""Handlers básicos: control de aforo, comandos, derivación y red de seguridad."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import ClassVar

from app.core.business_hours import (
    ServicePolicy,
    is_within_business_hours,
    resolve_service_policy,
)
from app.core.envelope import ContentType, Direction
from app.core.hub import hub, inbox_topic
from app.core.localized import pick as pick_localized
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


def _contact_locale(ctx: TurnContext) -> str | None:
    """Idioma en el que escribe el cliente, si lo declaró alguna vez."""
    return getattr(ctx.contact, "locale", None)


async def _service_policy(ctx: TurnContext) -> ServicePolicy:
    """Horario y objetivo que rigen este turno.

    Lo del departamento manda; lo que no fije, lo pone el inquilino. Así la
    cola común —que todavía no tiene departamento— también queda cubierta.
    """
    department = None
    if ctx.conversation.department_id is not None:
        department = await repo.get_department(ctx.session, ctx.conversation.department_id)
    return resolve_service_policy(department, ctx.tenant.settings)


class BusinessHoursHandler(Handler):
    """Fuera del horario que rige el hilo, avisa una vez y no contesta más.

    Va después de ``HumanControlHandler``: si alguien del equipo ya está
    atendiendo el hilo, manda esa decisión y aquí no se hace nada. Y antes del
    modelo, para no gastar una llamada en un turno que no va a responderse.
    """

    name: ClassVar[str] = "business_hours"

    #: Marca en el estado del hilo para no repetir el aviso en cada mensaje.
    STATE_KEY: ClassVar[str] = "out_of_hours_notified"

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        policy = await _service_policy(ctx)
        if not policy.has_schedule:
            await next_()
            return

        if is_within_business_hours(
            policy.business_hours, timezone=policy.timezone
        ):
            # De vuelta en horario: se limpia la marca para que el próximo
            # cierre vuelva a avisar.
            if ctx.state.get(self.STATE_KEY):
                ctx.set_state(self.STATE_KEY, False)
            await next_()
            return

        if ctx.state.get(self.STATE_KEY):
            # Ya se le avisó en este cierre: se recibe el mensaje en silencio.
            ctx.suppress_reply = True
            return

        log.info("fuera_de_horario", conversation=str(ctx.conversation.id))
        ctx.set_state(self.STATE_KEY, True)
        notice = pick_localized(policy.out_of_hours_message, _contact_locale(ctx))
        if notice:
            ctx.reply(notice)
        else:
            # Sin texto configurado no se inventa ninguno: el mensaje queda
            # guardado y en la cola, que es lo que importa.
            ctx.suppress_reply = True


class FirstResponseSlaHandler(Handler):
    """Arranca el reloj de la primera respuesta cuando el cliente queda esperando.

    Va después del horario de atención: el vencimiento se cuenta en minutos
    hábiles, así que fuera de hora el plazo empieza a correr recién en la
    próxima apertura y no se consume de madrugada. Alcanza también a la cola
    común cuando el inquilino fija un objetivo por omisión.
    """

    name: ClassVar[str] = "first_response_sla"

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        await next_()

        conversation = ctx.conversation
        if conversation.first_response_at or conversation.first_response_due_at:
            return

        due = await repo.set_first_response_deadline(
            ctx.session, conversation=conversation, policy=await _service_policy(ctx)
        )
        if due is not None:
            log.info(
                "sla_primera_respuesta_iniciado",
                conversation=str(conversation.id),
                vence=due.isoformat(),
            )


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
    #: código; este valor solo se usa mientras no haya uno configurado. Lo
    #: configurado admite un texto por idioma, y se elige por el del contacto.
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
        configured = pick_localized(
            ctx.tenant.settings.get("fallback_message"), _contact_locale(ctx)
        )
        ctx.reply(configured or self.MESSAGE)
