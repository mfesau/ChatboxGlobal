"""Cliente de OpenRouter para el asistente.

OpenRouter expone la API de *chat completions* de OpenAI delante de modelos de
muchos proveedores. Aquí vive solo lo que es propio de ese formato: traducir
los pasos que arma ``AIHandler`` a mensajes con ``role``, y leer la respuesta
transmitida por eventos SSE. El bucle de herramientas, los topes y los
mensajes al usuario siguen en ``app/handlers/ai.py``, iguales para los dos
proveedores.

No se usa el SDK de OpenAI: añadiría una dependencia grande para una sola
petición HTTP, y ``httpx`` ya está.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.logging_setup import get_logger

log = get_logger(__name__)

#: Una respuesta transmitida puede tardar en empezar —los modelos que razonan
#: piensan antes de escribir—, pero entre trozo y trozo no debería pasar tanto.
TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

#: Formato que OpenRouter espera para cada audio guardado.
AUDIO_FORMATS = {
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
}


class OpenRouterError(RuntimeError):
    """La petición no llegó a dar respuesta: error HTTP o de red.

    ``status`` es el código HTTP, o ``None`` cuando ni siquiera hubo respuesta.
    """

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


class OpenRouterClient:
    """Una petición de chat transmitida, con la clave y las cabeceras puestas."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        app_url: str | None = None,
        app_title: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        # Opcionales: sirven para que el panel de OpenRouter diga de qué
        # aplicación es cada gasto.
        if app_url:
            self._headers["HTTP-Referer"] = app_url
        if app_title:
            self._headers["X-Title"] = app_title
        self._transport = transport

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Envía la petición y entrega cada trozo del flujo ya decodificado.

        Las líneas que empiezan por ``:`` son comentarios con los que OpenRouter
        mantiene viva la conexión mientras el modelo arranca; no traen datos.
        """
        try:
            async with (
                httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as http,
                http.stream("POST", self._url, json=payload, headers=self._headers) as response,
            ):
                if response.status_code != 200:
                    cuerpo = await response.aread()
                    raise OpenRouterError(
                        response.status_code, _error_message(cuerpo, response.status_code)
                    )
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        log.warning("openrouter_chunk_unreadable", chunk=data[:200])
        except httpx.HTTPError as exc:
            raise OpenRouterError(None, f"sin respuesta de OpenRouter: {exc!r}") from exc


def _error_message(body: bytes, status: int) -> str:
    """Saca el motivo del cuerpo de error, que suele ser JSON pero no siempre."""
    try:
        detalle = json.loads(body).get("error") or {}
        mensaje = detalle.get("message") if isinstance(detalle, dict) else str(detalle)
    except (ValueError, AttributeError):
        mensaje = None
    return f"HTTP {status}: {mensaje or body[:300].decode(errors='replace')}"


# --------------------------------------------------------------------------- #
# Traducción de los pasos de AIHandler al formato de chat
# --------------------------------------------------------------------------- #
def to_chat_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Herramienta neutra -> función en el formato de OpenAI.

    Se recibe ya pasada por ``_as_function_tool``, que quita lo que algunos
    proveedores rechazan; aquí solo cambia el envoltorio.
    """
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def to_chat_messages(system: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte la lista de pasos en mensajes con ``role``.

    Las llamadas a herramientas seguidas se agrupan en un único mensaje del
    asistente, que es como las devolvió el modelo; cada resultado va en su
    propio mensaje ``tool`` enlazado por el identificador de la llamada.
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for step in steps:
        tipo = step.get("type")
        if tipo == "user_input":
            messages.append({"role": "user", "content": _user_parts(step["content"])})
        elif tipo == "model_output":
            # Lo que mandó el equipo puede llevar ficheros, pero un mensaje del
            # asistente solo admite texto: basta con lo que dice.
            texto = "\n".join(
                part.get("text", "") for part in step["content"] if part.get("type") == "text"
            )
            messages.append({"role": "assistant", "content": texto})
        elif tipo == "function_call":
            llamada = {
                "id": step["id"],
                "type": "function",
                "function": {
                    "name": step["name"],
                    "arguments": json.dumps(step.get("arguments") or {}, ensure_ascii=False),
                },
            }
            previo = messages[-1]
            if previo["role"] == "assistant" and previo.get("tool_calls"):
                previo["tool_calls"].append(llamada)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [llamada]})
        elif tipo == "function_result":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": step["call_id"],
                    "content": str(step.get("result", "")),
                }
            )
    return messages


def _user_parts(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    partes: list[dict[str, Any]] = []
    for part in content:
        clase = part.get("type")
        if clase == "text":
            partes.append({"type": "text", "text": part["text"]})
            continue
        mime = part.get("mime_type", "")
        datos = part.get("data", "")
        data_url = f"data:{mime};base64,{datos}"
        if clase == "image":
            partes.append({"type": "image_url", "image_url": {"url": data_url}})
        elif clase == "document":
            partes.append(
                {"type": "file", "file": {"filename": "documento.pdf", "file_data": data_url}}
            )
        elif clase == "audio" and mime in AUDIO_FORMATS:
            partes.append(
                {
                    "type": "input_audio",
                    "input_audio": {"data": datos, "format": AUDIO_FORMATS[mime]},
                }
            )
        elif clase == "video":
            partes.append({"type": "video_url", "video_url": {"url": data_url}})
    return partes


def has_media(steps: list[dict[str, Any]]) -> bool:
    return any(
        part.get("type") not in (None, "text")
        for step in steps
        if step.get("type") == "user_input"
        for part in step["content"]
    )


def without_media(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los mismos pasos con cada fichero cambiado por una línea que lo nombra.

    Para el modelo que no admite ese tipo de entrada: OpenRouter rechaza la
    petición entera, y es mejor que el asistente sepa que hubo una foto, aunque
    no la vea, que quedarse sin turno.
    """
    nombres = {
        "image": "una imagen",
        "document": "un documento PDF",
        "audio": "un audio",
        "video": "un vídeo",
    }
    resultado: list[dict[str, Any]] = []
    for step in steps:
        if step.get("type") != "user_input":
            resultado.append(step)
            continue
        partes = []
        for part in step["content"]:
            if part.get("type") == "text":
                partes.append(part)
            else:
                nombre = nombres.get(part.get("type"), "un archivo")
                partes.append(
                    {"type": "text", "text": f"[el usuario adjuntó {nombre} que no puedes ver]"}
                )
        resultado.append({**step, "content": partes})
    return resultado


#: Nombre de cada cifra de consumo en la respuesta -> nombre con que se guarda.
USAGE_FIELDS = {
    "prompt_tokens": "input_tokens",
    "completion_tokens": "output_tokens",
    "total_tokens": "total_tokens",
}


def usage_of(raw: dict[str, Any] | None) -> dict[str, int]:
    """Consumo de una vuelta, con los nombres que espera ``record_ai_run``."""
    if not raw:
        return {}
    uso = {
        destino: raw[origen]
        for origen, destino in USAGE_FIELDS.items()
        if isinstance(raw.get(origen), int)
    }
    cacheados = (raw.get("prompt_tokens_details") or {}).get("cached_tokens")
    if isinstance(cacheados, int):
        uso["cached_content_token_count"] = cacheados
    razonados = (raw.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if isinstance(razonados, int):
        uso["thoughts_token_count"] = razonados
    return uso
