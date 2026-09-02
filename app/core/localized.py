"""Textos que salen hacia el cliente, en el idioma en que él escribe.

A diferencia de la interfaz —que traduce el equipo desde el navegador—, estos
son mensajes que ustedes redactan: el aviso fuera de horario y la respuesta
del asistente cuando no logra resolver. Traducirlos automáticamente sería
poner en boca de la empresa algo que nadie revisó, así que se guardan escritos
por idioma y aquí solo se elige cuál corresponde.

Un texto admite dos formas, y ambas siguen valiendo:

* Una cadena suelta — lo que había antes de existir este módulo — que se envía
  igual a todo el mundo.
* Un objeto por idioma, ``{"es": "…", "en": "…"}``, del que se toma el del
  contacto y, si no está escrito, el del idioma de respaldo.
"""

from __future__ import annotations

from typing import Any

#: Idioma en el que se responde a quien no declara ninguno, o declara uno que
#: nadie escribió todavía.
FALLBACK_LOCALE = "es"

#: Los idiomas que la interfaz ofrece; el mismo juego que ``i18n.js``.
SUPPORTED_LOCALES = ("es", "en", "de")


def normalize_locale(locale: str | None) -> str | None:
    """``"de-DE"`` → ``"de"``. Devuelve ``None`` si no es uno de los nuestros.

    El navegador manda la región junto al idioma, pero para elegir un texto
    solo importa el idioma: no hay una versión para Alemania y otra para
    Austria.
    """
    if not locale:
        return None
    code = str(locale).strip().lower().replace("_", "-").split("-")[0]
    return code if code in SUPPORTED_LOCALES else None


def pick(value: Any, locale: str | None) -> str | None:
    """Texto que corresponde a ``locale``, sea cual sea la forma de ``value``.

    El orden es: el idioma del contacto, el de respaldo y, si tampoco está,
    cualquier otro que sí se haya escrito — antes que dejar al cliente sin
    respuesta por un idioma que nadie completó.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None

    code = normalize_locale(locale)
    for candidate in (code, FALLBACK_LOCALE):
        if candidate and str(value.get(candidate) or "").strip():
            return value[candidate]
    for written in value.values():
        if isinstance(written, str) and written.strip():
            return written
    return None


def has_any(value: Any) -> bool:
    """¿Hay algo escrito, en el idioma que sea?"""
    return pick(value, None) is not None
