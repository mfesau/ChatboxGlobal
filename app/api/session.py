"""Login unificado: un solo formulario en «/» para clientes y para el equipo.

«/» es la entrada única de la aplicación (ver ``app/main.py``): quien escribe
ahí su correo y contraseña no elige de antemano si es del equipo o un
cliente — este endpoint prueba primero como agente y, si no coincide, como
cliente, y deja la sesión que corresponda. «/console» deja de tener su propio
formulario de acceso: sin sesión de agente válida, redirige aquí.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

from app.api.auth import _DUMMY_HASH as _AGENT_DUMMY_HASH
from app.api.auth import _serialize as _serialize_agent
from app.api.auth import issue_session
from app.api.contact_auth import _DUMMY_HASH as _CONTACT_DUMMY_HASH
from app.api.contact_auth import _serialize as _serialize_contact
from app.api.contact_auth import issue_contact_session
from app.api.deps import SessionDep, SettingsDep
from app.core.security import verify_password
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/session", tags=["sesión unificada"])


class UnifiedLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    tenant: str | None = None

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("El correo debe incluir el signo @")
        return value


@router.post("/login")
async def unified_login(
    body: UnifiedLoginIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Prueba el correo primero contra los agentes y luego contra los clientes.

    Las dos verificaciones de contraseña corren siempre, exista o no la
    cuenta en cada tabla: así el tiempo de respuesta no delata si un correo
    pertenece al equipo, a un cliente, o a ninguno de los dos — mismo
    principio que ya aplican por separado ``app/api/auth.py`` y
    ``app/api/contact_auth.py``.
    """
    tenant = await repo.get_or_create_tenant(session, body.tenant or settings.default_tenant_slug)

    agent = await repo.find_agent_by_email(session, tenant_id=tenant.id, email=body.email)
    agent_stored = agent.password_hash if agent is not None else _AGENT_DUMMY_HASH
    agent_valid = await asyncio.to_thread(verify_password, body.password, agent_stored)

    contact = await repo.find_contact_by_email(session, tenant_id=tenant.id, email=body.email)
    contact_stored = contact.password_hash if contact is not None else _CONTACT_DUMMY_HASH
    contact_valid = await asyncio.to_thread(verify_password, body.password, contact_stored)

    if agent is not None and agent.is_active and agent_valid:
        await issue_session(
            agent=agent,
            tenant=tenant,
            request=request,
            response=response,
            session=session,
            settings=settings,
        )
        await repo.record_audit(
            session,
            tenant_id=tenant.id,
            actor=f"agent:{agent.email}",
            action="login",
            subject_type="agent",
            subject_id=str(agent.id),
        )
        log.info("unified_login_ok", kind="agent", email=agent.email)
        return {"kind": "agent", "redirect": "/console", "agent": _serialize_agent(agent)}

    if contact is not None and not contact.is_blocked and contact_valid:
        await issue_contact_session(
            contact=contact,
            request=request,
            response=response,
            session=session,
            settings=settings,
        )
        log.info("unified_login_ok", kind="contact", email=contact.primary_email)
        return {"kind": "contact", "redirect": "/", "contact": _serialize_contact(contact)}

    log.warning("unified_login_rejected", email=body.email, tenant=tenant.slug)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales no válidas"
    )
