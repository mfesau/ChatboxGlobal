"""Estado del sistema: lo que hay que mirar para saber si todo funciona.

Hasta aquí los fallos se descubrían cuando alguien los veía: el asistente estuvo
sin responder desde la migración a Gemini sin que nadie lo notara, y el cupo
agotado del proveedor tampoco avisaba. Este módulo reúne en un solo sitio las
cifras que lo habrían delatado —errores por canal, fallos de la IA, mensajes
atascados en la cola de salida, espacio en disco— y les pone un semáforo.

Lo usa la pestaña «Estado del sistema» de la consola. Está separado de la API
para que una comprobación periódica con aviso pueda usar las mismas cifras y
los mismos umbrales.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.envelope import Direction, utcnow
from app.db.models import AIRun, ChannelAccount, Message, OutboxItem

#: Ventana de los recuentos. Un día cubre una noche entera sin nadie mirando.
WINDOW = timedelta(hours=24)

OK, WARN, ERROR = "ok", "warn", "error"

#: Umbrales del semáforo.
OUTBOX_WARN_S = 5 * 60
OUTBOX_ERROR_S = 30 * 60
AI_ERROR_RATE = 0.2
AI_MIN_RUNS = 5
DISK_WARN = 0.20
DISK_ERROR = 0.10


def _worst(*levels: str) -> str:
    order = {OK: 0, WARN: 1, ERROR: 2}
    return max(levels, key=lambda level: order[level], default=OK)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _database(session: AsyncSession) -> dict[str, Any]:
    size: int | None = None
    if session.bind.dialect.name == "postgresql":
        size = (
            await session.execute(text("SELECT pg_database_size(current_database())"))
        ).scalar_one()
    return {"status": OK, "size_bytes": size}


def _disk(path: str) -> dict[str, Any]:
    """Espacio del disco donde se guardan los adjuntos."""
    target = Path(path)
    try:
        usage = shutil.disk_usage(target if target.exists() else target.parent)
    except OSError as exc:
        return {"status": ERROR, "path": path, "error": str(exc)}
    free_ratio = usage.free / usage.total if usage.total else 0.0
    status = ERROR if free_ratio < DISK_ERROR else WARN if free_ratio < DISK_WARN else OK
    return {
        "status": status,
        "path": path,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_ratio": round(free_ratio, 3),
    }


async def _outbox(session: AsyncSession, tenant_id: Any, since: datetime) -> dict[str, Any]:
    """Cola de salida: lo que espera, lo atascado y lo que se dio por perdido."""
    counts = dict(
        (
            await session.execute(
                select(OutboxItem.status, func.count())
                .where(OutboxItem.tenant_id == tenant_id)
                .where(
                    OutboxItem.status.in_(["pending", "in_progress"])
                    | (OutboxItem.updated_at >= since)
                )
                .group_by(OutboxItem.status)
            )
        ).all()
    )
    oldest = (
        await session.execute(
            select(func.min(OutboxItem.created_at)).where(
                OutboxItem.tenant_id == tenant_id,
                OutboxItem.status.in_(["pending", "in_progress"]),
            )
        )
    ).scalar_one()
    waited = int((utcnow() - _aware(oldest)).total_seconds()) if oldest else None
    perdidos = counts.get("failed", 0) + counts.get("dead", 0)

    status = OK
    if waited is not None and waited > OUTBOX_ERROR_S:
        status = ERROR
    elif (waited is not None and waited > OUTBOX_WARN_S) or perdidos:
        status = WARN
    return {
        "status": status,
        "pending": counts.get("pending", 0) + counts.get("in_progress", 0),
        "oldest_pending_seconds": waited,
        "failed_24h": perdidos,
        "sent_24h": counts.get("sent", 0),
    }


async def _ai(
    session: AsyncSession, tenant_id: Any, since: datetime, *, provider: str | None, model: str
) -> dict[str, Any]:
    runs, errors = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(AIRun.error.is_not(None)),
            ).where(AIRun.tenant_id == tenant_id, AIRun.created_at >= since)
        )
    ).one()
    last = (
        await session.execute(
            select(AIRun.created_at, AIRun.error)
            .where(AIRun.tenant_id == tenant_id, AIRun.error.is_not(None))
            .order_by(AIRun.created_at.desc())
            .limit(1)
        )
    ).first()
    rate = errors / runs if runs else 0.0

    if runs >= AI_MIN_RUNS and rate >= AI_ERROR_RATE:
        status = ERROR
    elif errors or provider is None:
        # Sin proveedor el asistente está apagado: no es una avería, pero
        # quien administra tiene que saberlo.
        status = WARN
    else:
        status = OK
    return {
        "status": status,
        "provider": provider,
        "model": model,
        "runs_24h": runs,
        "errors_24h": errors,
        "error_rate": round(rate, 3),
        "last_error": {"at": _iso(last[0]), "text": (last[1] or "")[:500]} if last else None,
    }


async def _channels(session: AsyncSession, tenant_id: Any, since: datetime) -> list[dict[str, Any]]:
    """Por canal: cuentas activas, último mensaje en cada sentido y fallos de envío."""
    accounts = dict(
        (
            await session.execute(
                select(ChannelAccount.channel, func.count())
                .where(ChannelAccount.tenant_id == tenant_id, ChannelAccount.is_active.is_(True))
                .group_by(ChannelAccount.channel)
            )
        ).all()
    )
    last_messages: dict[tuple[str, str], datetime] = {
        (str(channel), str(direction)): at
        for channel, direction, at in (
            await session.execute(
                select(Message.channel, Message.direction, func.max(Message.created_at))
                .where(Message.tenant_id == tenant_id)
                .group_by(Message.channel, Message.direction)
            )
        ).all()
    }
    failures = dict(
        (
            await session.execute(
                select(OutboxItem.channel, func.count())
                .where(
                    OutboxItem.tenant_id == tenant_id,
                    OutboxItem.status.in_(["failed", "dead"]),
                    OutboxItem.updated_at >= since,
                )
                .group_by(OutboxItem.channel)
            )
        ).all()
    )
    last_errors = {
        str(channel): (at, error)
        for channel, at, error in (
            await session.execute(
                select(OutboxItem.channel, OutboxItem.updated_at, OutboxItem.last_error)
                .where(OutboxItem.tenant_id == tenant_id, OutboxItem.last_error.is_not(None))
                .order_by(OutboxItem.updated_at.asc())
            )
        ).all()
    }

    nombres = {str(c) for c in accounts} | {c for c, _ in last_messages} | set(last_errors)
    nombres |= {str(c) for c in failures}
    filas = []
    for nombre in sorted(nombres):
        fallos = next((n for c, n in failures.items() if str(c) == nombre), 0)
        ultimo_error = last_errors.get(nombre)
        filas.append(
            {
                "channel": nombre,
                "status": WARN if fallos else OK,
                "active_accounts": next((n for c, n in accounts.items() if str(c) == nombre), 0),
                "last_inbound_at": _iso(last_messages.get((nombre, str(Direction.INBOUND)))),
                "last_outbound_at": _iso(last_messages.get((nombre, str(Direction.OUTBOUND)))),
                "failures_24h": fallos,
                "last_error": (
                    {"at": _iso(ultimo_error[0]), "text": (ultimo_error[1] or "")[:500]}
                    if ultimo_error
                    else None
                ),
            }
        )
    return filas


def _aware(value: datetime) -> datetime:
    """SQLite devuelve fechas sin zona; se entienden en UTC, como se guardaron."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def snapshot(
    session: AsyncSession,
    *,
    tenant_id: Any,
    settings: Any,
    provider: str | None,
    model: str,
    started_at: datetime | None,
) -> dict[str, Any]:
    """Todas las cifras, con un semáforo por apartado y uno general."""
    since = utcnow() - WINDOW
    database = await _database(session)
    disk = _disk(settings.uploads_dir)
    outbox = await _outbox(session, tenant_id, since)
    ai = await _ai(session, tenant_id, since, provider=provider, model=model)
    channels = await _channels(session, tenant_id, since)
    return {
        "status": _worst(
            database["status"],
            disk["status"],
            outbox["status"],
            ai["status"],
            *(row["status"] for row in channels),
        ),
        "generated_at": utcnow().isoformat(),
        "started_at": _iso(started_at),
        "environment": settings.environment,
        "database": database,
        "disk": disk,
        "outbox": outbox,
        "ai": ai,
        "channels": channels,
    }
