"""Handler de IA: genera la respuesta a partir del historial.

Dos proveedores posibles, elegidos por configuración (``Settings.ai_backend``):
Gemini directo, con el SDK oficial de Google (``google-genai``), u OpenRouter,
con su API de chat compatible con la de OpenAI (``app/handlers/openrouter.py``).
Los dos en modo asíncrono y con transmisión por fragmentos, de modo que el
chatbox web puede mostrar el texto mientras se genera y las peticiones largas
no agotan el tiempo de espera HTTP.

Con OpenRouter, el modelo y el esfuerzo de razonamiento se eligen desde la
consola de administración (``app/core/ai_settings.py``); la clave, en cambio,
solo vive en el ``.env``.

La conversación se arma como una lista de *pasos* (``input``): lo que escribió
el usuario, lo que respondió el modelo, y —cuando pide una herramienta— la
llamada y su resultado. Las herramientas se declaran en un formato propio del
proveedor, pero sus definiciones viven fuera de aquí
(``app/core/hotel_booking.py``) en forma neutra: se traducen al llamar, para
que cambiar de proveedor no obligue a reescribir cada módulo de negocio.
"""

from __future__ import annotations

import base64
import json
import time
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, ClassVar

from google import genai
from google.genai import errors as genai_errors

from app.core import ai_settings, hotel_booking, knowledge
from app.core.business_hours import resolve_service_policy, resolve_timezone
from app.core.envelope import Direction, utcnow
from app.core.hub import access_scope, conversation_topic, hub, inbox_topic
from app.core.pipeline import Handler, NextFn, TurnContext
from app.core.storage import resolve_upload
from app.db import repositories as repo
from app.handlers import openrouter
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


class StreamError(RuntimeError):
    """El proveedor cortó el flujo con un error en lugar de una respuesta.

    No llega como excepción sino como un evento más del flujo, así que sin
    mirarlo el turno acababa con texto vacío y el mismo aspecto que «el modelo
    no tuvo nada que decir»: el usuario recibía la disculpa genérica y no
    quedaba rastro ni en el log ni en ``ai_runs``.
    """

    def __init__(self, code: str | None, message: str) -> None:
        super().__init__(message)
        self.code = code or "stream_error"


#: Qué clase de contenido de la API corresponde a cada tipo MIME guardado.
#: Es una lista cerrada y no un `startswith`: el modelo rechaza la petición
#: entera si le llega un formato que no admite, y perder el turno por un audio
#: exótico sería peor que describirlo con palabras.
MEDIA_KINDS: dict[str, str] = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "image/gif": "image",
    "application/pdf": "document",
    "audio/mpeg": "audio",
    "audio/mp4": "audio",
    "audio/ogg": "audio",
    "audio/wav": "audio",
    "video/mp4": "video",
    "video/webm": "video",
    "video/quicktime": "video",
}

#: Cuántos mensajes recientes pueden aportar sus ficheros. Quien manda una foto
#: y pregunta por ella en el mensaje siguiente tiene que seguir siendo
#: entendido; el historial entero, en cambio, multiplicaría el coste de cada
#: turno por todas las fotos que alguien haya mandado nunca.
MEDIA_HISTORY_DEPTH = 4

#: Tope por fichero y del conjunto. El modelo cobra por lo que se le manda, y
#: una petición demasiado grande la rechaza de todos modos.
MEDIA_MAX_BYTES = 5 * 1024 * 1024
MEDIA_TOTAL_BYTES = 10 * 1024 * 1024

#: Cota superior de vueltas del bucle de herramientas; evita bucles infinitos.
MAX_TOOL_ITERATIONS = 3


