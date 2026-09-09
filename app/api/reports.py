"""Informes de supervisión.

Hasta ahora la consola solo enseñaba un recuento de mensajes por canal
(``GET /api/stats``) y la carga instantánea del equipo. Faltaba lo que se
pregunta al cerrar un mes: cuánto se tarda en atender, si se cumple el objetivo
de primera respuesta, por qué canal entra el trabajo y cuánto de todo eso lo
resolvió el asistente sin molestar a nadie.

Reservado a supervisión, y acotado por departamento cuando quien pregunta lo
tiene acotado: un supervisor de una rama no debe medir el rendimiento de otra.

No hay tablas nuevas. Todo sale de ``conversations``, ``messages`` y
``ai_runs``, que ya guardan lo necesario.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import SessionDep, SettingsDep, SupervisorDep
from app.db import reports
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/reports", tags=["informes"])

#: Periodo por omisión cuando no se piden fechas.
DEFAULT_DAYS = 30

#: Tope del intervalo. Sin él, una petición de diez años recorrería la tabla
#: entera de mensajes y dejaría la consola colgada.
MAX_DAYS = 366


def _window(
    principal: Any, since: date | None, until: date | None, department_id: uuid.UUID | None
) -> reports.Window:
    """Traduce los parámetros a un intervalo, y aplica el alcance de quien pregunta."""
    hasta = until or datetime.now(UTC).date()
    desde = since or hasta - timedelta(days=DEFAULT_DAYS)
    if desde > hasta:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La fecha inicial es posterior a la final",
        )
    if (hasta - desde).days > MAX_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El intervalo no puede pasar de {MAX_DAYS} días",
        )

    # ``None`` = sin acotar (administración y la clave de servicio). Un
    # conjunto acota a lo otorgado, más lo que sigue en la cola común: es la
    # misma regla que aplica la bandeja de esa persona.
    permitidos = principal.department_ids
    if permitidos is not None and department_id is not None and department_id not in permitidos:
        # 404 y no 403, igual que con las conversaciones ajenas: confirmar que
        # el departamento existe ya diría algo de la otra rama.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )

    return reports.Window(
        # El día final entra entero: pedir «hasta el 30» y perder ese día
        # sería una sorpresa desagradable en cada cierre de mes.
        since=datetime.combine(desde, time.min, tzinfo=UTC),
        until=datetime.combine(hasta, time.max, tzinfo=UTC),
        scope_department_ids=None if permitidos is None else set(permitidos),
        only_department_id=department_id,
    )


DateParam = Annotated[date | None, Query()]


@router.get("/summary")
async def report_summary(
    session: SessionDep,
    settings: SettingsDep,
    principal: SupervisorDep,
    since: DateParam = None,
    until: DateParam = None,
    department_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Todo el informe en una llamada: cifras, series y desgloses.

    Va junto y no en cinco endpoints porque la pantalla los pinta a la vez, y
    cinco viajes para dibujar una sola vista solo añaden parpadeo.
    """
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    window = _window(principal, since, until, department_id)

    return {
        "since": window.since.date().isoformat(),
        "until": window.until.date().isoformat(),
        "summary": await reports.summary(session, tenant_id=tenant.id, window=window),
        "timeseries": await reports.timeseries(session, tenant_id=tenant.id, window=window),
        "channels": await reports.by_channel(session, tenant_id=tenant.id, window=window),
        "departments": await reports.by_department(session, tenant_id=tenant.id, window=window),
        "agents": await reports.by_agent(session, tenant_id=tenant.id, window=window),
        "ai": await reports.ai_usage(session, tenant_id=tenant.id, window=window),
    }


@router.get("/export.csv")
async def export_csv(
    session: SessionDep,
    settings: SettingsDep,
    principal: SupervisorDep,
    dataset: Annotated[str, Query(pattern="^(timeseries|channels|departments|agents)$")],
    since: DateParam = None,
    until: DateParam = None,
    department_id: uuid.UUID | None = None,
) -> Response:
    """Un desglose en CSV, para seguir trabajándolo en una hoja de cálculo."""
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    window = _window(principal, since, until, department_id)

    # Con un diccionario de resultados se calcularían los cuatro desgloses para
    # devolver uno; se elige antes de consultar.
    consulta = {
        "timeseries": reports.timeseries,
        "channels": reports.by_channel,
        "departments": reports.by_department,
        "agents": reports.by_agent,
    }[dataset]
    filas: list[dict[str, Any]] = await consulta(
        session, tenant_id=tenant.id, window=window
    )

    buffer = io.StringIO()
    if filas:
        writer = csv.DictWriter(buffer, fieldnames=list(filas[0]))
        writer.writeheader()
        writer.writerows(filas)

    return Response(
        # Con BOM: sin él, Excel abre el CSV en la codificación del sistema y
        # destroza cualquier nombre con tilde.
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{dataset}-{window.since.date()}'
                f'-{window.until.date()}.csv"'
            ),
            "Cache-Control": "private, no-store",
        },
    )
