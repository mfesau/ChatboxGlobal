"""Auditoría y derechos del interesado.

Dos cosas que la aplicación ya hacía a medias. La tabla ``audit_log`` se venía
escribiendo desde una cuarentena de sitios, pero no había forma de leerla salvo
entrando en la base a mano. Y de los datos de un contacto se podía ver la ficha,
pero no exportarlos ni borrarlos, que es lo que el RGPD concede a quien escribe.

El borrado exige rol de administración —no de supervisión— y una confirmación
explícita en la URL: no se puede deshacer.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.api.deps import AdminDep, SessionDep, SettingsDep, SupervisorDep
from app.core.privacy import anonymise_contact, export_contact
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["auditoría y privacidad"])


class AuditEntryOut(BaseModel):
    id: uuid.UUID
    actor: str
    action: str
    subject_type: str | None
    subject_id: str | None
    detail: dict[str, Any]
    created_at: str


@router.get("/audit", response_model=list[AuditEntryOut])
async def list_audit(
    session: SessionDep,
    settings: SettingsDep,
    principal: SupervisorDep,
    actor: str | None = None,
    action: str | None = None,
    subject_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEntryOut]:
    """Traza de acciones administrativas, de la más reciente a la más antigua.

    Abierta a supervisión y no solo a administración: quien responde de un
    turno necesita poder ver quién reasignó qué, y la traza no contiene el
    contenido de ninguna conversación.
    """
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    rows = await repo.list_audit(
        session,
        tenant_id=tenant.id,
        actor=actor,
        action=action,
        subject_id=subject_id,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [
        AuditEntryOut(
            id=row.id,
            actor=row.actor,
            action=row.action,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            detail=row.detail,
            created_at=row.created_at.isoformat(),
        )
        for row in rows
    ]


@router.get("/audit/actions", response_model=list[str])
async def list_audit_actions(
    session: SessionDep, settings: SettingsDep, principal: SupervisorDep
) -> list[str]:
    """Acciones presentes en la traza, para poblar el desplegable del filtro."""
    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    return await repo.list_audit_actions(session, tenant_id=tenant.id)


@router.get("/contacts/{contact_id}/export")
async def export_contact_data(
    contact_id: uuid.UUID,
    session: SessionDep,
    principal: SupervisorDep,
) -> Response:
    """Copia de todo lo que se guarda sobre un contacto (RGPD, artículo 15).

    Se devuelve como descarga y no como respuesta JSON corriente para que el
    navegador ofrezca guardarla: quien la pide se la va a entregar al
    interesado, no la va a leer en pantalla.
    """
    contact = await repo.get_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")

    bundle = await export_contact(session, contact)
    await repo.record_audit(
        session,
        tenant_id=contact.tenant_id,
        actor=principal.audit_actor,
        action="contact_exported",
        subject_type="contact",
        subject_id=str(contact_id),
        detail={"messages": len(bundle["messages"])},
    )
    log.info("contact_exported", contact_id=str(contact_id), actor=principal.audit_actor)

    return Response(
        content=json.dumps(bundle, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="contacto-{contact_id}.json"',
            # No es una respuesta que deba quedar en ninguna caché intermedia.
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/contacts/{contact_id}/erase")
async def erase_contact_data(
    contact_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    confirm: bool = False,
) -> dict[str, Any]:
    """Anonimiza a un contacto y su rastro (RGPD, artículo 17).

    Reservado a administración y con ``?confirm=true``: no hay vuelta atrás y
    tampoco copia del texto borrado. Las filas se conservan anonimizadas para
    no falsear los informes ni perder la trazabilidad de quién atendió qué;
    lo que se va es el dato personal.
    """
    contact = await repo.get_contact(session, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contacto no encontrado")
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El borrado es irreversible: repita la llamada con confirm=true",
        )

    summary = await anonymise_contact(session, contact, settings=settings)
    await repo.record_audit(
        session,
        tenant_id=contact.tenant_id,
        actor=principal.audit_actor,
        action="contact_erased",
        subject_type="contact",
        subject_id=str(contact_id),
        detail=summary,
    )
    return {"status": "ok", **summary}
