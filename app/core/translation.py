"""Traducción de los mensajes para el equipo.

La cooperativa atiende en castellano y en alemán, y no todo el equipo lee bien
los dos. Con esto cualquier agente puede llevar cualquier conversación: lo que
escribe el cliente le aparece también en su idioma, y lo que él contesta sale
en el del cliente. El original nunca se pierde: el cliente recibe la versión
traducida y la consola enseña las dos.

Usa el mismo proveedor y el mismo modelo que el asistente (``Settings.ai_backend``
y la elección de la consola), sin herramientas y con salida en JSON. Un lote
entero de mensajes va en una sola petición: abrir una conversación larga no
puede costar una llamada por mensaje.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.core import ai_settings
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Idiomas que se ofrecen, por su código ISO 639-1. El modelo traduce entre
#: muchos más; esta lista es la de la consola y la de la validación.
LANGUAGES: dict[str, str] = {
    "es": "español",
    "de": "alemán",
    "en": "inglés",
    "pt": "portugués",
}

#: Tope de caracteres por lote. Una conversación larga se traduce en varias
#: peticiones antes que en una que el modelo corte a medias.
BATCH_CHARS = 6_000

#: Clave, dentro de ``Message.translations``, del idioma en que está escrito.
SOURCE_KEY = "_source"


class TranslationUnavailable(RuntimeError):
    """No hay proveedor de IA configurado, o no respondió."""


def _strip_fences(raw: str) -> str:
    """Quita el ```json … ``` con que algunos modelos envuelven el JSON."""
    match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    return match.group(1) if match else raw


async def _complete_json(
    settings: Any, tenant: Any, system: str, user: str, *, transport: Any = None
) -> Any:
    backend = settings.ai_backend
    if backend == "openrouter":
        elegido = ai_settings.read(tenant, settings)
        payload = {
            "model": elegido.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": settings.ai_max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=60.0, transport=transport) as http:
                response = await http.post(
                    f"{settings.openrouter_base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}"
                    },
                )
        except httpx.HTTPError as exc:
            raise TranslationUnavailable(f"OpenRouter no respondió: {exc!r}") from exc
        if response.status_code != 200:
            raise TranslationUnavailable(f"OpenRouter HTTP {response.status_code}")
        raw = response.json()["choices"][0]["message"].get("content") or ""
    elif backend == "gemini":
        from google import genai  # importación diferida: el módulo es pesado

        client = genai.Client(api_key=settings.google_api_key.get_secret_value())
        try:
            response = await client.aio.models.generate_content(
                model=settings.ai_model,
                contents=user,
                config={"system_instruction": system, "response_mime_type": "application/json"},
            )
        except Exception as exc:  # el SDK lanza varias clases distintas
            raise TranslationUnavailable(f"Gemini no respondió: {exc}") from exc
        raw = response.text or ""
    else:
        raise TranslationUnavailable("El asistente no tiene proveedor configurado")

    try:
        return json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        log.warning("translation_unreadable", raw=raw[:300])
        raise TranslationUnavailable("La traducción llegó ilegible") from exc


SYSTEM_BATCH = (
    "Eres un traductor profesional de atención al cliente. Recibes una lista "
    "JSON de mensajes con su id. Para cada uno, detecta su idioma (código ISO "
    "639-1) y tradúcelo al idioma de destino conservando el tono, los nombres, "
    "las cifras y los saltos de línea. Si el mensaje ya está en el idioma de "
    "destino, devuelve translation null. Responde solo con JSON de la forma "
    '{"items": [{"id": "...", "source": "es", "translation": "..."}]}.'
)


async def translate_batch(
    settings: Any,
    tenant: Any,
    items: list[tuple[str, str]],
    target: str,
    *,
    transport: Any = None,
) -> dict[str, tuple[str | None, str | None]]:
    """Traduce ``[(id, texto)]`` a ``target``. Devuelve ``{id: (origen, traducción)}``.

    ``traducción`` es ``None`` cuando el texto ya estaba en ese idioma.
    """
    resultado: dict[str, tuple[str | None, str | None]] = {}
    lote: list[tuple[str, str]] = []
    tamano = 0

    async def enviar() -> None:
        if not lote:
            return
        user = json.dumps(
            {
                "target": f"{target} ({LANGUAGES.get(target, target)})",
                "messages": [{"id": ident, "text": texto} for ident, texto in lote],
            },
            ensure_ascii=False,
        )
        data = await _complete_json(settings, tenant, SYSTEM_BATCH, user, transport=transport)
        for item in (data or {}).get("items", []) if isinstance(data, dict) else []:
            ident = str(item.get("id", ""))
            if ident:
                source = (item.get("source") or "").lower()[:5] or None
                translation = item.get("translation")
                if source == target:
                    translation = None
                resultado[ident] = (source, translation or None)

    for ident, texto in items:
        if lote and tamano + len(texto) > BATCH_CHARS:
            await enviar()
            lote, tamano = [], 0
        lote.append((ident, texto))
        tamano += len(texto)
    await enviar()
    return resultado


SYSTEM_SINGLE = (
    "Eres un traductor profesional de atención al cliente. Traduce el mensaje "
    "que escribe un agente al idioma de destino, conservando el tono cordial, "
    "los nombres, las cifras y los saltos de línea. No añadas nada. Responde "
    'solo con JSON de la forma {"translation": "..."}.'
)


async def translate_text(
    settings: Any, tenant: Any, text: str, target: str, *, transport: Any = None
) -> str:
    """Traduce un solo texto, el que el agente va a enviar."""
    user = json.dumps(
        {"target": f"{target} ({LANGUAGES.get(target, target)})", "text": text},
        ensure_ascii=False,
    )
    data = await _complete_json(settings, tenant, SYSTEM_SINGLE, user, transport=transport)
    translation = data.get("translation") if isinstance(data, dict) else None
    if not translation or not isinstance(translation, str):
        raise TranslationUnavailable("La traducción llegó vacía")
    return translation.strip()
