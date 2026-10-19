"""Ajustes del asistente en la consola: modelo y esfuerzo de razonamiento.

Solo administración. La clave nunca pasa por aquí —vive en el ``.env``—; la
consola solo sabe si está puesta.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import AdminDep, SessionDep, SettingsDep
from app.core import ai_settings
from app.db import repositories as repo

router = APIRouter(prefix="/api/admin/ai", tags=["asistente"])


class AiSettingsIn(BaseModel):
    #: Vacío vuelve al modelo del ``.env``.
    model: str | None = Field(default=None, max_length=ai_settings.MODEL_MAX_LENGTH)
    effort: str = "default"


def _payload(tenant_row: Any, settings: Any) -> dict[str, Any]:
    elegido = ai_settings.read(tenant_row, settings)
    return {
        "provider": settings.ai_backend,
        "openrouter_key_configured": settings.openrouter_api_key is not None,
        "model": elegido.model,
        "effort": elegido.effort,
        "default_model": settings.openrouter_model,
        "efforts": list(ai_settings.EFFORTS),
    }


@router.get("")
async def get_ai_settings(
    session: SessionDep, settings: SettingsDep, principal: AdminDep
) -> dict[str, Any]:
    tenant_row = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    return _payload(tenant_row, settings)


@router.get("/models")
async def list_models(settings: SettingsDep, principal: AdminDep) -> dict[str, Any]:
    """Catálogo de OpenRouter, reducido a los modelos que admiten herramientas."""
    return {"models": await ai_settings.fetch_catalog(settings)}


@router.put("")
async def set_ai_settings(
    body: AiSettingsIn, session: SessionDep, settings: SettingsDep, principal: AdminDep
) -> dict[str, Any]:
    model = (body.model or "").strip() or None
    if body.effort not in ai_settings.EFFORTS:
        raise HTTPException(
            status_code=422,
            detail=f"Esfuerzo desconocido: {body.effort}",
        )
    if model is not None:
        if not ai_settings.is_valid_model(model):
            raise HTTPException(
                status_code=422,
                detail="El modelo se escribe como proveedor/modelo, p. ej. google/gemini-3.8-flash",
            )
        # Con el catálogo a mano se comprueba que exista: un error de tecleo
        # dejaría el asistente mudo hasta que alguien mirase el log. Sin
        # catálogo (OpenRouter caído) se acepta tal cual.
        catalogo = await ai_settings.fetch_catalog(settings)
        if catalogo and model not in {m["id"] for m in catalogo}:
            raise HTTPException(
                status_code=422,
                detail=f"OpenRouter no tiene un modelo con herramientas llamado {model}",
            )

    tenant_row = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    guardado = {"effort": body.effort}
    if model is not None:
        guardado["model"] = model
    await repo.update_tenant_settings(
        session, tenant_row, **{ai_settings.AI_SETTINGS_KEY: guardado}
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="ai_settings_updated",
        subject_type="tenant",
        subject_id=str(tenant_row.id),
        detail=guardado,
    )
    return _payload(tenant_row, settings)