class AIHandler(Handler):
    """Compone el contexto, invoca al modelo y traduce el resultado a respuestas."""

    name: ClassVar[str] = "ai"

    def __init__(
        self,
        settings: Any,
        client: Any | None = None,
        *,
        transport: Any | None = None,
    ) -> None:
        self.settings = settings
        # Un cliente inyectado sin clave configurada es el de Gemini de las
        # pruebas antiguas; con clave, manda la configuración.
        self.provider = settings.ai_backend or ("gemini" if client is not None else None)
        self._client = client
        if self._client is None and self.provider == "openrouter":
            self._client = openrouter.OpenRouterClient(
                api_key=settings.openrouter_api_key.get_secret_value(),
                base_url=settings.openrouter_base_url,
                app_url=settings.public_base_url,
                app_title=settings.app_name,
                transport=transport,
            )
        elif self._client is None and self.provider == "gemini":
            self._client = genai.Client(
                api_key=settings.google_api_key.get_secret_value()
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def model_for(self, tenant: Any) -> str:
        """Modelo que responde, tal como se anota en ``ai_runs``.

        Con OpenRouter lo elige la administración desde la consola; con Gemini
        directo sigue mandando ``AI_MODEL``, porque los identificadores de
        uno y otro no son intercambiables.
        """
        if self.provider == "openrouter":
            return ai_settings.read(tenant, self.settings).model
        return self.settings.ai_model

    async def handle(self, ctx: TurnContext, next_: NextFn) -> None:
        if not self.enabled:
            log.debug("ai_handler_disabled", reason="sin GOOGLE_API_KEY ni OPENROUTER_API_KEY")
            await next_()
            return
        # Un mensaje que solo trae una foto no tiene texto ni interacción, y
        # es justo el que hay que mirar: sin esta condición el asistente lo
        # dejaba pasar de largo.
        if not ctx.text and not ctx.inbound.action and not ctx.inbound.attachments:
            await next_()
            return

        # Cada departamento decide si el asistente contesta en sus
        # conversaciones. Apagado, el mensaje queda en la cola para el equipo,
        # sin respuesta automática y sin el aviso de «no he podido procesar».
        department = await self._department(ctx)
        if department is not None and not ai_settings.for_department(department).enabled:
            log.info("ai_off_for_department", department=department.name)
            ctx.suppress_reply = True
            return
        ctx.scratch["department"] = department

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
        except openrouter.OpenRouterError as exc:
            await self._record_error(ctx, str(exc), started)
            # Igual que con Gemini: el 429 se arregla esperando y se cuenta.
            # El 402 —sin saldo en OpenRouter— queda en el log y en `ai_runs`
            # para quien administra; a quien escribe no le incumbe.
            if exc.status == 429:
                ctx.reply(
                    "Estamos atendiendo muchas consultas en este momento. "
                    "Inténtelo de nuevo en unos segundos, por favor."
                )
                return
            if exc.status == 402:
                log.error("openrouter_without_credit", error=str(exc)[:300])
            await next_()
            return
        except StreamError as exc:
            await self._record_error(ctx, f"{exc.code}: {exc}", started)
            # El cupo agotado se le cuenta a quien escribe, porque se arregla
            # solo esperando; lo demás es cosa nuestra y no se explica.
            if exc.code in {"quota_exceeded", "rate_limit_exceeded"}:
                ctx.reply(
                    "Estamos atendiendo muchas consultas en este momento. "
                    "Inténtelo de nuevo en unos segundos, por favor."
                )
                return
            await next_()
            return

        await repo.record_ai_run(
            ctx.session,
            tenant_id=ctx.tenant.id,
            conversation_id=ctx.conversation.id,
            message_id=ctx.stored_message.id,
            model=self.model_for(ctx.tenant),
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
    async def _department(self, ctx: TurnContext) -> Any:
        """Departamento de la conversación, o ``None`` en la cola común."""
        if ctx.conversation.department_id is None:
            return None
        return await repo.get_department(ctx.session, ctx.conversation.department_id)

    def _build_system_prompt(self, ctx: TurnContext) -> str:
        """Instrucción del sistema: lo estable primero, el contexto después.

        Las instrucciones del departamento van detrás de las generales: las
        completan y, si chocan, mandan, porque son más concretas. La fecha y
        la hora van siempre: sin ellas el modelo no sabe qué es «mañana», y
        una reserva de hotel depende justo de eso.
        """
        contact_name = (ctx.contact.display_name if ctx.contact else None) or "el usuario"
        department = ctx.scratch.get("department")
        partes = [self.settings.ai_system_prompt]
        if department is not None:
            partes.append(f"Atiendes en nombre del departamento «{department.name}».")
            instrucciones = ai_settings.for_department(department).instructions
            if instrucciones:
                partes.append(
                    "Instrucciones propias de este departamento, que mandan sobre las "
                    f"anteriores si no coinciden:\n{instrucciones}"
                )
        partes.append(_current_moment(department, ctx.tenant))
        return (
            "\n\n".join(partes)
            + "\n\n"
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

        Los ficheros de los mensajes recientes viajan con ellos, de modo que el
        modelo ve la foto en lugar de leer que hubo una. Los antiguos se siguen
        describiendo con palabras: mandarlo todo en cada turno multiplicaría el
        coste por cada imagen que alguien haya enviado nunca.
        """
        if ctx.scratch.get("skip_history"):
            history = [ctx.stored_message]
        else:
            history = ctx.history[-self.settings.ai_history_turns :]

        adjuntables = self._media_by_message(history)

        def paso(tipo: str, contenido: list[dict[str, Any]]) -> dict[str, Any]:
            return {"type": tipo, "content": contenido}

        def texto_de(step: dict[str, Any]) -> str:
            # El texto es siempre la primera parte; los ficheros van detrás.
            return step["content"][0]["text"]

        messages: list[dict[str, Any]] = []
        for row in history:
            media = adjuntables.get(row.id, [])
            content = (row.text or "").strip()
            if not content:
                # Con el fichero delante, el modelo ya ve de qué se trata; sin
                # él hace falta contarlo, o el turno llegaría vacío.
                content = "" if media else self._describe_non_text(row)
                if not content and not media:
                    continue
            tipo = "user_input" if row.direction is Direction.INBOUND else "model_output"

            if messages and messages[-1]["type"] == tipo and not media:
                if content:
                    messages[-1]["content"][0]["text"] = (
                        f"{texto_de(messages[-1])}\n{content}"
                    )
                continue

            partes: list[dict[str, Any]] = [
                {"type": "text", "text": content or "(adjunto sin texto)"}
            ]
            partes.extend(media)
            messages.append(paso(tipo, partes))

        vacio = [{"type": "text", "text": ctx.text or "(sin texto)"}]
        if not messages or messages[0]["type"] != "user_input":
            messages.insert(0, paso("user_input", vacio))
        if messages[-1]["type"] == "model_output":
            messages.append(paso("user_input", vacio))
        return messages

    def _media_by_message(self, history: list[Any]) -> dict[Any, list[dict[str, Any]]]:
        """Ficheros que acompañarán a cada mensaje, dentro de los topes.

        Se recorre de lo más reciente a lo más antiguo: si hay que dejar algo
        fuera por tamaño, que sea lo viejo.
        """
        por_mensaje: dict[Any, list[dict[str, Any]]] = {}
        presupuesto = MEDIA_TOTAL_BYTES

        for row in reversed(history[-MEDIA_HISTORY_DEPTH:]):
            for entry in row.attachments or []:
                if not isinstance(entry, dict):
                    continue
                clase = MEDIA_KINDS.get((entry.get("mime_type") or "").lower())
                if clase is None:
                    continue
                fichero = resolve_upload(entry.get("url"), settings=self.settings)
                if fichero is None:
                    continue
                tamano = fichero.stat().st_size
                if tamano > MEDIA_MAX_BYTES or tamano > presupuesto:
                    log.info(
                        "media_skipped_for_model",
                        reason="demasiado grande",
                        size=tamano,
                        mime=entry.get("mime_type"),
                    )
                    continue
                try:
                    datos = fichero.read_bytes()
                except OSError as error:  # pragma: no cover - depende del disco
                    log.warning("media_unreadable", path=str(fichero), error=str(error))
                    continue
                presupuesto -= tamano
                por_mensaje.setdefault(row.id, []).append(
                    {
                        "type": clase,
                        "mime_type": entry["mime_type"],
                        "data": base64.b64encode(datos).decode(),
                    }
                )
        return por_mensaje

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
        department = ctx.scratch.get("department") or await repo.get_department(
            ctx.session, department_id
        )
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
        system: str,
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
            if self.provider == "openrouter":
                text, calls, status, usage = await self._openrouter_round(
                    ctx, system, messages, tools, stream_to_client
                )
            else:
                stream = await self._client.aio.interactions.create(
                    model=self.settings.ai_model,
                    input=messages,
                    system_instruction=system,
                    tools=[_as_function_tool(tool) for tool in tools],
                    generation_config={"max_output_tokens": self.settings.ai_max_tokens},
                    stream=True,
                )
                text, calls, status, raw_usage = await self._read_stream(
                    ctx, stream, stream_to_client
                )
                usage = _gemini_usage(raw_usage)
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

            if tipo == "error":
                detalle = getattr(event, "error", None)
                raise StreamError(
                    getattr(detalle, "code", None),
                    getattr(detalle, "message", None) or "el proveedor cortó el flujo",
                )

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

    async def _openrouter_round(
        self,
        ctx: TurnContext,
        system: str,
        steps: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stream_to_client: bool,
    ) -> tuple[str, list[ToolCall], str | None, dict[str, int]]:
        """Una vuelta contra OpenRouter: petición, lectura y, si hace falta, reintento.

        El reintento es solo para los ficheros. Qué admite cada modelo depende
        del modelo elegido en la consola, y uno que no ve imágenes rechaza la
        petición entera; antes que perder el turno, se repite una vez con cada
        fichero nombrado en texto.
        """
        elegido = ai_settings.read(ctx.tenant, self.settings)
        payload: dict[str, Any] = {
            "model": elegido.model,
            "messages": openrouter.to_chat_messages(system, steps),
            "max_tokens": self.settings.ai_max_tokens,
            "stream": True,
            "usage": {"include": True},
        }
        reasoning = ai_settings.reasoning_payload(elegido.effort)
        if reasoning is not None:
            payload["reasoning"] = reasoning
        if tools:
            payload["tools"] = [
                openrouter.to_chat_tool(_as_function_tool(tool)) for tool in tools
            ]
        try:
            return await self._read_openrouter_stream(ctx, payload, stream_to_client)
        except openrouter.OpenRouterError as exc:
            if exc.status not in {400, 404, 415, 422} or not openrouter.has_media(steps):
                raise
            log.warning("openrouter_media_rejected", model=payload["model"], error=str(exc)[:300])
            payload["messages"] = openrouter.to_chat_messages(
                system, openrouter.without_media(steps)
            )
            return await self._read_openrouter_stream(ctx, payload, stream_to_client)

    async def _read_openrouter_stream(
        self, ctx: TurnContext, payload: dict[str, Any], stream_to_client: bool
    ) -> tuple[str, list[ToolCall], str | None, dict[str, int]]:
        """Rearma texto y llamadas a herramientas a partir de los trozos SSE.

        Las llamadas llegan troceadas igual que con Gemini: el primer trozo de
        cada una trae ``id`` y nombre, y los siguientes, pedazos de los
        argumentos en JSON, todos con el mismo ``index``. ``reasoning`` —lo
        que piensa un modelo que razona— se descarta: no es para el chat.
        """
        partes: list[str] = []
        pendientes: dict[int, dict[str, Any]] = {}
        finish: str | None = None
        usage: dict[str, int] = {}

        async with aclosing(self._client.stream(payload)) as chunks:
            async for chunk in chunks:
                if chunk.get("error"):
                    detalle = chunk["error"]
                    raise StreamError(
                        _openrouter_error_code(detalle.get("code")),
                        detalle.get("message") or "OpenRouter cortó el flujo",
                    )
                if chunk.get("usage"):
                    usage = openrouter.usage_of(chunk["usage"])
                for choice in chunk.get("choices") or []:
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}
                    texto = delta.get("content")
                    if texto:
                        partes.append(texto)
                        if stream_to_client:
                            await hub.publish(
                                conversation_topic(ctx.conversation.channel_conversation_id),
                                {"type": "delta", "text": texto},
                            )
                    for pedazo in delta.get("tool_calls") or []:
                        indice = pedazo.get("index", 0)
                        llamada = pendientes.setdefault(
                            indice, {"id": None, "name": None, "arguments": ""}
                        )
                        llamada["id"] = llamada["id"] or pedazo.get("id")
                        funcion = pedazo.get("function") or {}
                        llamada["name"] = llamada["name"] or funcion.get("name")
                        llamada["arguments"] += funcion.get("arguments") or ""

        if finish == "error":
            raise StreamError("stream_error", "OpenRouter terminó el flujo con error")

        calls = [
            ToolCall(
                # El identificador enlaza la llamada con su resultado en la
                # vuelta siguiente; si el modelo no lo da, se inventa uno.
                id=datos["id"] or f"call_{indice}",
                name=datos["name"],
                arguments=_parse_arguments(datos["arguments"], name=datos["name"]),
            )
            for indice, datos in sorted(pendientes.items())
            if datos["name"]
        ]
        # El filtro de contenido equivale a la negativa de Gemini: se le
        # contesta lo mismo a quien escribe.
        status = "refusal" if finish == "content_filter" else finish
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
                    **access_scope(ctx.conversation),
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
            model=self.model_for(ctx.tenant),
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


#: Cómo se llama cada cifra de consumo en Gemini y con qué nombre se guarda.
#: La traducción hace falta: Gemini las publica con el prefijo ``total_`` y
#: ``record_ai_run`` espera los nombres de la derecha.
USAGE_FIELDS = {
    "total_input_tokens": "input_tokens",
    "total_output_tokens": "output_tokens",
    "total_cached_tokens": "cached_content_token_count",
    "total_thought_tokens": "thoughts_token_count",
    "total_tokens": "total_tokens",
}


_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
)


def _current_moment(department: Any, tenant: Any) -> str:
    """«Fecha y hora actual: jueves 22 de octubre de 2026, 14:35 (America/Asuncion).»

    En la zona del departamento, o la de la empresa si no fija una; es la
    misma con la que se calcula el horario de atención.
    """
    policy = resolve_service_policy(department, getattr(tenant, "settings", None))
    zone = resolve_timezone(policy.timezone)
    ahora = utcnow().astimezone(zone)
    return (
        f"Fecha y hora actual: {_WEEKDAYS[ahora.weekday()]} {ahora.day} de "
        f"{_MONTHS[ahora.month - 1]} de {ahora.year}, {ahora:%H:%M} ({zone.key})."
    )


def _gemini_usage(usage: Any) -> dict[str, int]:
    """Consumo de una vuelta de Gemini, con los nombres que se guardan."""
    if usage is None:
        return {}
    return {
        destino: value
        for origen, destino in USAGE_FIELDS.items()
        if isinstance(value := getattr(usage, origen, None), int)
    }


def _accumulate_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    """Suma el gasto de cada vuelta del bucle de herramientas."""
    for clave, value in usage.items():
        total[clave] = total.get(clave, 0) + value


def _openrouter_error_code(code: Any) -> str:
    """Código de un error dentro del flujo, en los términos que ya se manejan.

    OpenRouter manda el código HTTP como número; el manejo de errores del
    turno habla en los términos de Gemini, así que se traduce aquí.
    """
    if code in (429, "429"):
        return "rate_limit_exceeded"
    if code in (402, "402"):
        return "insufficient_credits"
    return str(code) if code else "stream_error"
