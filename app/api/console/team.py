"""Equipo: agentes, vistas guardadas y supervisión."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.api.auth import generate_temporary_password
from app.api.console._common import _agent_names
from app.api.console.schemas import (
    AgentDepartmentsIn,
    AgentOut,
    AgentUpdateIn,
    NewAgentIn,
    PasswordIn,
    SavedViewIn,
    SavedViewOut,
)
from app.api.deps import (
    AdminDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    SupervisorDep,
)
from app.core.mailer import invitation_email_body, send_email
from app.core.security import WeakPasswordError, hash_password
from app.db import repositories as repo
from app.db.models import (
    ROLE_ADMIN,
    ROLE_AGENT,
)
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["consola"])



# --------------------------------------------------------------------------- #
# Equipo
# --------------------------------------------------------------------------- #
@router.get("/agents", response_model=list[AgentOut])
async def list_agents(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[AgentOut]:
    """El equipo, activo e inactivo. Alimenta el desplegable de derivación y
    la tabla de administración; cada cliente filtra lo que no le sirve —por
    ejemplo, no se deriva a una cuenta desactivada—."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return [
        AgentOut(
            id=agent.id,
            email=agent.email,
            display_name=agent.display_name,
            role=agent.role,
            presence=agent.presence,
            is_active=agent.is_active,
            department_id=agent.department_id,
            extra_department_ids=[d.id for d in agent.granted_departments],
        )
        for agent in await repo.list_agents(session, tenant_id=tenant_row.id, only_active=False)
    ]


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: NewAgentIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Da de alta un compañero. Reservado a supervisión.

    Un agente elige su propia contraseña inicial en el formulario. Un
    supervisor o administrador, en cambio, recibe una generada por el
    sistema: la persona que crea la cuenta no necesita inventar ni comunicar
    una contraseña, y quien se incorpora la recibe por correo de invitación
    junto con el enlace de acceso.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_agent_by_email(session, tenant_id=tenant_row.id, email=body.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe un agente con ese correo"
        )
    if body.department_id is not None:
        department = await repo.get_department(session, body.department_id)
        if department is None or department.tenant_id != tenant_row.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )

    generated_password: str | None = None
    if body.role == ROLE_AGENT:
        if not body.password:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La contraseña es obligatoria para el rol de agente",
            )
        plain_password = body.password
    else:
        generated_password = generate_temporary_password()
        plain_password = generated_password

    try:
        # La derivación de la contraseña es intencionadamente costosa: se ejecuta
        # en un hilo para no bloquear el bucle de eventos.
        password_hash = await asyncio.to_thread(hash_password, plain_password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    agent = await repo.create_agent(
        session,
        tenant_id=tenant_row.id,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
        password_hash=password_hash,
        department_id=body.department_id,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="agent_created",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={"email": agent.email, "role": agent.role},
    )

    email_sent = False
    if generated_password is not None:
        role_label = "administrador" if body.role == ROLE_ADMIN else "supervisor"
        email_sent = await send_email(
            settings=settings,
            to=agent.email,
            subject=f"Su cuenta de {role_label} en {settings.app_name}",
            body=invitation_email_body(
                display_name=agent.display_name,
                role_label=role_label,
                email=agent.email,
                temporary_password=generated_password,
                app_name=settings.app_name,
                base_url=settings.public_base_url,
            ),
        )
        if not email_sent:
            log.warning("invitation_email_not_sent", agent=agent.email, role=agent.role)

    return {
        "status": "ok",
        "agent_id": str(agent.id),
        "email": agent.email,
        # Presente solo para supervisor/admin: quien administra la ve una vez
        # en la consola, por si el correo de invitación no llegó a enviarse.
        "temporary_password": generated_password,
        "invitation_email_sent": email_sent,
    }


@router.post("/agents/{agent_id}/password")
async def set_password(
    agent_id: uuid.UUID, body: PasswordIn, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Establece o restablece una contraseña. Reservado a administración."""
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    try:
        agent.password_hash = await asyncio.to_thread(hash_password, body.password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    # Administración acaba de elegir esta contraseña a conciencia, de modo que
    # levanta la obligación de cambiarla; es también cómo se desatasca una
    # cuenta de arranque cuya contraseña generada se perdió.
    agent.must_change_password = False
    return {"status": "ok"}


@router.patch("/agents/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdateIn, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Cambia el nombre visible de un agente, o reactiva una cuenta. Reservado
    a administración."""
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nada para actualizar"
        )
    if "display_name" in changes:
        agent.display_name = changes["display_name"].strip()
    if "is_active" in changes:
        agent.is_active = changes["is_active"]

    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_updated",
        subject_type="agent",
        subject_id=str(agent.id),
        detail=changes,
    )
    return {"status": "ok"}


