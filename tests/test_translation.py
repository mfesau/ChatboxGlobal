"""Traducción para el equipo: lo del cliente en el idioma del agente, y al revés."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.config import get_settings
from app.core import translation
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import Message
from tests.test_teamwork import arrive


def _openrouter(contenido: dict[str, Any], llamadas: list[dict[str, Any]]):
    """OpenRouter simulado que siempre contesta ``contenido`` como JSON."""

    def responder(request: httpx.Request) -> httpx.Response:
        llamadas.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(contenido, ensure_ascii=False)}}]},
        )

    return httpx.MockTransport(responder)


def _settings():
    return get_settings().model_copy(update={"openrouter_api_key": SecretStr("sk-or-prueba")})


async def test_a_batch_is_translated_in_one_request():
    llamadas: list[dict[str, Any]] = []
    transporte = _openrouter(
        {
            "items": [
                {"id": "1", "source": "es", "translation": "Ich möchte ein Zimmer buchen"},
                {"id": "2", "source": "de", "translation": None},
            ]
        },
        llamadas,
    )

    hechas = await translation.translate_batch(
        _settings(),
        None,
        [("1", "Quiero reservar una habitación"), ("2", "Danke schön")],
        "de",
        transport=transporte,
    )

    assert len(llamadas) == 1
    assert llamadas[0]["response_format"] == {"type": "json_object"}
    assert hechas == {"1": ("es", "Ich möchte ein Zimmer buchen"), "2": ("de", None)}


async def test_a_reply_wrapped_in_a_code_fence_is_still_read():
    def responder(request: httpx.Request) -> httpx.Response:
        contenido = '```json\n{"translation": "Guten Tag"}\n```'
        return httpx.Response(200, json={"choices": [{"message": {"content": contenido}}]})

    texto = await translation.translate_text(
        _settings(), None, "Buenos días", "de", transport=httpx.MockTransport(responder)
    )
    assert texto == "Guten Tag"


async def test_without_provider_translation_is_unavailable():
    with pytest.raises(translation.TranslationUnavailable):
        await translation.translate_text(get_settings(), None, "Hola", "de")


# --------------------------------------------------------------------------- #
# Consola
# --------------------------------------------------------------------------- #
@pytest.fixture
def traductor(monkeypatch):
    """Sustituye al modelo: traduce poniendo el idioma delante."""
    pedidos: list[tuple[str, list]] = []

    async def batch(settings, tenant, items, target, **kwargs):
        pedidos.append((target, items))
        return {ident: ("es", f"[{target}] {texto}") for ident, texto in items}

    async def single(settings, tenant, text, target, **kwargs):
        return f"[{target}] {text}"

    monkeypatch.setattr(translation, "translate_batch", batch)
    monkeypatch.setattr(translation, "translate_text", single)
    return pedidos


async def test_the_console_gets_the_customer_messages_in_its_language(
    anonymous, as_agent, team, traductor
):
    conversation_id = await arrive(anonymous, "cliente-traducido", "Quiero reservar")
    ana = await as_agent(team["ana"]["email"])

    primera = await ana.post(
        f"/api/conversations/{conversation_id}/translate", json={"target": "de"}
    )
    assert primera.status_code == 200, primera.text
    cuerpo = primera.json()
    assert "[de] Quiero reservar" in cuerpo["translations"].values()
    assert cuerpo["customer_language"] == "es"

    # Lo ya traducido se guarda: abrirla otra vez no vuelve a llamar al modelo.
    await ana.post(f"/api/conversations/{conversation_id}/translate", json={"target": "de"})
    assert len(traductor) == 1

    mensajes = (await ana.get(f"/api/conversations/{conversation_id}/messages")).json()
    entrante = next(m for m in mensajes if m["direction"] == "inbound")
    assert entrante["translations"]["de"] == "[de] Quiero reservar"
    assert entrante["translations"]["_source"] == "es"


async def test_a_reply_goes_out_translated_and_keeps_the_original(
    anonymous, as_agent, team, traductor
):
    conversation_id = await arrive(anonymous, "cliente-respuesta", "Hola")
    ana = await as_agent(team["ana"]["email"])

    enviada = await ana.post(
        f"/api/conversations/{conversation_id}/reply",
        json={"text": "Guten Tag, wie kann ich helfen?", "translate_to": "es",
              "source_language": "de"},
    )
    assert enviada.status_code == 202, enviada.text

    async with session_scope() as session:
        mensajes = await repo.recent_messages(session, uuid.UUID(conversation_id))
        respuesta: Message = next(m for m in mensajes if m.author_type == "agent")
    assert respuesta.text == "[es] Guten Tag, wie kann ich helfen?"
    assert respuesta.translations == {
        "_source": "es",
        "de": "Guten Tag, wie kann ich helfen?",
    }


async def test_an_unknown_language_is_refused(anonymous, as_agent, team, traductor):
    conversation_id = await arrive(anonymous, "cliente-idioma", "Hola")
    ana = await as_agent(team["ana"]["email"])

    assert (
        await ana.post(f"/api/conversations/{conversation_id}/translate", json={"target": "xx"})
    ).status_code == 422


async def test_without_provider_the_console_hears_503(anonymous, as_agent, team):
    conversation_id = await arrive(anonymous, "cliente-sin-ia", "Hola")
    ana = await as_agent(team["ana"]["email"])

    respuesta = await ana.post(
        f"/api/conversations/{conversation_id}/translate", json={"target": "de"}
    )
    assert respuesta.status_code == 503
