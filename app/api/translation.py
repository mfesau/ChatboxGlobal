"""Traducción de una conversación en la consola (ver ``app/core/translation.py``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import PrincipalDep, SessionDep, SettingsDep, authorized_conversation
from app.core import translation
from app.db import repositories as repo
from app.db.models import Conversation, Tenant

router = APIRouter(prefix="/api/conversations", tags=["consola"])

#: Cuántos mensajes recientes se traducen al abrir una conversación.
RECENT = 60


class TranslateIn(BaseModel):
    target: str


def customer_language(messages: list[Any]) -> str | None:
    """Idioma del cliente: el del último mensaje suyo cuyo idioma se conoce."""
    for message in reversed(messages):
        if str(message.direction) == "inbound":
            source = (message.translations or {}).get(translation.SOURCE_KEY)
            if source:
                return source
    return None


@router.post("/{conversation_id}/translate")
async def translate_conversation(
    body: TranslateIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    conversation: Conversation = Depends(authorized_conversation),  # noqa: B008
) -> dict[str, Any]:
    """Traduce al idioma de quien mira los mensajes recientes que aún no lo estén.

    Lo ya traducido se reutiliza. Devuelve todas las traducciones, también las
    guardadas, y el idioma del cliente, que la consola usa para ofrecer enviar
    la respuesta traducida.
    """
    target = body.target.lower()
    if target not in translation.LANGUAGES:
        raise HTTPException(status_code=422, detail=f"Idioma no admitido: {body.target}")

    tenant = await session.get(Tenant, conversation.tenant_id)
    messages = await repo.recent_messages(session, conversation.id, limit=RECENT)
    pendientes = [
        (str(message.id), message.text)
        for message in messages
        if message.text and target not in (message.translations or {})
    ]
    if pendientes:
        try:
            hechas = await translation.translate_batch(settings, tenant, pendientes, target)
        except translation.TranslationUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        for message in messages:
            hecho = hechas.get(str(message.id))
            if hecho is None:
                continue
            source, texto = hecho
            nuevas = dict(message.translations or {})
            nuevas[target] = texto
            if source and translation.SOURCE_KEY not in nuevas:
                nuevas[translation.SOURCE_KEY] = source
            # Reasignar, no mutar: así SQLAlchemy ve el cambio en el JSON.
            message.translations = nuevas
        await session.flush()

    return {
        "target": target,
        "customer_language": customer_language(messages),
        "translations": {
            str(message.id): (message.translations or {}).get(target)
            for message in messages
            if message.text
        },
    }