@router.delete("/agents/{agent_id}")
async def deactivate_agent(
    agent_id: uuid.UUID, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Desactiva una cuenta: no puede volver a iniciar sesión.

    No borra la fila: el nombre de la persona debe seguir apareciendo en el
    historial de derivaciones, notas y auditoría de antes de darla de baja.
    Reservado a administración.
    """
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")
    if agent.id == principal.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No puede desactivar su propia cuenta"
        )
    if agent.role == ROLE_ADMIN and agent.is_active:
        remaining = await repo.count_active_admins(session, tenant_id=agent.tenant_id)
        if remaining <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No puede desactivar al único administrador activo",
            )

    agent.is_active = False
    agent.presence = "offline"
    await repo.close_agent_sessions(session, agent.id)
    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_deactivated",
        subject_type="agent",
        subject_id=str(agent.id),
    )
    return {"status": "ok"}


@router.delete("/agents/{agent_id}/permanently")
async def delete_agent_permanently(
    agent_id: uuid.UUID, session: SessionDep, principal: AdminDep
) -> dict[str, str]:
    """Borra la cuenta de verdad. Reservado a administración.

    Frente a desactivar, esto no tiene vuelta atrás y **el historial pierde el
    nombre**: las derivaciones, notas y mensajes de esa persona siguen ahí,
    pero sin autor. Existe para lo que desactivar no resuelve —una cuenta
    creada por error, o alguien que ejerce su derecho a que le borren los
    datos—, no para dar de baja a quien deja el equipo; para eso es
    «Desactivar», que conserva quién hizo qué.

    Las salvaguardas son las mismas que al desactivar: ni la propia cuenta ni
    el último administrador activo, porque cualquiera de las dos dejaría la
    instalación sin quien la administre.
    """
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")
    if agent.id == principal.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="No puede eliminar su propia cuenta"
        )
    if agent.role == ROLE_ADMIN and agent.is_active:
        remaining = await repo.count_active_admins(session, tenant_id=agent.tenant_id)
        if remaining <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No puede eliminar al único administrador activo",
            )

    # La auditoría se anota antes de borrar y se queda con el correo y el
    # nombre: es lo único que quedará para saber a quién se dio de baja, ya que
    # la fila desaparece y el resto de tablas pierde la referencia.
    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_deleted",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={"email": agent.email, "display_name": agent.display_name, "role": agent.role},
    )
    await repo.delete_agent(session, agent)
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Vistas guardadas
# --------------------------------------------------------------------------- #
def _saved_view_out(row: Any) -> SavedViewOut:
    return SavedViewOut(
        id=row.id,
        name=row.name,
        filters=dict(row.filters or {}),
        shared=row.owner_agent_id is None,
    )


@router.get("/saved-views", response_model=list[SavedViewOut])
async def list_saved_views(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[SavedViewOut]:
    """Las vistas del equipo más las propias de quien pregunta."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    views = await repo.list_saved_views(session, tenant_id=tenant_row.id, agent_id=principal.id)
    return [_saved_view_out(row) for row in views]


@router.post("/saved-views", status_code=status.HTTP_201_CREATED, response_model=SavedViewOut)
async def create_saved_view(
    body: SavedViewIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> SavedViewOut:
    """Guarda una vista. Las del equipo quedan reservadas a supervisión."""
    if body.shared and not principal.is_supervisor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compartir una vista con el equipo requiere permisos de supervisión",
        )
    if not body.shared and principal.id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La clave de servicio solo puede crear vistas del equipo",
        )

    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    owner_agent_id = None if body.shared else principal.id
    if await repo.find_saved_view_by_name(
        session, tenant_id=tenant_row.id, owner_agent_id=owner_agent_id, name=body.name
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una vista con ese nombre"
        )

    # Se guarda solo lo que trae valor: una vista no arrastra claves vacías.
    filters = body.filters.model_dump(exclude_none=True, mode="json")
    view = await repo.create_saved_view(
        session,
        tenant_id=tenant_row.id,
        owner_agent_id=owner_agent_id,
        name=body.name,
        filters=filters,
    )
    return _saved_view_out(view)


