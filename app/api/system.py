"""Estado del sistema para administración (ver ``app/core/health.py``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import AdminDep, SessionDep, SettingsDep
from app.core import ai_settings, health
from app.db import repositories as repo

router = APIRouter(prefix="/api/admin/system", tags=["operación"])


@router.get("")
async def system_status(
    request: Request, session: SessionDep, settings: SettingsDep, principal: AdminDep
) -> dict[str, Any]:
    """Errores por canal, fallos de la IA, cola de salida, disco y base de datos."""
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    provider = settings.ai_backend
    model = (
        ai_settings.read(tenant, settings).model if provider == "openrouter" else settings.ai_model
    )
    return await health.snapshot(
        session,
        tenant_id=tenant.id,
        settings=settings,
        provider=provider,
        model=model,
        started_at=getattr(request.app.state, "started_at", None),
    )
