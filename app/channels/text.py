"""Utilidades de texto compartidas por los adaptadores de canal.

Todo canal impone un tope de caracteres por mensaje —4096 en WhatsApp y
Telegram, 2000 en Messenger, 1600 en un SMS— y todos necesitan la misma
operación: partir una respuesta larga por donde menos se note. La función vivía
copiada, letra por letra, en cada adaptador.
"""

from __future__ import annotations


def split_text(text: str, limit: int) -> list[str]:
    """Divide por saltos de línea cuando es posible, y por longitud si no.

    El corte se busca primero en un salto de línea y luego en un espacio, pero
    solo si cae en la segunda mitad del trozo: cortar en el primer espacio
    disponible dejaría fragmentos ridículamente cortos.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