@router.delete("/saved-views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_view(
    view_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    tenant: str | None = None,
) -> None:
    """Borra una vista propia; las del equipo, solo supervisión."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    view = await repo.get_saved_view(session, view_id)
    # 404 y no 403 cuando la vista es de otra persona: su existencia tampoco
    # es asunto de quien pregunta.
    if (
        view is None
        or view.tenant_id != tenant_row.id
        or (view.owner_agent_id is not None and view.owner_agent_id != principal.id)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vista no encontrada")
    if view.owner_agent_id is None and not principal.is_supervisor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Borrar una vista del equipo requiere permisos de supervisión",
        )
    await repo.delete_saved_view(session, view)


@router.put("/agents/{agent_id}/departments")
async def set_agent_departments(
    agent_id: uuid.UUID, body: AgentDepartmentsIn, session: SessionDep, principal: AdminDep
) -> dict[str, Any]:
    """Fija el departamento principal y los adicionales de un agente.

    Reservado a administración: es quien "está por encima de todo" y decide
    qué colas puede atender cada persona, más allá de la suya.
    """
    agent = await repo.get_agent(session, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agente no encontrado")

    for department_id in filter(None, [body.department_id, *body.extra_department_ids]):
        department = await repo.get_department(session, department_id)
        if department is None or department.tenant_id != agent.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Uno de los departamentos indicados no existe",
            )

    agent.department_id = body.department_id
    await repo.set_agent_departments(
        session, agent=agent, department_ids=body.extra_department_ids
    )
    await repo.record_audit(
        session,
        tenant_id=agent.tenant_id,
        actor=principal.audit_actor,
        action="agent_departments_updated",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={
            "department_id": str(body.department_id) if body.department_id else None,
            "extra_department_ids": [str(d) for d in body.extra_department_ids],
        },
    )
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Supervisión
# --------------------------------------------------------------------------- #
@router.get("/supervisor/overview")
async def supervisor_overview(
    session: SessionDep,
    settings: SettingsDep,
    principal: SupervisorDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Panorama del equipo: carga por agente y últimas derivaciones.

    Una supervisión acotada por departamento ve aquí solo lo suyo; la clave
    de servicio y la administración, sin restricción.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    names = await _agent_names(session, tenant_row.id)
    department_ids = principal.department_ids
    return {
        "tenant": tenant_row.slug,
        "workload": await repo.workload_by_agent(session, tenant_row.id, department_ids),
        "messages_by_channel": await repo.channel_stats(session, tenant_row.id, department_ids),
        "recent_transfers": [
            {
                "conversation_id": str(entry.conversation_id),
                "action": entry.action,
                "from": names.get(entry.from_agent_id),
                "to": names.get(entry.to_agent_id),
                "by": names.get(entry.by_agent_id),
                "note": entry.note,
                "at": entry.created_at.isoformat(),
            }
            for entry in await repo.transfer_activity(
                session, tenant_row.id, department_ids=department_ids
            )
        ],
    }


@router.get("/stats")
async def stats(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> dict[str, Any]:
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return {
        "tenant": tenant_row.slug,
        "messages_by_channel": await repo.channel_stats(session, tenant_row.id),
    }
