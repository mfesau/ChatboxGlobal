"""Encuesta de satisfacción: envío, recogida de la nota y página pública."""

from __future__ import annotations

import uuid

from app.core import csat
from app.core.envelope import ChannelKind, ConversationRef
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import CsatSurvey, OutboxItem


async def _department_with_csat(admin, *, enabled: bool = True, question: str | None = None):
    creado = await admin.post("/api/departments", json={"name": f"Soporte {uuid.uuid4().hex[:6]}"})
    assert creado.status_code == 201, creado.text
    department_id = creado.json()["id"]
    if enabled or question is not None:
        respuesta = await admin.put(
            f"/api/departments/{department_id}/csat",
            json={"enabled": enabled, "question": question},
        )
        assert respuesta.status_code == 200, respuesta.text
    return uuid.UUID(department_id)


async def _closable_conversation(
    tenant_id: uuid.UUID,
    *,
    department_id: uuid.UUID | None,
    channel: ChannelKind = ChannelKind.WEB,
) -> uuid.UUID:
    async with session_scope() as session:
        conversation = await repo.resolve_conversation(
            session,
            tenant_id=tenant_id,
            ref=ConversationRef(
                channel=channel, channel_conversation_id=f"csat-{uuid.uuid4().hex[:8]}"
            ),
            contact_id=None,
        )
        conversation.department_id = department_id
        return conversation.id


async def _surveys(tenant_id: uuid.UUID) -> list[CsatSurvey]:
    from sqlalchemy import select

    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(CsatSurvey).where(CsatSurvey.tenant_id == tenant_id)
                )
            ).scalars()
        )


# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #
async def test_the_survey_is_off_until_a_branch_turns_it_on(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    creado = await admin.post("/api/departments", json={"name": "Sin encuesta"})
    estado = await admin.get(f"/api/departments/{creado.json()['id']}/csat")

    assert estado.status_code == 200
    assert estado.json()["enabled"] is False
    assert estado.json()["question"] == csat.DEFAULT_QUESTION


async def test_a_branch_can_write_its_own_question(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin, question="¿Le atendimos bien? 1 a 5")

    estado = await admin.get(f"/api/departments/{department_id}/csat")
    assert estado.json() == {"enabled": True, "question": "¿Le atendimos bien? 1 a 5"}


async def test_only_administration_configures_the_survey(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)

    ana = await as_agent(team["ana"]["email"])
    negado = await ana.put(
        f"/api/departments/{department_id}/csat", json={"enabled": False}
    )
    assert negado.status_code == 403


# --------------------------------------------------------------------------- #
# Envío al cerrar
# --------------------------------------------------------------------------- #
async def test_closing_a_conversation_asks_for_a_score(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id
    )

    cerrada = await admin.post(f"/api/conversations/{conversation_id}/close")
    assert cerrada.status_code == 200

    encuestas = await _surveys(team["tenant_id"])
    assert len(encuestas) == 1
    assert encuestas[0].conversation_id == conversation_id
    assert encuestas[0].score is None
    assert encuestas[0].token


async def test_marking_it_solved_asks_too(as_agent, team):
    """Los dos caminos que cierran deben medir igual."""
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id
    )

    marcada = await admin.post(
        f"/api/conversations/{conversation_id}/state", json={"state": "solved"}
    )
    assert marcada.status_code == 200
    assert len(await _surveys(team["tenant_id"])) == 1


async def test_a_branch_without_the_survey_asks_nothing(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin, enabled=False)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id
    )

    await admin.post(f"/api/conversations/{conversation_id}/close")
    assert await _surveys(team["tenant_id"]) == []


async def test_a_conversation_in_the_common_queue_is_not_surveyed(as_agent, team):
    """Sin departamento no hay rama que haya pedido medirse."""
    admin = await as_agent(team["admin"]["email"])
    conversation_id = await _closable_conversation(team["tenant_id"], department_id=None)

    await admin.post(f"/api/conversations/{conversation_id}/close")
    assert await _surveys(team["tenant_id"]) == []


async def test_reopening_and_closing_again_does_not_ask_twice(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id
    )

    await admin.post(f"/api/conversations/{conversation_id}/close")
    await admin.post(f"/api/conversations/{conversation_id}/state", json={"state": "pending"})
    await admin.post(f"/api/conversations/{conversation_id}/close")

    assert len(await _surveys(team["tenant_id"])) == 1


