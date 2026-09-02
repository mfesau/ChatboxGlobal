"""Handler de IA: genera la respuesta con Claude a partir del historial.

Emplea el SDK oficial de Anthropic en modo asíncrono y con transmisión por
fragmentos, de modo que el chatbox web puede mostrar el texto mientras se genera
y las peticiones largas no agotan el tiempo de espera HTTP.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    RateLimitError,
)

from app.core.envelope import Direction
from app.core.hub import conversation_topic, hub
from app.core.pipeline import Handler, NextFn, TurnContext
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Reintento en servidor cuando un clasificador de seguridad declina la petición.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

HANDOFF_TOOL: dict[str, Any] = {
    "name": "derivar_a_agente",
    "description": (
        "Transfiere la conversación a una persona del equipo de atención. "
        "Úsalo cuando el usuario lo solicite de forma explícita, cuando exprese "
        "una queja formal, o cuando la consulta exija datos de cuenta a los que "
        "no tienes acceso."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "motivo": {
                "type": "string",
                "description": "Resumen en una frase del motivo de la derivación.",
            },
            "urgencia": {
                "type": "string",
                "enum": ["baja", "media", "alta"],
                "description": "Prioridad estimada de la atención.",
            },
        },
        "required": ["motivo", "urgencia"],
        "additionalProperties": False,
    },
    "strict": True,
}

#: Cota superior de vueltas del bucle de herramientas; evita bucles infinitos.
MAX_TOOL_ITERATIONS = 3


class AIHandler(Handler):
    """Compone el contexto, invoca al modelo y traduce el resultado a respuestas."""

    name: ClassVar[str] = "ai"

    def __init__(self, settings: Any, client: AsyncAnthropic | None = None) -> None:
        self.settings = settings
        self._client = client
        if self._client is None and settings.anthropic_api_key is not None:
            self._client = AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value()
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        if not self.enabled:
            log.debug("ai_handler_disabled", reason="sin ANTHROPIC_API_KEY")
            await next_()
            return
        if not ctx.text and not ctx.inbound.action:
            await next_()
            return

        started = time.monotonic()
        messages = self._build_messages(ctx)
        system = self._build_system_prompt(ctx)

        try:
            text, usage, stop_reason = await self._converse(ctx, system, messages)
        except RateLimitError as exc:
            await self._record_error(ctx, str(exc), started)
            ctx.reply(
                "Estamos atendiendo muchas consultas en este momento. "
                "Inténtelo de nuevo en unos segundos, por favor."
            )
            return
        except (APIStatusError, APIConnectionError) as exc:
            await self._record_error(ctx, str(exc), started)
            await next_()
            return

        await repo.record_ai_run(
            ctx.session,
            tenant_id=ctx.tenant.id,
            conversation_id=ctx.conversation.id,
            message_id=ctx.stored_message.id,
            model=self.settings.ai_model,
            handler=self.name,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cache_read_tokens=usage.get("cache_read_input_tokens"),
            latency_ms=int((time.monotonic() - started) * 1_000),
            stop_reason=stop_reason,
        )

        if stop_reason == "refusal":
            ctx.reply(
                "No puedo ayudarle con esa solicitud. "
                "Escriba /agente si desea hablar con una persona del equipo."
            )
            return

        if text.strip():
            ctx.reply(text.strip())
            return
        await next_()

    # ------------------------------------------------------------- contexto
    def _build_system_prompt(self, ctx: TurnContext) -> list[dict[str, Any]]:
        """Prefijo estable primero, contexto volátil después.

        El orden importa para la caché de prompts: el bloque marcado con
        ``cache_control`` debe contener únicamente texto invariable.
        """
        contact_name = (ctx.contact.display_name if ctx.contact else None) or "el usuario"
        return [
            {
                "type": "text",
                "text": self.settings.ai_system_prompt,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": (
                    f"Canal de la conversación: {ctx.conversation.channel}. "
                    f"Nombre del interlocutor: {contact_name}. "
                    "Si el canal es whatsapp, evita el formato Markdown enriquecido y "
                    "mantén las respuestas por debajo de 1.000 caracteres."
                ),
            },
        ]

    def _build_messages(self, ctx: TurnContext) -> list[dict[str, Any]]:
        """Convierte el historial persistido en el formato de la API."""
        if ctx.scratch.get("skip_history"):
            history = [ctx.stored_message]
        else:
            history = ctx.history[-self.settings.ai_history_turns :]

        messages: list[dict[str, Any]] = []
        for row in history:
            content = (row.text or "").strip()
            if not content:
                content = self._describe_non_text(row)
            if not content:
                continue
            role = "user" if row.direction is Direction.INBOUND else "assistant"
            # La API rechaza dos turnos consecutivos del mismo rol.
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += f"\n{content}"
            else:
                messages.append({"role": role, "content": content})

        if not messages or messages[0]["role"] != "user":
            messages.insert(0, {"role": "user", "content": ctx.text or "(sin texto)"})
        if messages[-1]["role"] == "assistant":
            messages.append({"role": "user", "content": ctx.text or "(sin texto)"})
        return messages

    @staticmethod
    def _describe_non_text(row: Any) -> str:
        """Describe en palabras un mensaje sin texto, para no perder el turno."""
        attachments = row.attachments or []
        if attachments:
            kinds = ", ".join(
                str(item.get("content_type", "archivo")) for item in attachments
            )
            return f"[el usuario adjuntó: {kinds}]"
        if row.action:
            return f"[interacción: {row.action}]"
        return ""

    # ------------------------------------------------------------- inferencia
    async def _converse(
        self,
        ctx: TurnContext,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> tuple[str, dict[str, int], str | None]:
        """Bucle de herramientas. Devuelve texto, uso de tokens y motivo de parada."""
        assert self._client is not None
        usage_total: dict[str, int] = {}
        collected: list[str] = []
        stop_reason: str | None = None
        stream_to_client = ctx.conversation.channel == "web"

        for _ in range(MAX_TOOL_ITERATIONS):
            async with self._client.beta.messages.stream(
                model=self.settings.ai_model,
                max_tokens=self.settings.ai_max_tokens,
                system=system,
                messages=messages,
                tools=[HANDOFF_TOOL],
                thinking={"type": "adaptive"},
                output_config={"effort": self.settings.ai_effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                if stream_to_client:
                    async for delta in stream.text_stream:
                        await hub.publish(
                            conversation_topic(ctx.conversation.channel_conversation_id),
                            {"type": "delta", "text": delta},
                        )
                response = await stream.get_final_message()

            stop_reason = response.stop_reason
            _accumulate_usage(usage_total, response.usage)
            collected.extend(
                block.text for block in response.content if block.type == "text"
            )

            if stop_reason != "tool_use":
                break

            tool_results = await self._run_tools(ctx, response.content)
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        return "\n\n".join(part for part in collected if part.strip()), usage_total, stop_reason

    async def _run_tools(
        self, ctx: TurnContext, content: list[Any]
    ) -> list[dict[str, Any]]:
        """Ejecuta las herramientas solicitadas y devuelve todos los resultados.

        Los ``tool_result`` han de viajar juntos en un único mensaje de usuario.
        """
        results: list[dict[str, Any]] = []
        for block in content:
            if block.type != "tool_use":
                continue
            try:
                output = await self._dispatch_tool(ctx, block.name, dict(block.input))
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output}
                )
            except Exception as exc:  # la herramienta falla, la conversación sigue
                log.exception("tool_failed", tool=block.name)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Error al ejecutar la herramienta: {exc}",
                        "is_error": True,
                    }
                )
        return results

    async def _dispatch_tool(
        self, ctx: TurnContext, name: str, arguments: dict[str, Any]
    ) -> str:
        if name == "derivar_a_agente":
            await repo.set_conversation_control(ctx.session, ctx.conversation.id, "human")
            ctx.conversation.control = "human"
            ctx.set_state("handoff", arguments)
            await repo.record_audit(
                ctx.session,
                tenant_id=ctx.tenant.id,
                actor="ai",
                action="handoff_requested",
                subject_type="conversation",
                subject_id=str(ctx.conversation.id),
                detail=arguments,
            )
            await hub.publish(
                f"inbox:{ctx.tenant.slug}",
                {
                    "type": "handoff_requested",
                    "conversation_id": str(ctx.conversation.id),
                    "reason": arguments.get("motivo"),
                    "urgency": arguments.get("urgencia"),
                },
            )
            return "Derivación registrada. Un agente humano atenderá la conversación."
        raise ValueError(f"Herramienta desconocida: {name}")

    async def _record_error(self, ctx: TurnContext, error: str, started: float) -> None:
        log.warning("ai_call_failed", error=error[:300])
        await repo.record_ai_run(
            ctx.session,
            tenant_id=ctx.tenant.id,
            conversation_id=ctx.conversation.id,
            message_id=ctx.stored_message.id,
            model=self.settings.ai_model,
            handler=self.name,
            latency_ms=int((time.monotonic() - started) * 1_000),
            error=error[:2000],
        )


def _accumulate_usage(total: dict[str, int], usage: Any) -> None:
    for field in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
        value = getattr(usage, field, None)
        if isinstance(value, int):
            total[field] = total.get(field, 0) + value
