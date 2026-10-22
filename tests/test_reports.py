"""Informes de supervisión: cifras, alcance y exportación."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.envelope import ChannelKind, ConversationRef
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.reports import percentile


async def _conversation(
    *,
    tenant_id: uuid.UUID,
    channel: ChannelKind = ChannelKind.WEB,
    department_id: uuid.UUID | None = None,
    assignee_id: uuid.UUID | None = None,
    status: str = "open",
    first_response_seconds: int | None = None,
    breached: bool = False,
    created_days_ago: int = 1,
    suffix: str = "",
) -> uuid.UUID:
    """Deja una conversación en la base con las marcas que el informe mide."""
    creada = datetime.now(UTC) - timedelta(days=created_days_ago)
    async with session_scope() as session:
        conversation = await repo.resolve_conversation(
            session,
            tenant_id=tenant_id,
            ref=ConversationRef(
                channel=channel,
                channel_conversation_id=f"{channel}-{suffix or uuid.uuid4().hex[:8]}",
            ),
            contact_id=None,
        )
        conversation.created_at = creada
        conversation.department_id = department_id
        conversation.assignee_id = assignee_id
        conversation.status = status
        if first_response_seconds is not None:
            conversation.first_response_at = creada + timedelta(
                seconds=first_response_seconds
            )
        if breached:
            conversation.sla_breached_at = creada + timedelta(hours=2)
        return conversation.id


# --------------------------------------------------------------------------- #
# Percentiles
# --------------------------------------------------------------------------- #
def test_the_percentile_interpolates_instead_of_picking_the_nearest():
    """Sin interpolar, con cuatro datos el «p90» sería siempre el peor de todos."""
    assert percentile([], 0.5) is None
    assert percentile([7.0], 0.9) == 7.0
    assert percentile([0.0, 10.0], 0.5) == 5.0
    assert percentile([0.0, 100.0, 200.0, 300.0], 0.9) == 270.0


# --------------------------------------------------------------------------- #
# Cifras
# --------------------------------------------------------------------------- #
async def test_the_summary_counts_what_a_month_end_asks(as_agent, team):
    await _conversation(
        tenant_id=team["tenant_id"], status="closed", first_response_seconds=60, suffix="a"
    )
    await _conversation(
        tenant_id=team["tenant_id"], status="closed", first_response_seconds=600, suffix="b"
    )
    await _conversation(tenant_id=team["tenant_id"], breached=True, suffix="c")

    marta = await as_agent(team["marta"]["email"])
    informe = (await marta.get("/api/reports/summary")).json()

    assert informe["summary"]["conversations"] == 3
    assert informe["summary"]["closed"] == 2
    assert informe["summary"]["open"] == 1
    assert informe["summary"]["sla_breached"] == 1
    assert informe["summary"]["first_response_seconds_p50"] == 330.0
    # Solo entra en el porcentaje lo que tuvo objetivo: 2 respondidas y 1
    # incumplida son las tres que se miden aquí.
    assert informe["summary"]["sla_compliance"] == round(1 - 1 / 3, 4)


async def test_the_timeseries_has_no_holes(as_agent, team):
    """Una gráfica que salta del lunes al jueves miente sobre la curva."""
    await _conversation(tenant_id=team["tenant_id"], created_days_ago=5, suffix="x")

    marta = await as_agent(team["marta"]["email"])
    informe = (await marta.get("/api/reports/summary?since=2020-01-01&until=2020-01-10")).json()

    serie = informe["timeseries"]
    assert len(serie) == 10
    assert [fila["day"] for fila in serie] == [
        f"2020-01-{dia:02d}" for dia in range(1, 11)
    ]
    assert all(fila["conversations"] == 0 for fila in serie)


async def test_the_breakdown_by_channel_orders_by_volume(as_agent, team):
    for indice in range(3):
        await _conversation(
            tenant_id=team["tenant_id"], channel=ChannelKind.WHATSAPP, suffix=f"w{indice}"
        )
    await _conversation(tenant_id=team["tenant_id"], channel=ChannelKind.WEB, suffix="web")

    marta = await as_agent(team["marta"]["email"])
    canales = (await marta.get("/api/reports/summary")).json()["channels"]

    assert canales[0] == {"channel": "whatsapp", "conversations": 3}
    assert canales[1] == {"channel": "web", "conversations": 1}


async def test_the_load_per_agent_is_measured(as_agent, team):
    ana_id = uuid.UUID(team["ana"]["id"])
    await _conversation(
        tenant_id=team["tenant_id"],
        assignee_id=ana_id,
        status="closed",
        first_response_seconds=120,
        suffix="ana1",
    )
    await _conversation(tenant_id=team["tenant_id"], assignee_id=ana_id, suffix="ana2")

    marta = await as_agent(team["marta"]["email"])
    agentes = (await marta.get("/api/reports/summary")).json()["agents"]

    assert len(agentes) == 1
    assert agentes[0]["agent"] == team["ana"]["name"]
    assert agentes[0]["conversations"] == 2
    assert agentes[0]["closed"] == 1
    assert agentes[0]["first_response_seconds_p50"] == 120.0


async def test_conversations_without_a_department_are_named_not_omitted(as_agent, team):
    await _conversation(tenant_id=team["tenant_id"], suffix="cola")
    marta = await as_agent(team["marta"]["email"])
    departamentos = (await marta.get("/api/reports/summary")).json()["departments"]
    assert departamentos == [{"department": "Sin departamento", "conversations": 1}]


# --------------------------------------------------------------------------- #
# Alcance
# --------------------------------------------------------------------------- #
async def test_an_agent_cannot_read_the_reports(as_agent, team):
    ana = await as_agent(team["ana"]["email"])
    assert (await ana.get("/api/reports/summary")).status_code == 403


async def test_a_restricted_supervisor_only_measures_their_branches(as_agent, team):
    """Medir el rendimiento de otra rama no le corresponde."""
    admin = await as_agent(team["admin"]["email"])
    suya = await admin.post("/api/departments", json={"name": "Soporte"})
    ajena = await admin.post("/api/departments", json={"name": "Hotel"})
    suya_id = uuid.UUID(suya.json()["id"])
    ajena_id = uuid.UUID(ajena.json()["id"])

    await _conversation(tenant_id=team["tenant_id"], department_id=suya_id, suffix="s1")
    await _conversation(tenant_id=team["tenant_id"], department_id=ajena_id, suffix="h1")
    await _conversation(tenant_id=team["tenant_id"], department_id=ajena_id, suffix="h2")

    otorgar = await admin.put(
        f"/api/agents/{team['marta']['id']}/departments",
        json={"department_id": str(suya_id), "extra_department_ids": []},
    )
    assert otorgar.status_code == 200, otorgar.text

    marta = await as_agent(team["marta"]["email"])
    informe = (await marta.get("/api/reports/summary")).json()
    assert informe["summary"]["conversations"] == 1

    # Y pedir la rama ajena de forma explícita no la abre.
    negado = await marta.get(f"/api/reports/summary?department_id={ajena_id}")
    assert negado.status_code == 404

    # Administración sí ve las tres.
    completo = (await admin.get("/api/reports/summary")).json()
    assert completo["summary"]["conversations"] == 3


async def test_an_impossible_range_is_refused(as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    invertido = await marta.get("/api/reports/summary?since=2026-05-01&until=2026-04-01")
    assert invertido.status_code == 422

    enorme = await marta.get("/api/reports/summary?since=2000-01-01&until=2026-01-01")
    assert enorme.status_code == 422


# --------------------------------------------------------------------------- #
# Exportación
# --------------------------------------------------------------------------- #
async def test_a_breakdown_can_be_exported_as_csv(as_agent, team):
    await _conversation(tenant_id=team["tenant_id"], channel=ChannelKind.WHATSAPP, suffix="e1")
    marta = await as_agent(team["marta"]["email"])

    respuesta = await marta.get("/api/reports/export.csv?dataset=channels")
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/csv")
    assert "attachment" in respuesta.headers["content-disposition"]
    # Con marca de orden de bytes, o Excel destroza los nombres con tilde.
    assert respuesta.content.startswith(b"\xef\xbb\xbf")
    assert "channel,conversations" in respuesta.text
    assert "whatsapp,1" in respuesta.text


async def test_an_unknown_dataset_is_refused(as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    assert (
        await marta.get("/api/reports/export.csv?dataset=lo-que-sea")
    ).status_code == 422
