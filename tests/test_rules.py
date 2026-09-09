"""Reglas de automatización y mensajes programados."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import rules
from app.core.envelope import ChannelKind, ContentType, ConversationRef, InboundMessage, Party
from app.core.pipeline import Pipeline, TurnContext
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import Conversation, Message, OutboxItem, ScheduledMessage
from app.handlers.rules import RulesHandler


async def _conversation(
    tenant_id: uuid.UUID,
    *,
    channel: ChannelKind = ChannelKind.WEB,
    department_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with session_scope() as session:
        conversation = await repo.resolve_conversation(
            session,
            tenant_id=tenant_id,
            ref=ConversationRef(
                channel=channel, channel_conversation_id=f"regla-{uuid.uuid4().hex[:8]}"
            ),
            contact_id=None,
        )
        conversation.department_id = department_id
        return conversation.id


async def _labels(conversation_id: uuid.UUID) -> list[str]:
    """Etiquetas puestas en la conversación, por nombre."""
    from sqlalchemy.orm import selectinload

    async with session_scope() as session:
        conversation = (
            await session.execute(
                select(Conversation)
                .where(Conversation.id == conversation_id)
                .options(selectinload(Conversation.labels))
            )
        ).scalar_one()
        return sorted(label.name for label in conversation.labels)


async def _turn(conversation_id: uuid.UUID, *, text: str, previous: int = 0) -> TurnContext:
    """Hace pasar un entrante por el RulesHandler y devuelve el contexto."""
    from app.config import get_settings

    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        tenant = await repo.get_or_create_tenant(session, "default")

        historial = []
        for indice in range(previous + 1):
            stored = Message(
                tenant_id=tenant.id,
                conversation_id=conversation.id,
                channel=conversation.channel,
                direction="inbound",
                text=text if indice == previous else "anterior",
                provider_message_id=f"turno-{uuid.uuid4().hex[:8]}",
            )
            session.add(stored)
            historial.append(stored)
        await session.flush()

        inbound = InboundMessage(
            channel=conversation.channel,
            conversation=ConversationRef(
                channel=conversation.channel,
                channel_conversation_id=conversation.channel_conversation_id,
            ),
            sender=Party(channel_user_id="cliente"),
            provider_message_id=historial[-1].provider_message_id,
            content_type=ContentType.TEXT,
            text=text,
        )
        ctx = TurnContext(
            inbound=inbound,
            session=session,
            settings=get_settings(),
            tenant=tenant,
            conversation=conversation,
            contact=None,
            stored_message=historial[-1],
            history=historial,
        )
        await Pipeline([RulesHandler()]).run(ctx)
        return ctx


# --------------------------------------------------------------------------- #
# Validación
# --------------------------------------------------------------------------- #
def test_a_rule_that_does_nothing_is_refused():
    with pytest.raises(rules.RuleError, match="al menos una acción"):
        rules.validate({}, [])


def test_an_unknown_action_is_refused():
    with pytest.raises(rules.RuleError, match="Acción desconocida"):
        rules.validate({}, [{"action": "lanzar_cohete"}])


def test_an_action_without_its_target_is_refused():
    with pytest.raises(rules.RuleError, match="etiqueta"):
        rules.validate({}, [{"action": "add_label"}])
    with pytest.raises(rules.RuleError, match="departamento"):
        rules.validate({}, [{"action": "assign_department"}])
    with pytest.raises(rules.RuleError, match="texto"):
        rules.validate({}, [{"action": "reply", "body": "  "}])


def test_a_broken_regular_expression_is_refused():
    with pytest.raises(rules.RuleError, match="no válida"):
        rules.validate({"regex": "(sin cerrar"}, [{"action": "stop"}])


def test_a_very_long_regular_expression_is_refused():
    """La evalúa el turno de quien escribe; una retorcida lo hace esperar."""
    with pytest.raises(rules.RuleError, match="caracteres"):
        rules.validate({"regex": "a" * 300}, [{"action": "stop"}])


def test_an_unknown_channel_is_refused():
    with pytest.raises(rules.RuleError, match="Canal desconocido"):
        rules.validate({"channels": ["paloma"]}, [{"action": "stop"}])


# --------------------------------------------------------------------------- #
# Coincidencia
# --------------------------------------------------------------------------- #
def _rule(**conditions):
    from types import SimpleNamespace

    return SimpleNamespace(conditions=conditions, name="prueba")


def test_all_the_conditions_must_hold():
    """Es una «y», no una «o»: una regla se lee entera de un vistazo."""
    regla = _rule(keywords=["factura"], channels=["whatsapp"])
    assert rules.matches(
        regla, text="quiero mi factura", channel=ChannelKind.WHATSAPP, is_first=True
    )
    assert not rules.matches(
        regla, text="quiero mi factura", channel=ChannelKind.WEB, is_first=True
    )
    assert not rules.matches(
        regla, text="buenos días", channel=ChannelKind.WHATSAPP, is_first=True
    )


def test_keywords_ignore_case():
    regla = _rule(keywords=["Factura"])
    assert rules.matches(regla, text="LA FACTURA", channel=ChannelKind.WEB, is_first=True)


def test_a_rule_without_conditions_always_matches():
    """Es lo que hace posible una respuesta de bienvenida."""
    assert rules.matches(_rule(), text="hola", channel=ChannelKind.WEB, is_first=True)


def test_the_first_message_condition():
    regla = _rule(only_first_message=True)
    assert rules.matches(regla, text="hola", channel=ChannelKind.WEB, is_first=True)
    assert not rules.matches(regla, text="hola", channel=ChannelKind.WEB, is_first=False)


# --------------------------------------------------------------------------- #
# Aplicación
# --------------------------------------------------------------------------- #
async def test_a_rule_labels_and_replies(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    etiqueta = await admin.post("/api/labels", json={"name": "Facturación", "color": "#ff0000"})
    assert etiqueta.status_code == 201, etiqueta.text

    creada = await admin.post(
        "/api/rules",
        json={
            "name": "Facturación",
            "conditions": {"keywords": ["factura"]},
            "actions": [
                {"action": "add_label", "label_id": etiqueta.json()["id"]},
                {"action": "reply", "body": "Le paso con facturación."},
            ],
        },
    )
    assert creada.status_code == 201, creada.text

    conversation_id = await _conversation(team["tenant_id"])
    ctx = await _turn(conversation_id, text="Necesito la factura de agosto")

    assert [reply.text for reply in ctx.replies] == ["Le paso con facturación."]
    assert ctx.scratch["rules_applied"] == ["Facturación"]

    assert await _labels(conversation_id) == ["Facturación"]


async def test_a_rule_routes_to_a_department(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    destino = await admin.post("/api/departments", json={"name": "Facturación"})
    department_id = destino.json()["id"]

    await admin.post(
        "/api/rules",
        json={
            "name": "A facturación",
            "conditions": {"keywords": ["factura"]},
            "actions": [{"action": "assign_department", "department_id": department_id}],
        },
    )

    conversation_id = await _conversation(team["tenant_id"])
    await _turn(conversation_id, text="dudas con la factura")

    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert str(conversation.department_id) == department_id

    # Y queda su apunte en el historial, igual que una derivación a mano.
    derivaciones = (
        await admin.get(f"/api/conversations/{conversation_id}/assignments")
    ).json()
    assert any("Regla" in (row.get("note") or "") for row in derivaciones)


async def test_stop_silences_the_assistant(as_agent, team):
    """Sin esto, el modelo contestaría encima de lo que dijo la regla."""
    admin = await as_agent(team["admin"]["email"])
    await admin.post(
        "/api/rules",
        json={
            "name": "Solo personas",
            "conditions": {"keywords": ["reclamación"]},
            "actions": [
                {"action": "reply", "body": "Un compañero le atiende enseguida."},
                {"action": "stop"},
            ],
        },
    )

    conversation_id = await _conversation(team["tenant_id"])

    class Marcador:
        name = "marcador"
        llamado = False

        async def handle(self, ctx, next_):
            Marcador.llamado = True
            await next_()

    from app.config import get_settings

    async with session_scope() as session:
        conversation = await session.get(Conversation, conversation_id)
        tenant = await repo.get_or_create_tenant(session, "default")
        stored = Message(
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            channel=conversation.channel,
            direction="inbound",
            text="tengo una reclamación",
            provider_message_id=f"t-{uuid.uuid4().hex[:8]}",
        )
        session.add(stored)
        await session.flush()
        ctx = TurnContext(
            inbound=InboundMessage(
                channel=conversation.channel,
                conversation=ConversationRef(
                    channel=conversation.channel,
                    channel_conversation_id=conversation.channel_conversation_id,
                ),
                sender=Party(channel_user_id="cliente"),
                provider_message_id=stored.provider_message_id,
                text="tengo una reclamación",
            ),
            session=session,
            settings=get_settings(),
            tenant=tenant,
            conversation=conversation,
            contact=None,
            stored_message=stored,
            history=[stored],
        )
        await Pipeline([RulesHandler(), Marcador()]).run(ctx)

    assert [reply.text for reply in ctx.replies] == ["Un compañero le atiende enseguida."]
    assert Marcador.llamado is False


async def test_rules_run_in_priority_order(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    for nombre, prioridad in (("Segunda", 200), ("Primera", 10)):
        await admin.post(
            "/api/rules",
            json={
                "name": nombre,
                "priority": prioridad,
                "actions": [{"action": "reply", "body": nombre}],
            },
        )

    conversation_id = await _conversation(team["tenant_id"])
    ctx = await _turn(conversation_id, text="hola")
    assert ctx.scratch["rules_applied"] == ["Primera", "Segunda"]


async def test_a_rule_of_another_branch_does_not_apply(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    hotel = await admin.post("/api/departments", json={"name": "Hotel"})
    soporte = await admin.post("/api/departments", json={"name": "Soporte"})

    await admin.post(
        "/api/rules",
        json={
            "name": "Solo del hotel",
            "department_id": hotel.json()["id"],
            "actions": [{"action": "reply", "body": "del hotel"}],
        },
    )

    conversation_id = await _conversation(
        team["tenant_id"], department_id=uuid.UUID(soporte.json()["id"])
    )
    ctx = await _turn(conversation_id, text="hola")
    assert ctx.replies == []


async def test_an_inactive_rule_does_nothing(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    creada = await admin.post(
        "/api/rules",
        json={"name": "Apagada", "actions": [{"action": "reply", "body": "no debería salir"}]},
    )
    await admin.patch(f"/api/rules/{creada.json()['id']}", json={"is_active": False})

    conversation_id = await _conversation(team["tenant_id"])
    ctx = await _turn(conversation_id, text="hola")
    assert ctx.replies == []


async def test_the_same_label_is_not_added_twice(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    etiqueta = await admin.post("/api/labels", json={"name": "Urgente", "color": "#ff0000"})
    await admin.post(
        "/api/rules",
        json={
            "name": "Urgente",
            "actions": [{"action": "add_label", "label_id": etiqueta.json()["id"]}],
        },
    )

    conversation_id = await _conversation(team["tenant_id"])
    await _turn(conversation_id, text="hola")
    await _turn(conversation_id, text="hola otra vez", previous=1)

    assert await _labels(conversation_id) == ["Urgente"]


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
async def test_only_administration_writes_rules(as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    ana = await as_agent(team["ana"]["email"])

    cuerpo = {"name": "X", "actions": [{"action": "stop"}]}
    assert (await marta.post("/api/rules", json=cuerpo)).status_code == 403

    # Supervisión sí las lee: necesita saber por qué se etiquetó sola una
    # conversación.
    assert (await marta.get("/api/rules")).status_code == 200
    assert (await ana.get("/api/rules")).status_code == 403


async def test_a_rule_can_be_deleted(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    creada = await admin.post(
        "/api/rules", json={"name": "Temporal", "actions": [{"action": "stop"}]}
    )
    rule_id = creada.json()["id"]

    assert (await admin.delete(f"/api/rules/{rule_id}")).status_code == 204
    assert (await admin.get("/api/rules")).json() == []
    assert (await admin.delete(f"/api/rules/{rule_id}")).status_code == 404


async def test_editing_only_the_actions_still_validates_the_whole_rule(as_agent, team):
    admin = await as_agent(team["admin"]["email"])
    creada = await admin.post(
        "/api/rules", json={"name": "Editable", "actions": [{"action": "stop"}]}
    )
    roto = await admin.patch(
        f"/api/rules/{creada.json()['id']}", json={"actions": [{"action": "add_label"}]}
    )
    assert roto.status_code == 422


# --------------------------------------------------------------------------- #
# Mensajes programados
# --------------------------------------------------------------------------- #
async def test_a_message_can_wait_for_its_hour(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    conversation_id = await _conversation(team["tenant_id"])
    cuando = datetime.now(UTC) + timedelta(hours=2)

    creado = await ana.post(
        f"/api/conversations/{conversation_id}/scheduled",
        json={"text": "¿Pudo resolverlo?", "send_at": cuando.isoformat()},
    )
    assert creado.status_code == 201, creado.text
    assert creado.json()["status"] == "pending"

    listado = await ana.get(f"/api/conversations/{conversation_id}/scheduled")
    assert [row["text"] for row in listado.json()] == ["¿Pudo resolverlo?"]


async def test_an_hour_in_the_past_is_refused(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    conversation_id = await _conversation(team["tenant_id"])
    ayer = datetime.now(UTC) - timedelta(days=1)

    respuesta = await ana.post(
        f"/api/conversations/{conversation_id}/scheduled",
        json={"text": "tarde", "send_at": ayer.isoformat()},
    )
    assert respuesta.status_code == 422


async def test_scheduling_too_far_ahead_is_refused(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    conversation_id = await _conversation(team["tenant_id"])
    dentro_de_un_año = datetime.now(UTC) + timedelta(days=400)

    respuesta = await ana.post(
        f"/api/conversations/{conversation_id}/scheduled",
        json={"text": "muy lejos", "send_at": dentro_de_un_año.isoformat()},
    )
    assert respuesta.status_code == 422


async def test_a_reply_from_the_customer_cancels_what_was_pending(as_agent, team):
    """Un «¿sigue ahí?» de ayer no debe salir justo después de que conteste."""
    ana = await as_agent(team["ana"]["email"])
    conversation_id = await _conversation(team["tenant_id"])
    await ana.post(
        f"/api/conversations/{conversation_id}/scheduled",
        json={
            "text": "¿Sigue ahí?",
            "send_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )

    async with session_scope() as session:
        cancelados = await repo.cancel_scheduled_messages(
            session, conversation_id=conversation_id
        )
    assert cancelados == 1

    listado = (await ana.get(f"/api/conversations/{conversation_id}/scheduled")).json()
    assert listado[0]["status"] == "cancelled"


async def test_a_due_message_reaches_the_outbox(as_agent, team):
    from sqlalchemy import select

    ana = await as_agent(team["ana"]["email"])
    conversation_id = await _conversation(team["tenant_id"])
    await ana.post(
        f"/api/conversations/{conversation_id}/scheduled",
        json={
            "text": "Seguimiento",
            "send_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
    )

    # Se adelanta el reloj de la fila en lugar de esperar cinco minutos.
    async with session_scope() as session:
        [item] = list(
            (
                await session.execute(
                    select(ScheduledMessage).where(
                        ScheduledMessage.conversation_id == conversation_id
                    )
                )
            ).scalars()
        )
        item.send_at = datetime.now(UTC) - timedelta(minutes=1)

    async with session_scope() as session:
        vencidos = await repo.due_scheduled_messages(session)
        assert len(vencidos) == 1
        conversation = await repo.get_conversation(session, conversation_id)
        await repo.enqueue_scheduled(session, item=vencidos[0], conversation=conversation)

    async with session_scope() as session:
        salientes = list(
            (
                await session.execute(
                    select(OutboxItem).where(OutboxItem.conversation_id == conversation_id)
                )
            ).scalars()
        )
        assert len(salientes) == 1
        assert salientes[0].payload["message"]["text"] == "Seguimiento"

    listado = (await ana.get(f"/api/conversations/{conversation_id}/scheduled")).json()
    assert listado[0]["status"] == "sent"


async def test_a_scheduled_message_can_be_cancelled_by_hand(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    conversation_id = await _conversation(team["tenant_id"])
    creado = await ana.post(
        f"/api/conversations/{conversation_id}/scheduled",
        json={
            "text": "Ya no hace falta",
            "send_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    scheduled_id = creado.json()["id"]

    assert (await ana.delete(f"/api/scheduled/{scheduled_id}")).status_code == 204
    # Cancelar dos veces avisa en lugar de callar.
    assert (await ana.delete(f"/api/scheduled/{scheduled_id}")).status_code == 409
