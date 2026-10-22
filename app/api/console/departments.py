"""Departamentos: alta, logotipo, horario y encuesta de satisfacción."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.api.console._common import _department_out, _require_known_timezone
from app.api.console.schemas import BusinessHoursIn, DepartmentIn, DepartmentOut
from app.api.deps import (
    AdminDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
)
from app.core.storage import save_upload
from app.db import repositories as repo

router = APIRouter(prefix="/api", tags=["consola"])



# --------------------------------------------------------------------------- #
# Departamentos
# --------------------------------------------------------------------------- #
@router.get("/departments", response_model=list[DepartmentOut])
async def list_departments(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[DepartmentOut]:
    """Lista de departamentos. Cualquier persona autenticada la necesita para derivar."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    departments = await repo.list_departments(session, tenant_id=tenant_row.id)
    return [_department_out(row) for row in departments]


@router.post("/departments", status_code=status.HTTP_201_CREATED, response_model=DepartmentOut)
async def create_department(
    body: DepartmentIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> DepartmentOut:
    """Crea un departamento. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_department_by_name(session, tenant_id=tenant_row.id, name=body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe un departamento con ese nombre"
        )
    department = await repo.create_department(session, tenant_id=tenant_row.id, name=body.name)
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="department_created",
        subject_type="department",
        subject_id=str(department.id),
        detail={"name": department.name},
    )
    return _department_out(department)


@router.get("/departments/{department_id}/logo", include_in_schema=False)
async def department_logo(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> FileResponse:
    """Entrega el logo de un departamento a cualquier persona autenticada.

    A diferencia de un adjunto (``app/api/attachments.py``), un logo no
    pertenece a ninguna conversación concreta —lo ve cualquiera del
    inquilino que necesite pintar la pestaña de ese departamento—, así que
    no se aplica la comprobación de acceso por conversación, solo la de
    inquilino.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_row.id or not department.logo_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sin logo")
    path = Path(settings.uploads_dir) / department.logo_path
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sin logo")
    return FileResponse(
        path,
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.put("/departments/{department_id}/logo", response_model=DepartmentOut)
async def set_department_logo(
    department_id: uuid.UUID,
    file: UploadFile,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> DepartmentOut:
    """Fija el logo de un departamento. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )
    attachment = await save_upload(file, namespace="departments", settings=settings)
    previous = department.logo_path
    department.logo_path = attachment.url.removeprefix("/uploads/")
    await session.flush()
    if previous:
        (Path(settings.uploads_dir) / previous).unlink(missing_ok=True)
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="department_logo_updated",
        subject_type="department",
        subject_id=str(department.id),
    )
    return _department_out(department)


@router.delete("/departments/{department_id}/logo", response_model=DepartmentOut)
async def delete_department_logo(
    department_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> DepartmentOut:
    """Quita el logo de un departamento. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )
    previous = department.logo_path
    department.logo_path = None
    await session.flush()
    if previous:
        (Path(settings.uploads_dir) / previous).unlink(missing_ok=True)
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="department_logo_removed",
        subject_type="department",
        subject_id=str(department.id),
    )
    return _department_out(department)


@router.put("/departments/{department_id}/business-hours", response_model=DepartmentOut)
async def set_business_hours(
    department_id: uuid.UUID,
    body: BusinessHoursIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> DepartmentOut:
    """Fija el horario de atención de un departamento. Reservado a administración.

    Fuera de ese horario el asistente deja de responder y, si hay texto de
    aviso, el cliente lo recibe una sola vez. Un horario vacío vuelve a la
    atención permanente.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )
    _require_known_timezone(body.timezone)

    department.business_hours = body.business_hours
    department.timezone = body.timezone
    department.out_of_hours_message = body.out_of_hours_message
    department.first_response_target_minutes = body.first_response_target_minutes
    await session.flush()
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="department_business_hours_updated",
        subject_type="department",
        subject_id=str(department.id),
        detail={"timezone": body.timezone, "days": sorted(body.business_hours)},
    )
    return _department_out(department)