async def test_the_question_leaves_by_the_same_channel(as_agent, team):
    """Se encola como cualquier saliente, no por una vía aparte."""
    from sqlalchemy import select

    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin, question="¿Qué tal?")
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id
    )
    await admin.post(f"/api/conversations/{conversation_id}/close")

    async with session_scope() as session:
        salientes = list(
            (
                await session.execute(
                    select(OutboxItem).where(OutboxItem.conversation_id == conversation_id)
                )
            ).scalars()
        )
    assert len(salientes) == 1
    mensaje = salientes[0].payload["message"]
    assert "¿Qué tal?" in mensaje["text"]
    # En el chatbox web hay botones: la nota se pulsa.
    assert [opcion["title"] for opcion in mensaje["quick_replies"]] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]


# --------------------------------------------------------------------------- #
# Forma de la pregunta por canal
# --------------------------------------------------------------------------- #
def test_a_channel_without_buttons_gets_a_link():
    from types import SimpleNamespace

    conversation = SimpleNamespace(channel=ChannelKind.EMAIL)
    survey = SimpleNamespace(token="un-testigo")
    mensaje = csat.build_message(
        conversation=conversation,
        survey=survey,
        department=None,
        public_base_url="https://chat.empresa.local/",
    )
    assert mensaje.quick_replies == []
    assert "https://chat.empresa.local/csat/un-testigo" in mensaje.text


def test_a_channel_with_buttons_gets_five_of_them():
    from types import SimpleNamespace

    mensaje = csat.build_message(
        conversation=SimpleNamespace(channel=ChannelKind.TELEGRAM),
        survey=SimpleNamespace(token="x"),
        department=None,
        public_base_url="https://chat.empresa.local",
    )
    assert [opcion["id"] for opcion in mensaje.quick_replies] == [
        f"csat:{n}" for n in range(1, 6)
    ]
    assert "csat/" not in (mensaje.text or "")


# --------------------------------------------------------------------------- #
# Lectura de la nota
# --------------------------------------------------------------------------- #
def test_the_score_is_read_from_the_button_or_from_a_bare_number():
    assert csat.read_score(None, {"id": "csat:4"}) == 4
    assert csat.read_score("5", None) == 5
    assert csat.read_score("3.", None) == 3


def test_an_ambiguous_answer_is_not_scored():
    """Adivinar la nota de «entre 3 y 4 diría» falsearía la medida."""
    assert csat.read_score("entre 3 y 4 diría", None) is None
    assert csat.read_score("gracias!", None) is None
    assert csat.read_score("9", None) is None
    assert csat.read_score("0", None) is None
    assert csat.read_score(None, None) is None


# --------------------------------------------------------------------------- #
# Página pública
# --------------------------------------------------------------------------- #
async def test_the_public_page_accepts_a_score(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id, channel=ChannelKind.EMAIL
    )
    await admin.post(f"/api/conversations/{conversation_id}/close")

    [encuesta] = await _surveys(team["tenant_id"])
    token = encuesta.token

    pagina = await anonymous.get(f"/csat/{token}")
    assert pagina.status_code == 200
    assert "¿Cómo valora la atención recibida?" in pagina.text

    enviada = await anonymous.post(
        f"/csat/{token}", data={"score": "5", "comment": "Muy rápidos"}
    )
    assert enviada.status_code == 200

    [encuesta] = await _surveys(team["tenant_id"])
    assert encuesta.score == 5
    assert encuesta.comment == "Muy rápidos"
    assert encuesta.responded_at is not None


async def test_answering_twice_does_not_overwrite_the_first(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id, channel=ChannelKind.EMAIL
    )
    await admin.post(f"/api/conversations/{conversation_id}/close")
    [encuesta] = await _surveys(team["tenant_id"])

    await anonymous.post(f"/csat/{encuesta.token}", data={"score": "5"})
    await anonymous.post(f"/csat/{encuesta.token}", data={"score": "1"})

    [encuesta] = await _surveys(team["tenant_id"])
    assert encuesta.score == 5


async def test_an_unknown_token_reveals_nothing(anonymous):
    respuesta = await anonymous.get("/csat/no-existe-este-testigo")
    assert respuesta.status_code == 404


async def test_a_score_outside_the_range_is_refused(anonymous, as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id, channel=ChannelKind.EMAIL
    )
    await admin.post(f"/api/conversations/{conversation_id}/close")
    [encuesta] = await _surveys(team["tenant_id"])

    assert (
        await anonymous.post(f"/csat/{encuesta.token}", data={"score": "9"})
    ).status_code == 422


