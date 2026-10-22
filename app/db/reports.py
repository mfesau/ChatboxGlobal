"""Agregados para los informes de supervisión.

Aparte de ``repositories.py`` a propósito: allí viven las consultas que sirven
al modelo de dominio —una conversación, su cartera, sus mensajes—, y aquí las
que recorren varias tablas para contar. Son dos oficios distintos y el fichero
de repositorios ya es largo.

Las medianas y percentiles se calculan en Python y no en SQL. ``percentile_cont``
existe en PostgreSQL pero no en SQLite, donde corren las pruebas, y el volumen
de un informe —las conversaciones de un mes de un equipo— cabe holgadamente en
memoria. Escribirlo dos veces, una por dialecto, no lo compensa.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AIRun, Conversation, Department, Message

#: Estado en base de una conversación terminada. El equipo la marca como
#: «resuelta» y ``WORK_STATES`` (app/api/console.py) lo traduce a ``closed``,
#: que es lo que hay en la columna.
CLOSED_STATUSES = ("closed",)


@dataclass(slots=True)
class Window:
    """Intervalo y alcance del informe, con los dos extremos incluidos.

    Se distinguen dos cosas que se parecen. ``scope_department_ids`` es lo que
    quien pregunta **puede** medir: ``None`` sin acotar —administración—, o el
    conjunto otorgado a una supervisión. ``only_department_id`` es el filtro
    que esa persona **elige** aplicar dentro de lo que puede ver.

    El alcance incluye siempre las conversaciones sin departamento, que son las
    que siguen en la cola común. Es la misma regla de ``agent_can_access`` en
    ``repositories.py``: una supervisión ve su rama y lo que aún no se derivó a
    ninguna, y si el informe no las contase, la suma de las ramas no cuadraría
    con la bandeja que esa misma persona tiene delante.
    """

    since: datetime
    until: datetime
    scope_department_ids: set[uuid.UUID] | None = None
    only_department_id: uuid.UUID | None = None


def _scope(stmt: Select, tenant_id: uuid.UUID, window: Window) -> Select:
    stmt = stmt.where(
        Conversation.tenant_id == tenant_id,
        Conversation.created_at >= window.since,
        Conversation.created_at <= window.until,
    )
    if window.scope_department_ids is not None:
        stmt = stmt.where(
            or_(
                Conversation.department_id.is_(None),
                Conversation.department_id.in_(window.scope_department_ids),
            )
        )
    if window.only_department_id is not None:
        stmt = stmt.where(Conversation.department_id == window.only_department_id)
    return stmt


def percentile(values: list[float], fraction: float) -> float | None:
    """Percentil por interpolación lineal. ``None`` si no hay datos.

    Interpolar y no elegir el elemento más cercano evita que una muestra
    pequeña dé saltos: con cuatro conversaciones, el «p90» sin interpolar sería
    siempre la más lenta de todas.
    """
    if not values:
        return None
    ordenados = sorted(values)
    if len(ordenados) == 1:
        return ordenados[0]
    posicion = fraction * (len(ordenados) - 1)
    inferior = int(posicion)
    superior = min(inferior + 1, len(ordenados) - 1)
    peso = posicion - inferior
    return ordenados[inferior] * (1 - peso) + ordenados[superior] * peso


# --------------------------------------------------------------------------- #
# Resumen
# --------------------------------------------------------------------------- #
async def summary(
    session: AsyncSession, *, tenant_id: uuid.UUID, window: Window
) -> dict[str, Any]:
    """Cifras de cabecera del periodo."""
    filas = list(
        (
            await session.execute(
                _scope(
                    select(
                        Conversation.id,
                        Conversation.status,
                        Conversation.created_at,
                        Conversation.first_response_at,
                        Conversation.sla_breached_at,
                    ),
                    tenant_id,
                    window,
                )
            )
        ).all()
    )

    respuestas = [
        (fila.first_response_at - fila.created_at).total_seconds()
        for fila in filas
        if fila.first_response_at is not None
    ]
    atendidas = sum(1 for fila in filas if fila.status in CLOSED_STATUSES)
    incumplidas = sum(1 for fila in filas if fila.sla_breached_at is not None)

    # Solo cuenta lo que tuvo objetivo de respuesta: una conversación sin SLA
    # configurado no puede incumplirlo, y meterla en el denominador maquillaría
    # el porcentaje hacia arriba.
    con_objetivo = sum(
        1
        for fila in filas
        if fila.first_response_at is not None or fila.sla_breached_at is not None
    )

    autoria = dict(
        (
            await session.execute(
                _scope(
                    select(Message.author_type, func.count())
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .group_by(Message.author_type),
                    tenant_id,
                    window,
                )
            )
        ).all()
    )

    return {
        "conversations": len(filas),
        "closed": atendidas,
        "open": len(filas) - atendidas,
        "first_response_seconds_p50": percentile(respuestas, 0.5),
        "first_response_seconds_p90": percentile(respuestas, 0.9),
        "answered_by_a_person": len(respuestas),
        "sla_breached": incumplidas,
        "sla_compliance": (
            None if not con_objetivo else round(1 - incumplidas / con_objetivo, 4)
        ),
        "messages_by_author": {str(clave): valor for clave, valor in autoria.items()},
    }


# --------------------------------------------------------------------------- #
# Series y desgloses
# --------------------------------------------------------------------------- #
async def timeseries(
    session: AsyncSession, *, tenant_id: uuid.UUID, window: Window
) -> list[dict[str, Any]]:
    """Conversaciones abiertas por día, sin huecos.

    Los días vacíos se rellenan con cero en lugar de omitirse: una gráfica que
    salta del lunes al jueves miente sobre la forma de la curva.
    """
    filas = (
        await session.execute(
            _scope(select(Conversation.created_at), tenant_id, window)
        )
    ).all()

    por_dia: dict[date, int] = {}
    for (creada,) in filas:
        por_dia[creada.date()] = por_dia.get(creada.date(), 0) + 1

    serie = []
    dia = window.since.date()
    ultimo = window.until.date()
    while dia <= ultimo:
        serie.append({"day": dia.isoformat(), "conversations": por_dia.get(dia, 0)})
        dia += timedelta(days=1)
    return serie


async def by_channel(
    session: AsyncSession, *, tenant_id: uuid.UUID, window: Window
) -> list[dict[str, Any]]:
    filas = (
        await session.execute(
            _scope(
                select(Conversation.channel, func.count()).group_by(Conversation.channel),
                tenant_id,
                window,
            )
        )
    ).all()
    return [
        {"channel": str(canal), "conversations": cuantas}
        for canal, cuantas in sorted(filas, key=lambda fila: -fila[1])
    ]


async def by_department(
    session: AsyncSession, *, tenant_id: uuid.UUID, window: Window
) -> list[dict[str, Any]]:
    filas = (
        await session.execute(
            _scope(
                select(Department.name, func.count())
                .select_from(Conversation)
                .outerjoin(Department, Conversation.department_id == Department.id)
                .group_by(Department.name),
                tenant_id,
                window,
            )
        )
    ).all()
    return [
        # Sin departamento = todavía en la cola común, no derivada a ninguna rama.
        {"department": nombre or "Sin departamento", "conversations": cuantas}
        for nombre, cuantas in sorted(filas, key=lambda fila: -fila[1])
    ]


async def by_agent(
    session: AsyncSession, *, tenant_id: uuid.UUID, window: Window
) -> list[dict[str, Any]]:
    """Carga y tiempo de respuesta por persona.

    Se mira el responsable actual y no el histórico de derivaciones: lo que
    interesa es de qué responde cada quien, no por cuántas manos pasó el hilo.
    """
    filas = list(
        (
            await session.execute(
                _scope(
                    select(
                        Agent.id,
                        Agent.display_name,
                        Agent.email,
                        Conversation.status,
                        Conversation.created_at,
                        Conversation.first_response_at,
                        Conversation.sla_breached_at,
                    )
                    .select_from(Conversation)
                    .join(Agent, Conversation.assignee_id == Agent.id),
                    tenant_id,
                    window,
                )
            )
        ).all()
    )

    por_agente: dict[uuid.UUID, dict[str, Any]] = {}
    for fila in filas:
        entrada = por_agente.setdefault(
            fila.id,
            {
                "agent_id": str(fila.id),
                "agent": fila.display_name or fila.email,
                "conversations": 0,
                "closed": 0,
                "sla_breached": 0,
                "_respuestas": [],
            },
        )
        entrada["conversations"] += 1
        if fila.status in CLOSED_STATUSES:
            entrada["closed"] += 1
        if fila.sla_breached_at is not None:
            entrada["sla_breached"] += 1
        if fila.first_response_at is not None:
            entrada["_respuestas"].append(
                (fila.first_response_at - fila.created_at).total_seconds()
            )

    resultado = []
    for entrada in por_agente.values():
        respuestas = entrada.pop("_respuestas")
        entrada["first_response_seconds_p50"] = percentile(respuestas, 0.5)
        resultado.append(entrada)
    return sorted(resultado, key=lambda fila: -fila["conversations"])


async def ai_usage(
    session: AsyncSession, *, tenant_id: uuid.UUID, window: Window
) -> dict[str, Any]:
    """Consumo del asistente en el periodo.

    Se acota por la fecha de la propia ejecución y no por la de la
    conversación: lo que se mide es el gasto del mes, y una conversación
    antigua que sigue viva también lo genera.
    """
    def acotado(stmt: Select) -> Select:
        # Mismo alcance por departamento que el resto del informe, pero por
        # la fecha de la ejecución y no por la de la conversación.
        stmt = stmt.join(Conversation, Conversation.id == AIRun.conversation_id).where(
            AIRun.tenant_id == tenant_id,
            AIRun.created_at >= window.since,
            AIRun.created_at <= window.until,
        )
        if window.scope_department_ids is not None:
            stmt = stmt.where(
                or_(
                    Conversation.department_id.is_(None),
                    Conversation.department_id.in_(window.scope_department_ids),
                )
            )
        if window.only_department_id is not None:
            stmt = stmt.where(Conversation.department_id == window.only_department_id)
        return stmt

    fila = (
        await session.execute(
            acotado(
                select(
                    func.count(),
                    func.sum(AIRun.input_tokens),
                    func.sum(AIRun.output_tokens),
                    func.avg(AIRun.latency_ms),
                ).select_from(AIRun)
            )
        )
    ).one()

    errores = (
        await session.execute(
            acotado(select(func.count()).select_from(AIRun)).where(AIRun.error.is_not(None))
        )
    ).scalar_one()

    return {
        "runs": fila[0] or 0,
        "input_tokens": int(fila[1] or 0),
        "output_tokens": int(fila[2] or 0),
        "average_latency_ms": int(fila[3]) if fila[3] is not None else None,
        "failed_runs": errores or 0,
    }
