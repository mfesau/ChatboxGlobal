"""El asistente con OpenRouter, y su modelo y esfuerzo elegidos desde la consola.

OpenRouter se simula con un transporte de ``httpx``: las pruebas no salen a la
red ni gastan saldo, pero el cliente recorre el mismo camino que en producción
—cabeceras, SSE, códigos de error—.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.core import ai_settings
from app.handlers import openrouter
from app.handlers.ai import AIHandler, StreamError


def _settings(**changes: Any) -> Settings:
    valores = {"openrouter_api_key": "sk-or-prueba", "database_url": "sqlite+aiosqlite://"}
    valores.update(changes)
    return Settings(**valores)


def _sse(*chunks: dict[str, Any]) -> bytes:
    """Cuerpo SSE como lo manda OpenRouter, comentario de espera incluido."""
    lineas = [": OPENROUTER PROCESSING", ""]
    for chunk in chunks:
        lineas += [f"data: {json.dumps(chunk)}", ""]
    lineas += ["data: [DONE]", ""]
    return "\n".join(lineas).encode()


def _texto(*trozos: str, usage: dict[str, Any] | None = None) -> bytes:
    chunks = [{"choices": [{"delta": {"content": t}}]} for t in trozos]
    chunks.append(
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage or {}}
    )
    return _sse(*chunks)


class FakeOpenRouter:
    """Responde por turno lo que se le haya encolado y guarda lo que recibe."""

    def __init__(self, *respuestas: tuple[int, bytes]) -> None:
        self.respuestas = list(respuestas)
        self.peticiones: list[dict[str, Any]] = []
        self.cabeceras: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.peticiones.append(json.loads(request.content))
        self.cabeceras.append(request.headers)
        status, cuerpo = self.respuestas.pop(0)
        return httpx.Response(
            status, content=cuerpo, headers={"content-type": "text/event-stream"}
        )


def _handler(fake: FakeOpenRouter, **changes: Any) -> AIHandler:
    return AIHandler(_settings(**changes), transport=httpx.MockTransport(fake))


def _ctx(*, tenant_settings: dict[str, Any] | None = None) -> Any:
    """Turno mínimo, fuera del chatbox web para no publicar en el hub."""
    replies: list[str] = []
    return SimpleNamespace(
        tenant=SimpleNamespace(id="t", slug="default", settings=tenant_settings or {}),
        conversation=SimpleNamespace(
            channel="whatsapp", channel_conversation_id="c", department_id=None
        ),
        replies=replies,
        reply=replies.append,
        scratch={},
    )


def _user(texto: str, *media: dict[str, Any]) -> dict[str, Any]:
    return {"type": "user_input", "content": [{"type": "text", "text": texto}, *media]}


# --------------------------------------------------------------------------- #
# Qué proveedor responde
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("cambios", "esperado"),
    [
        ({}, "openrouter"),
        ({"google_api_key": "g"}, "openrouter"),
        ({"google_api_key": "g", "ai_provider": "gemini"}, "gemini"),
        ({"openrouter_api_key": None, "google_api_key": "g"}, "gemini"),
        ({"openrouter_api_key": None}, None),
        ({"openrouter_api_key": None, "google_api_key": "g", "ai_provider": "openrouter"}, None),
    ],
)
def test_the_backend_follows_the_keys_and_the_provider_setting(cambios, esperado):
    assert _settings(**cambios).ai_backend == esperado


def test_the_assistant_is_on_with_only_the_openrouter_key():
    handler = _handler(FakeOpenRouter())
    assert handler.enabled
    assert handler.provider == "openrouter"


# --------------------------------------------------------------------------- #
# Traducción al formato de chat
# --------------------------------------------------------------------------- #
def test_steps_become_chat_messages_with_tool_calls_grouped():
    pasos = [
        _user("Hola"),
        {"type": "model_output", "content": [{"type": "text", "text": "¿En qué ayudo?"}]},
        _user("Quiero hablar con alguien"),
        {"type": "function_call", "id": "a", "name": "derivar_a_agente",
         "arguments": {"motivo": "pide persona", "urgencia": "media"}},
        {"type": "function_call", "id": "b", "name": "otra", "arguments": {}},
        {"type": "function_result", "call_id": "a", "name": "derivar_a_agente", "result": "ok"},
        {"type": "function_result", "call_id": "b", "name": "otra", "result": "hecho"},
    ]
    mensajes = openrouter.to_chat_messages("Eres amable", pasos)

    assert [m["role"] for m in mensajes] == [
        "system", "user", "assistant", "user", "assistant", "tool", "tool",
    ]
    llamadas = mensajes[4]["tool_calls"]
    assert [c["id"] for c in llamadas] == ["a", "b"]
    assert json.loads(llamadas[0]["function"]["arguments"])["urgencia"] == "media"
    assert mensajes[5] == {"role": "tool", "tool_call_id": "a", "content": "ok"}


def test_media_travels_in_the_shape_openrouter_expects():
    pasos = [
        _user(
            "Mire esto",
            {"type": "image", "mime_type": "image/png", "data": "AAA"},
            {"type": "document", "mime_type": "application/pdf", "data": "BBB"},
            {"type": "audio", "mime_type": "audio/ogg", "data": "CCC"},
        )
    ]
    partes = openrouter.to_chat_messages("s", pasos)[1]["content"]

    assert partes[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}
    assert partes[2]["file"]["file_data"] == "data:application/pdf;base64,BBB"
    assert partes[3] == {"type": "input_audio", "input_audio": {"data": "CCC", "format": "ogg"}}


# --------------------------------------------------------------------------- #
# Un turno completo
# --------------------------------------------------------------------------- #
async def test_a_plain_answer_is_rebuilt_from_the_stream():
    fake = FakeOpenRouter(
        (200, _texto("Buenos ", "días", usage={
            "prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128,
            "prompt_tokens_details": {"cached_tokens": 100},
        }))
    )
    handler = _handler(fake)

    texto, uso, parada = await handler._converse(_ctx(), "Eres amable", [_user("Hola")], [])

    assert texto == "Buenos días"
    assert parada == "stop"
    assert uso == {
        "input_tokens": 120, "output_tokens": 8, "total_tokens": 128,
        "cached_content_token_count": 100,
    }
    peticion = fake.peticiones[0]
    assert peticion["model"] == "google/gemini-3.8-flash"
    assert peticion["stream"] is True
    assert peticion["messages"][0] == {"role": "system", "content": "Eres amable"}
    # Sin esfuerzo elegido no se manda nada: cada modelo usa el suyo.
    assert "reasoning" not in peticion
    assert fake.cabeceras[0]["authorization"] == "Bearer sk-or-prueba"


async def test_the_model_and_effort_chosen_in_the_console_are_used():
    fake = FakeOpenRouter((200, _texto("Hola")))
    handler = _handler(fake)
    ctx = _ctx(tenant_settings={"ai": {"model": "anthropic/claude-haiku-5.5", "effort": "low"}})

    await handler._converse(ctx, "s", [_user("Hola")], [])

    assert fake.peticiones[0]["model"] == "anthropic/claude-haiku-5.5"
    assert fake.peticiones[0]["reasoning"] == {"effort": "low", "exclude": True}
    assert handler.model_for(ctx.tenant) == "anthropic/claude-haiku-5.5"


async def test_a_tool_call_is_run_and_its_result_sent_back(monkeypatch):
    def pedazo(**campos: Any) -> dict[str, Any]:
        return {"choices": [{"delta": {"tool_calls": [{"index": 0, **campos}]}}]}

    llamada = _sse(
        pedazo(id="call_1", type="function",
               function={"name": "buscar_en_base_de_conocimiento", "arguments": '{"preg'}),
        pedazo(function={"arguments": 'unta": "precio"}'}),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    )
    fake = FakeOpenRouter((200, llamada), (200, _texto("Cuesta 100.")))
    handler = _handler(fake)
    pedidas: list[tuple[str, dict[str, Any]]] = []

    async def dispatch(ctx, name, arguments):
        pedidas.append((name, arguments))
        return "La habitación cuesta 100."

    monkeypatch.setattr(handler, "_dispatch_tool", dispatch)
    from app.handlers.ai import BUSCAR_CONOCIMIENTO_TOOL

    texto, _, _ = await handler._converse(
        _ctx(), "s", [_user("¿Cuánto cuesta?")], [BUSCAR_CONOCIMIENTO_TOOL]
    )

    assert texto == "Cuesta 100."
    assert pedidas == [("buscar_en_base_de_conocimiento", {"pregunta": "precio"})]
    herramienta = fake.peticiones[0]["tools"][0]
    assert herramienta["function"]["name"] == "buscar_en_base_de_conocimiento"
    segunda = fake.peticiones[1]["messages"]
    assert segunda[-2]["tool_calls"][0]["id"] == "call_1"
    assert segunda[-1] == {
        "role": "tool", "tool_call_id": "call_1", "content": "La habitación cuesta 100.",
    }


async def test_a_model_that_cannot_see_images_gets_them_named_instead():
    fake = FakeOpenRouter(
        (404, b'{"error": {"message": "No endpoints found that support image input"}}'),
        (200, _texto("Recibí su foto, pero no puedo verla.")),
    )
    handler = _handler(fake)
    pasos = [_user("¿Qué es esto?", {"type": "image", "mime_type": "image/jpeg", "data": "AAA"})]

    texto, _, _ = await handler._converse(_ctx(), "s", pasos, [])

    assert texto.startswith("Recibí su foto")
    reintento = fake.peticiones[1]["messages"][1]["content"]
    assert all(parte["type"] == "text" for parte in reintento)
    assert "imagen" in reintento[1]["text"]


async def test_an_http_error_carries_its_status():
    fake = FakeOpenRouter((429, b'{"error": {"message": "Rate limit exceeded", "code": 429}}'))
    handler = _handler(fake)

    with pytest.raises(openrouter.OpenRouterError) as error:
        await handler._converse(_ctx(), "s", [_user("Hola")], [])
    assert error.value.status == 429
    assert "Rate limit exceeded" in str(error.value)


async def test_an_error_inside_the_stream_is_not_mistaken_for_silence():
    fake = FakeOpenRouter(
        (200, _sse({"error": {"code": 429, "message": "cupo agotado"},
                    "choices": [{"delta": {}, "finish_reason": "error"}]}))
    )
    handler = _handler(fake)

    with pytest.raises(StreamError) as error:
        await handler._converse(_ctx(), "s", [_user("Hola")], [])
    assert error.value.code == "rate_limit_exceeded"


async def test_a_busy_provider_is_explained_to_the_customer(monkeypatch):
    fake = FakeOpenRouter((429, b'{"error": {"message": "Rate limit exceeded"}}'))
    handler = _handler(fake)
    ctx = _ctx()
    ctx.text = "Hola"
    ctx.inbound = SimpleNamespace(action=None, attachments=[])
    ctx.stored_message = SimpleNamespace(id="m")
    ctx.session = None
    ctx.conversation.id = "c"

    async def nada(*args, **kwargs):
        return None

    async def sin_herramientas(ctx):
        return []

    monkeypatch.setattr("app.handlers.ai.repo.record_ai_run", nada)
    monkeypatch.setattr(handler, "_available_tools", sin_herramientas)
    monkeypatch.setattr(handler, "_build_messages", lambda ctx: [_user("Hola")])
    monkeypatch.setattr(handler, "_build_system_prompt", lambda ctx: "s")
    siguiente = []

    async def next_():
        siguiente.append(True)

    await handler.handle(ctx, next_)

    assert ctx.replies and "muchas consultas" in ctx.replies[0]
    assert not siguiente


# --------------------------------------------------------------------------- #
# Consola: modelo y esfuerzo
# --------------------------------------------------------------------------- #
CATALOGO = {
    "data": [
        {"id": "google/gemini-3.8-flash", "name": "Gemini 3.8 Flash",
         "supported_parameters": ["tools", "reasoning"],
         "architecture": {"input_modalities": ["text", "image"]},
         "pricing": {"prompt": "0.000000825", "completion": "0.000004125"}},
        {"id": "anthropic/claude-haiku-5.5", "name": "Claude Haiku 5.5",
         "supported_parameters": ["tools"],
         "architecture": {"input_modalities": ["text"]},
         "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}},
        {"id": "sin/herramientas", "name": "Sin herramientas",
         "supported_parameters": ["temperature"]},
    ]
}


@pytest.fixture
def catalogo(monkeypatch):
    """Catálogo de OpenRouter simulado; sin esto la prueba saldría a internet."""
    ai_settings.forget_catalog()
    original = ai_settings.fetch_catalog

    async def falso(settings, *, transport=None):
        return await original(
            settings,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=CATALOGO)),
        )

    monkeypatch.setattr(ai_settings, "fetch_catalog", falso)
    yield
    ai_settings.forget_catalog()


async def test_the_catalog_only_lists_models_with_tools(as_agent, team, catalogo):
    admin = await as_agent(team["admin"]["email"])

    respuesta = await admin.get("/api/admin/ai/models")

    assert respuesta.status_code == 200
    modelos = respuesta.json()["models"]
    assert [m["id"] for m in modelos] == ["anthropic/claude-haiku-5.5", "google/gemini-3.8-flash"]
    gemini = modelos[1]
    assert gemini["reasoning"] is True
    assert gemini["prompt_per_million"] == 0.825


async def test_an_admin_chooses_model_and_effort(as_agent, team, catalogo):
    admin = await as_agent(team["admin"]["email"])

    antes = (await admin.get("/api/admin/ai")).json()
    assert antes["model"] == antes["default_model"] == "google/gemini-3.8-flash"
    assert antes["effort"] == "default"
    # La clave nunca viaja a la consola; solo si está puesta.
    assert "sk-" not in json.dumps(antes)

    guardado = await admin.put(
        "/api/admin/ai", json={"model": "anthropic/claude-haiku-5.5", "effort": "low"}
    )
    assert guardado.status_code == 200, guardado.text
    assert guardado.json()["model"] == "anthropic/claude-haiku-5.5"

    despues = (await admin.get("/api/admin/ai")).json()
    assert despues["model"] == "anthropic/claude-haiku-5.5"
    assert despues["effort"] == "low"

    auditoria = await admin.get("/api/audit?action=ai_settings_updated")
    assert auditoria.status_code == 200
    assert auditoria.json()[0]["action"] == "ai_settings_updated"


async def test_a_mistyped_model_is_rejected_before_it_silences_the_assistant(
    as_agent, team, catalogo
):
    admin = await as_agent(team["admin"]["email"])

    for modelo in ("anthropic/claude-haiku-55", "sin/herramientas", "no es un modelo"):
        respuesta = await admin.put("/api/admin/ai", json={"model": modelo, "effort": "low"})
        assert respuesta.status_code == 422, modelo

    raro = await admin.put("/api/admin/ai", json={"model": None, "effort": "extremo"})
    assert raro.status_code == 422


async def test_an_empty_model_goes_back_to_the_env_one(as_agent, team, catalogo):
    admin = await as_agent(team["admin"]["email"])
    await admin.put("/api/admin/ai", json={"model": "anthropic/claude-haiku-5.5", "effort": "low"})

    vuelta = await admin.put("/api/admin/ai", json={"model": None, "effort": "default"})

    assert vuelta.json()["model"] == "google/gemini-3.8-flash"


async def test_only_admins_touch_the_assistant(as_agent, team, catalogo):
    agente = await as_agent(team["ana"]["email"])

    assert (await agente.get("/api/admin/ai")).status_code == 403
    assert (
        await agente.put("/api/admin/ai", json={"model": None, "effort": "low"})
    ).status_code == 403