# --------------------------------------------------------------------------- #
# En el informe
# --------------------------------------------------------------------------- #
async def test_the_report_shows_the_average_and_the_spread(anonymous, as_agent, team):
    """La media sola engaña: un 3 puede ser tibieza o guerra civil."""
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)

    # Dos conversaciones cerradas: dos encuestas por correo, con su enlace.
    for _ in range(2):
        conversation_id = await _closable_conversation(
            team["tenant_id"], department_id=department_id, channel=ChannelKind.EMAIL
        )
        await admin.post(f"/api/conversations/{conversation_id}/close")

    for encuesta, nota in zip(await _surveys(team["tenant_id"]), ("1", "5"), strict=True):
        await anonymous.post(f"/csat/{encuesta.token}", data={"score": nota})

    informe = (await admin.get("/api/reports/summary")).json()["csat"]
    assert informe["sent"] == 2
    assert informe["answered"] == 2
    assert informe["average"] == 3.0
    assert informe["distribution"]["1"] == 1
    assert informe["distribution"]["5"] == 1


# --------------------------------------------------------------------------- #
# Recogida por el propio hilo
# --------------------------------------------------------------------------- #
async def _turn(conversation_id: uuid.UUID, *, text: str | None, action=None):
    """Hace pasar un entrante por el CsatHandler y devuelve el contexto."""
    from app.config import get_settings
    from app.core.envelope import ContentType, InboundMessage, Party
    from app.core.pipeline import Pipeline, TurnContext
    from app.db.models import Conversation, Message
    from app.handlers.builtin import CsatHandler

    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        tenant = await repo.get_or_create_tenant(session, "default")
        inbound = InboundMessage(
            channel=conversation.channel,
            conversation=ConversationRef(
                channel=conversation.channel,
                channel_conversation_id=conversation.channel_conversation_id,
            ),
            sender=Party(channel_user_id="cliente"),
            provider_message_id=f"turno-{uuid.uuid4().hex[:8]}",
            content_type=ContentType.INTERACTIVE if action else ContentType.TEXT,
            text=text,
            action=action,
        )
        stored = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            channel=conversation.channel,
            direction="inbound",
            text=text,
            provider_message_id=inbound.provider_message_id,
        )
        session.add(stored)
        await session.flush()

        ctx = TurnContext(
            inbound=inbound,
            session=session,
            settings=get_settings(),
            tenant=tenant,
            conversation=conversation,
            contact=None,
            stored_message=stored,
        )
        await Pipeline([CsatHandler()]).run(ctx)
        return ctx


async def test_a_pressed_button_records_the_score(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id
    )
    await admin.post(f"/api/conversations/{conversation_id}/close")

    ctx = await _turn(conversation_id, text=None, action={"id": "csat:4"})
    assert [reply.text for reply in ctx.replies] == ["Gracias por su valoración."]

    [encuesta] = await _surveys(team["tenant_id"])
    assert encuesta.score == 4
    assert encuesta.responded_at is not None


async def test_a_written_number_also_counts(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id
    )
    await admin.post(f"/api/conversations/{conversation_id}/close")

    await _turn(conversation_id, text="2")
    [encuesta] = await _surveys(team["tenant_id"])
    assert encuesta.score == 2


async def test_something_that_is_not_a_score_lets_the_turn_through(as_agent, team):
    """Insistir con la pregunta convertiría la encuesta en un obstáculo."""
    admin = await as_agent(team["admin"]["email"])
    department_id = await _department_with_csat(admin)
    conversation_id = await _closable_conversation(
        team["tenant_id"], department_id=department_id
    )
    await admin.post(f"/api/conversations/{conversation_id}/close")

    ctx = await _turn(conversation_id, text="Perdón, tengo otra consulta")
    # El handler no responde ni bloquea: el turno sigue su camino normal.
    assert ctx.replies == []

    [encuesta] = await _surveys(team["tenant_id"])
    assert encuesta.score is None


async def test_without_a_pending_survey_the_handler_does_nothing(as_agent, team):
    """Un «4» suelto en una conversación corriente no es una nota."""
    conversation_id = await _closable_conversation(team["tenant_id"], department_id=None)
    ctx = await _turn(conversation_id, text="4")
    assert ctx.replies == []
    assert await _surveys(team["tenant_id"]) == []
