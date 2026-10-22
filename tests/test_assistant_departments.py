"""Asistente por departamento: encendido o apagado, y con instrucciones propias."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import httpx
from pydantic import SecretStr

from app.config import get_settings
from app.core import ai_settings
from app.db import repositories as repo
from app.db.engine import session_scope
from app.handlers.ai import AIHandler


def _department(**assistant: Any) -> Any:
    modules = {"assistant": assistant} if assistant else {}
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Hotel",
        enabled_modules=modules,
        business_hours={},
        timezone="America/Asuncion",
        out_of_hours_message=None,
        first_response_target_minutes=None,
    )


def _ctx(department: Any) -> Any:
    return SimpleNamespace(
        text="Hola",
        inbound=SimpleNamespace(action=None, attachments=[]),
        tenant=SimpleNamespace(id="t", slug="default", settings={}),
        conversation=SimpleNamespace(
            channel="whatsapp",
            channel_conversation_id="c",
            department_id=department.id if department else None,
        ),
        contact=SimpleNamespace(display_name="Laura"),
        scratch={},
        replies=[],
        suppress_reply=False,
        session=None,
    )


def _handler(monkeypatch, department: Any) -> AIHandler:
    """Asistente con OpenRouter simulado; responde siempre «respuesta»."""
    def responder(request: httpx.Request) -> httpx.Response:
        cuerpo = (
            'data: {"choices": [{"delta": {"content": "respuesta"}, "finish_reason": "stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=cuerpo.encode())

    settings = get_settings().model_copy(
        update={"openrouter_api_key": SecretStr("sk-or-prueba")}
    )
    handler = AIHandler(settings, transport=httpx.MockTransport(responder))

    async def get_department(session, department_id):
        return department

    async def nada(*args, **kwargs):
        return None

    async def sin_herramientas(ctx):
        return []

    monkeypatch.setattr("app.handlers.ai.repo.get_department", get_department)
    monkeypatch.setattr("app.handlers.ai.repo.record_ai_run", nada)
    monkeypatch.setattr(handler, "_available_tools", sin_herramientas)
    monkeypatch.setattr(handler, "_build_messages", lambda ctx: [
        {"type": "user_input", "content": [{"type": "text", "text": "Hola"}]}
    ])
    return handler


def test_without_configuration_the_assistant_answers_everywhere():
    elegido = ai_settings.for_department(_department())
    assert elegido.enabled is True
    assert elegido.instructions == ""


async def test_a_department_can_switch_the_assistant_off(monkeypatch):
    department = _department(enabled=False, instructions="")
    handler = _handler(monkeypatch, department)
    ctx = _ctx(department)
    ctx.reply = ctx.replies.append
    llamado = []

    async def next_():
        llamado.append(True)

    await handler.handle(ctx, next_)

    # Ni respuesta ni el aviso de reserva: el mensaje espera al equipo.
    assert ctx.replies == []
    assert ctx.suppress_reply is True
    assert llamado == []


async def test_the_assistant_answers_where_it_is_on(monkeypatch):
    department = _department(enabled=True, instructions="")
    handler = _handler(monkeypatch, department)
    ctx = _ctx(department)
    ctx.reply = ctx.replies.append
    ctx.stored_message = SimpleNamespace(id="m")
    ctx.conversation.id = "c"

    async def next_():
        return None

    await handler.handle(ctx, next_)

    assert ctx.replies == ["respuesta"]


def test_the_department_instructions_and_the_date_reach_the_model():
    department = _department(enabled=True, instructions="El check-in es a las 14:00.")
    handler = AIHandler(get_settings())
    ctx = _ctx(department)
    ctx.scratch["department"] = department

    prompt = handler._build_system_prompt(ctx)

    assert "departamento «Hotel»" in prompt
    assert "El check-in es a las 14:00." in prompt
    assert "Fecha y hora actual:" in prompt
    assert "(America/Asuncion)" in prompt
    # Lo general va primero; lo del departamento lo completa después.
    assert prompt.index(get_settings().ai_system_prompt) < prompt.index("check-in")


def test_the_common_queue_gets_no_department_text():
    handler = AIHandler(get_settings())
    ctx = _ctx(None)

    prompt = handler._build_system_prompt(ctx)

    assert "departamento «" not in prompt
    assert "Fecha y hora actual:" in prompt


# --------------------------------------------------------------------------- #
# Consola
# --------------------------------------------------------------------------- #
async def test_an_admin_configures_the_assistant_per_department(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    hotel = (await admin.post("/api/departments", json={"name": "Hotel"})).json()

    antes = (await admin.get("/api/admin/ai/departments")).json()
    fila = next(row for row in antes if row["id"] == hotel["id"])
    assert fila["enabled"] is True
    assert fila["instructions"] == ""

    guardado = await admin.put(
        f"/api/admin/ai/departments/{hotel['id']}",
        json={"enabled": False, "instructions": "  Check-in a las 14:00.  "},
    )
    assert guardado.status_code == 200, guardado.text
    assert guardado.json()["instructions"] == "Check-in a las 14:00."

    async with session_scope() as session:
        department = await repo.get_department(session, uuid.UUID(hotel["id"]))
        elegido = ai_settings.for_department(department)
    assert elegido.enabled is False
    assert elegido.instructions == "Check-in a las 14:00."


async def test_switching_the_assistant_keeps_the_other_modules(as_agent, team):
    """Guardar el asistente no puede apagar el módulo de hotel de la rama."""
    admin = await as_agent(team["admin"]["email"])
    hotel = (await admin.post("/api/departments", json={"name": "Hotel 2"})).json()
    await admin.put(f"/api/departments/{hotel['id']}/hotel/module", json={"enabled": True})

    await admin.put(
        f"/api/admin/ai/departments/{hotel['id']}", json={"enabled": False, "instructions": ""}
    )

    modulo = (await admin.get(f"/api/departments/{hotel['id']}/hotel/module")).json()
    assert modulo["enabled"] is True


async def test_only_admins_configure_the_assistant_per_department(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    hotel = (await admin.post("/api/departments", json={"name": "Hotel 3"})).json()
    marta = await as_agent(team["marta"]["email"])

    assert (await marta.get("/api/admin/ai/departments")).status_code == 403
    assert (
        await marta.put(
            f"/api/admin/ai/departments/{hotel['id']}", json={"enabled": False}
        )
    ).status_code == 403
    desconocido = await admin.put(
        f"/api/admin/ai/departments/{uuid.uuid4()}", json={"enabled": False}
    )
    assert desconocido.status_code == 404
