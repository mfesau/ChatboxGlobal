"""Handler de IA: genera la respuesta con Gemini a partir del historial.

Emplea el SDK oficial de Google (``google-genai``) en modo asíncrono y con
transmisión por fragmentos, de modo que el chatbox web puede mostrar el texto
mientras se genera y las peticiones largas no agotan el tiempo de espera HTTP.

La conversación se arma como una lista de *pasos* (``input``): lo que escribió
el usuario, lo que respondió el modelo, y —cuando pide una herramienta— la
llamada y su resultado. Las herramientas se declaran en un formato propio del
proveedor, pero sus definiciones viven fuera de aquí
(``app/core/hotel_booking.py``) en forma neutra: se traducen al llamar, para
que cambiar de proveedor no obligue a reescribir cada módulo de negocio.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, ClassVar

from google import genai
from google.genai import errors as genai_errors

from app.core import hotel_booking, knowledge
from app.core.envelope import Direction
from app.core.hub import conversation_topic, hub, inbox_topic
from app.core.pipeline import Handler, NextFn, TurnContext
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)

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

BUSCAR_CONOCIMIENTO_TOOL: dict[str, Any] = {
    "name": "buscar_en_base_de_conocimiento",
    "description": (
        "Busca en la documentación interna de la empresa —normas, tarifas, "
        "preguntas frecuentes, procedimientos— los pasajes que responden a una "
        "pregunta. Úsala SIEMPRE que te pregunten algo propio de esta empresa "
        "en vez de contestar de memoria, y responde solo con lo que devuelva. "
        "Si no devuelve nada útil, dilo y ofrece derivar a un agente."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pregunta": {
                "type": "string",
                "description": (
                    "Lo que hay que averiguar, redactado como una pregunta "
                    "completa. La búsqueda es por significado, así que conviene "
                    "una frase entera antes que dos palabras sueltas."
                ),
            }
        },
        "required": ["pregunta"],
        "additionalProperties": False,
    },
    "strict": True,
}

@dataclass(slots=True)
class ToolCall:
    """Llamada a una herramienta, ya rearmada a partir de los eventos del flujo."""

    id: str | None
    name: str
    arguments: dict[str, Any]


def _parse_arguments(raw: str, *, name: str | None = None) -> dict[str, Any]:
    """Convierte los argumentos, que llegan como trozos sueltos de JSON.

    Un JSON que no cierra significa que el flujo se cortó a mitad. Se devuelve
    vacío en lugar de reventar: la herramienta se quejará de que le falta un
    dato, que es un error que el modelo sabe contarle a quien escribe, y no una
    excepción que deje el turno sin respuesta.
    """
    if not raw:
        return {}
    try:
        cargado = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("tool_arguments_unreadable", tool=name, raw=raw[:200])
        return {}
    return cargado if isinstance(cargado, dict) else {}


#: Cota superior de vueltas del bucle de herramientas; evita bucles infinitos.
MAX_TOOL_ITERATIONS = 3


class AIHandler(Handler):
    """Compone el contexto, invoca al modelo y traduce el resultado a respuestas."""

    name: ClassVar[str] = "ai"

    def __init__(self, settings: Any, client: Any | None = None) -> None:
        self.settings = settings
        self._client = client
        if self._client is None and settings.google_api_key is not None:
            self._client = genai.Client(
                api_key=settings.google_api_key.get_secret_value()
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        if not self.enabled:
            log.debug("ai_handler_disabled", reason="sin GOOGLE_API_KEY")
            await next_()
            return
        if not ctx.text and not ctx.inbound.action:
            await next_()
            return

        started = time.monotonic()
        messages = self._build_messages(ctx)
        system = self._build_system_prompt(ctx)
        tools = [HANDOFF_TOOL, *await self._available_tools(ctx)]

        try:
            text, usage, stop_reason = await self._converse(ctx, system, messages, tools)
        except genai_errors.ClientError as exc:
            await self._record_error(ctx, str(exc), started)
            # 429 es cuota agotada o demasiadas peticiones: se le dice al
            # usuario que reintente. El resto son errores nuestros (petición
            # mal armada, clave inválida) y no se le explican a quien escribe.
            if getattr(exc, "code", None) == 429:
                ctx.reply(
                    "Estamos atendiendo muchas consultas en este momento. "
                    "Inténtelo de nuevo en unos segundos, por favor."
                )
                return
            await next_()
            return
        except (genai_errors.ServerError, genai_errors.APIError) as exc:
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
            cache_read_tokens=usage.get("cached_content_token_count"),
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
    def _build_system_prompt(self, ctx: TurnContext) -> str:
        """Instrucción del sistema: lo estable primero, el contexto después."""
        contact_name = (ctx.contact.display_name if ctx.contact else None) or "el usuario"
        return (
            f"{self.settings.ai_system_prompt}\n\n"
            f"Canal de la conversación: {ctx.conversation.channel}. "
            f"Nombre del interlocutor: {contact_name}. "
            "Si el canal es whatsapp, evita el formato Markdown enriquecido y "
            "mantén las respuestas por debajo de 1.000 caracteres."
        )

    def _build_messages(self, ctx: TurnContext) -> list[dict[str, Any]]:
        """Convierte el historial persistido en los pasos que espera la API.

        Cada turno es un paso: ``user_input`` lo que escribió la persona,
        ``model_output`` lo que respondió el asistente. Los turnos seguidos del
        mismo lado se funden en uno, y se garantiza que el primero y el último
        sean del usuario: es a lo último que dijo a lo que hay que contestar.
        """
        if ctx.scratch.get("skip_history"):
            history = [ctx.stored_message]
        else:
            history = ctx.history[-self.settings.ai_history_turns :]

        def paso(tipo: str, texto: str) -> dict[str, Any]:
            return {"type": tipo, "content": [{"type": "text", "text": texto}]}

        def texto_de(step: dict[str, Any]) -> str:
            return step["content"][0]["text"]

        messages: list[dict[str, Any]] = []
        for row in history:
            content = (row.text or "").strip()
            if not content:
                content = self._describe_non_text(row)
            if not content:
                continue
            tipo = "user_input" if row.direction is Direction.INBOUND else "model_output"
            if messages and messages[-1]["type"] == tipo:
                messages[-1]["content"][0]["text"] = f"{texto_de(messages[-1])}\n{content}"
            else:
                messages.append(paso(tipo, content))

        if not messages or messages[0]["type"] != "user_input":
            messages.insert(0, paso("user_input", ctx.text or "(sin texto)"))
        if messages[-1]["type"] == "model_output":
            messages.append(paso("user_input", ctx.text or "(sin texto)"))
        return messages

    async def _available_tools(self, ctx: TurnContext) -> list[dict[str, Any]]:
        """Herramientas adicionales, según qué módulos tenga activos el departamento.

        Sin departamento asignado la conversación todavía está en la cola
        común: no hay una rama de negocio concreta a la que ofrecer un módulo,
        así que no se agrega ninguna herramienta extra.
        """
        tools: list[dict[str, Any]] = []

        # La base de conocimiento no depende del departamento: los documentos
        # de toda la empresa valen en cualquier rama. Se ofrece solo si hay
        # algo dentro, para no invitar al modelo a buscar en el vacío.
        if await repo.tenant_has_knowledge(ctx.session, tenant_id=ctx.tenant.id):
            tools.append(BUSCAR_CONOCIMIENTO_TOOL)

        department_id = ctx.conversation.department_id
        if department_id is None:
            return tools
        department = await repo.get_department(ctx.session, department_id)
        if department is None or not repo.hotel_module_enabled(department):
            return tools
        return [*tools, *hotel_booking.HOTEL_TOOLS]

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
        tools: list[dict[str, Any]],
    ) -> tuple[str, dict[str, int], str | None]:
        """Bucle de herramientas. Devuelve texto, uso de tokens y motivo de parada."""
        assert self._client is not None
        usage_total: dict[str, int] = {}
        collected: list[str] = []
        stop_reason: str | None = None
        stream_to_client = ctx.conversation.channel == "web"

        for _ in range(MAX_TOOL_ITERATIONS):
            stream = await self._client.aio.interactions.create(
                model=self.settings.ai_model,
                input=messages,
                system_instruction=system,
                tools=[_as_function_tool(tool) for tool in tools],
                generation_config={"max_output_tokens": self.settings.ai_max_tokens},
                stream=True,
            )

            text, calls, status, usage = await self._read_stream(ctx, stream, stream_to_client)
            stop_reason = status or stop_reason
            _accumulate_usage(usage_total, usage)
            if text.strip():
                collected.append(text)
            if not calls:
                break

            # Lo ya dicho y lo pedido se reinyectan tal cual: la vuelta
            # siguiente tiene que ver su propia llamada antes que el resultado,
            # o el modelo no sabe a qué contesta.
            messages.extend(
                {"type": "function_call", "id": call.id, "name": call.name,
                 "arguments": call.arguments}
                for call in calls
            )
            messages.extend(await self._run_tools(ctx, calls))

        return "\n\n".join(part for part in collected if part.strip()), usage_total, stop_reason

    async def _read_stream(
        self, ctx: TurnContext, stream: Any, stream_to_client: bool
    ) -> tuple[str, list[ToolCall], str | None, Any]:
        """Rearma la respuesta a partir de los eventos del flujo.

        En modo transmitido el objeto ``interaction`` solo trae metadatos —
        identificador, estado y consumo—: **el texto no está ahí**. Llega
        troceado en los deltas de un paso ``model_output``, y una llamada a
        herramienta se reparte entre el ``step.start``, que trae su nombre e
        identificador, y unos deltas de argumentos que son trozos sueltos de
        JSON. Las dos cosas hay que rearmarlas por el camino.

        Los pasos de tipo ``thought`` también emiten deltas, pero de firma y no
        de texto; se ignoran, o el razonamiento interno acabaría en el chat.
        """
        partes: list[str] = []
        #: Índice del paso -> llamada a medio construir.
        pendientes: dict[int, dict[str, Any]] = {}
        status: str | None = None
        usage: Any = None

        async for event in stream:
            tipo = getattr(event, "event_type", None)

            if tipo == "step.start":
                paso = getattr(event, "step", None)
                if getattr(paso, "type", None) == "function_call":
                    pendientes[event.index] = {
                        "id": getattr(paso, "id", None),
                        "name": getattr(paso, "name", None),
                        "arguments": "",
                    }
                continue

            if tipo == "step.delta":
                delta = getattr(event, "delta", None)
                clase = getattr(delta, "type", None)
                if clase == "text" and getattr(delta, "text", None):
                    partes.append(delta.text)
                    # Solo el chatbox web puede pintar el texto conforme llega;
                    # los demás canales reciben el mensaje entero al final.
                    if stream_to_client:
                        await hub.publish(
                            conversation_topic(ctx.conversation.channel_conversation_id),
                            {"type": "delta", "text": delta.text},
                        )
                elif clase == "arguments_delta" and event.index in pendientes:
                    pendientes[event.index]["arguments"] += (
                        getattr(delta, "arguments", "") or ""
                    )
                continue

            interaction = getattr(event, "interaction", None)
            if interaction is not None:
                status = getattr(interaction, "status", None) or status
                usage = getattr(interaction, "usage", None) or usage

        calls = [
            ToolCall(
                id=datos["id"],
                name=datos["name"],
                arguments=_parse_arguments(datos["arguments"], name=datos["name"]),
            )
            for datos in pendientes.values()
            if datos["name"]
        ]
        return "".join(partes), calls, status, usage

    async def _run_tools(self, ctx: TurnContext, calls: list[Any]) -> list[dict[str, Any]]:
        """Ejecuta las herramientas pedidas y devuelve un paso por resultado.

        Un fallo no corta la conversación: se devuelve como resultado de error
        para que el modelo lo cuente, en vez de dejar el turno sin respuesta.
        """
        results: list[dict[str, Any]] = []
        for call in calls:
            try:
                output = await self._dispatch_tool(ctx, call.name, dict(call.arguments or {}))
                results.append(
                    {
                        "type": "function_result",
                        "call_id": call.id,
                        "name": call.name,
                        "result": output,
                    }
                )
            except Exception as exc:  # la herramienta falla, la conversación sigue
                log.exception("tool_failed", tool=call.name)
                results.append(
                    {
                        "type": "function_result",
                        "call_id": call.id,
                        "name": call.name,
                        "result": f"Error al ejecutar la herramienta: {exc}",
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
                inbox_topic(ctx.tenant.slug),
                {
                    "type": "handoff_requested",
                    "conversation_id": str(ctx.conversation.id),
                    "reason": arguments.get("motivo"),
                    "urgency": arguments.get("urgencia"),
                },
            )
            return "Derivación registrada. Un agente humano atenderá la conversación."

        if name == "buscar_en_base_de_conocimiento":
            return await self._search_knowledge(ctx, arguments)

        hotel_result = await hotel_booking.dispatch(ctx, name, arguments)
        if hotel_result is not None:
            return hotel_result
        raise ValueError(f"Herramienta desconocida: {name}")

    async def _search_knowledge(self, ctx: TurnContext, arguments: dict[str, Any]) -> str:
        """Devuelve los pasajes que responden a la pregunta, ya con su origen.

        Cada pasaje va encabezado por el título de su documento para que el
        modelo pueda citarlo: «según las condiciones de reserva…» ayuda mucho
        más a quien pregunta que una afirmación sin respaldo.
        """
        pregunta = (arguments.get("pregunta") or "").strip()
        if not pregunta:
            return "No se indicó qué buscar."

        try:
            pasajes = await knowledge.search(
                ctx.session,
                settings=self.settings,
                tenant_id=ctx.tenant.id,
                department_id=ctx.conversation.department_id,
                query=pregunta,
            )
        except knowledge.KnowledgeError as exc:
            log.warning("knowledge_search_unavailable", error=str(exc))
            return "La base de conocimiento no está disponible en este momento."

        if not pasajes:
            return (
                "La documentación interna no dice nada sobre eso. No inventes una "
                "respuesta: reconócelo y ofrece derivar la conversación a un agente."
            )

        # Los identificadores se dejan anotados en el turno para que queden en
        # `ai_runs` junto con el consumo: así se puede reconstruir en qué se
        # apoyó una respuesta concreta.
        ctx.scratch.setdefault("knowledge_chunks", []).extend(
            str(pasaje.chunk_id) for pasaje in pasajes
        )
        return "\n\n---\n\n".join(
            f"[{pasaje.title}]\n{pasaje.content}" for pasaje in pasajes
        )

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


def _as_function_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Traduce una herramienta neutra al formato de función del proveedor.

    Las definiciones se escriben una sola vez, junto al módulo de negocio que
    las implementa, y se adaptan aquí. ``strict`` y ``additionalProperties`` se
    descartan: son de otro proveedor y el esquema los rechaza.
    """
    schema = {
        clave: valor
        for clave, valor in (tool.get("input_schema") or {}).items()
        if clave != "additionalProperties"
    }
    return {
        "type": "function",
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": schema,
    }


#: Cómo se llama cada cifra de consumo en el proveedor y con qué nombre se
#: guarda. La traducción hace falta: el proveedor las publica con el prefijo
#: ``total_`` y ``record_ai_run`` espera los nombres de la derecha.
USAGE_FIELDS = {
    "total_input_tokens": "input_tokens",
    "total_output_tokens": "output_tokens",
    "total_cached_tokens": "cached_content_token_count",
    "total_thought_tokens": "thoughts_token_count",
    "total_tokens": "total_tokens",
}


def _accumulate_usage(total: dict[str, int], usage: Any) -> None:
    """Suma el gasto de cada vuelta del bucle de herramientas."""
    if usage is None:
        return
    for origen, destino in USAGE_FIELDS.items():
        value = getattr(usage, origen, None)
        if isinstance(value, int):
            total[destino] = total.get(destino, 0) + value
