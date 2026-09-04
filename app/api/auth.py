"""Inicio y cierre de sesión de los agentes de la consola."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    SESSION_COOKIE,
    SESSION_TTL,
    PrincipalDep,
    SessionDep,
    SettingsDep,
)
from app.config import Settings
from app.core.hub import hub, presence_topic
from app.core.security import hash_password, hash_token, new_session_token, verify_password
from app.db import repositories as repo
from app.db.models import Agent, Tenant
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/auth", tags=["sesión"])

#: Coste de una verificación fallida, para no delatar si el correo existe.
_DUMMY_HASH = hash_password("contraseña-inexistente-de-relleno")


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    tenant: str | None = None

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        # Comprobación deliberadamente laxa: el correo identifica al agente
        # dentro de la organización, no se envía nada a esa dirección.
        value = value.strip().lower()
        if "@" not in value:
            raise ValueError("El correo debe incluir el signo @")
        return value


class AgentOut(BaseModel):
    id: str
    email: str
    display_name: str | None
    role: str
    is_supervisor: bool


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Valida las credenciales y deja la sesión en una cookie ``HttpOnly``."""
    tenant = await repo.get_or_create_tenant(session, body.tenant or settings.default_tenant_slug)
    agent = await repo.find_agent_by_email(session, tenant_id=tenant.id, email=body.email)

    stored = agent.password_hash if agent is not None else _DUMMY_HASH
    # Se verifica siempre, incluso sin agente: así el tiempo de respuesta no
    # revela qué direcciones están registradas.
    valid = await asyncio.to_thread(verify_password, body.password, stored)

    if agent is None or not agent.is_active or not valid:
        log.warning("login_rejected", email=body.email, tenant=tenant.slug)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales no válidas"
        )

    await issue_session(
        agent=agent, tenant=tenant, request=request, response=response,
        session=session, settings=settings,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant.id,
        actor=f"agent:{agent.email}",
        action="login",
        subject_type="agent",
        subject_id=str(agent.id),
    )
    log.info("login_ok", agent=agent.email, role=agent.role)
    return {"agent": _serialize(agent), "expires_in_s": int(SESSION_TTL.total_seconds())}


async def issue_session(
    *,
    agent: Agent,
    tenant: Tenant,
    request: Request,
    response: Response,
    session: AsyncSession,
    settings: Settings,
) -> None:
    """Abre una sesión de agente y la deja en una cookie ``HttpOnly``.

    Común al login por contraseña y al inicio de sesión único (SAML): ambos
    terminan en el mismo tipo de sesión, así que el resto de la consola no
    distingue por qué puerta entró cada agente.
    """
    token = new_session_token()
    await repo.open_agent_session(
        session,
        agent_id=agent.id,
        token_hash=hash_token(token),
        ttl=SESSION_TTL,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await repo.touch_agent_presence(session, agent.id, "available")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        # En una red local sin TLS la cookie no puede exigir HTTPS; con
        # `SESSION_COOKIE_SECURE=true` detrás de un proxy con certificado, sí.
        secure=settings.session_cookie_secure,
        path="/",
    )
    await hub.publish(
        presence_topic(tenant.slug),
        {"type": "agent_online", "agent_id": str(agent.id), "agent": agent.label},
    )


@router.post("/logout")
async def logout(
    request: Request, response: Response, session: SessionDep, principal: PrincipalDep
) -> dict[str, str]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await repo.close_agent_session(session, hash_token(token))
    if principal.agent is not None:
        await repo.touch_agent_presence(session, principal.agent.id, "offline")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/sso")
async def sso_status(settings: SettingsDep) -> dict[str, bool]:
    """Le dice a la pantalla de acceso qué botones de SSO mostrar.

    Sin autenticar a propósito: hace falta saberlo antes de iniciar sesión.
    """
    return {"available": settings.saml_enabled, "google_available": settings.google_enabled}


@router.get("/me")
async def whoami(principal: PrincipalDep) -> dict[str, Any]:
    """Identidad efectiva de la petición, usada por la consola al cargar.

    ``department_ids`` es ``None`` cuando no hay acotación (administración y
    la clave de servicio); si no, la consola lo usa para no ofrecer, por
    ejemplo, un departamento en el filtro que quien pregunta no atiende.
    """
    department_ids = principal.department_ids
    ids_out = [str(d) for d in department_ids] if department_ids is not None else None
    if principal.agent is None:
        return {
            "agent": None,
            "role": principal.role,
            "via": principal.via,
            "is_supervisor": principal.is_supervisor,
            "department_ids": ids_out,
        }
    return {
        "agent": _serialize(principal.agent),
        "role": principal.role,
        "via": principal.via,
        "is_supervisor": principal.is_supervisor,
        "department_ids": ids_out,
    }


@router.post("/presence")
async def set_presence(
    body: dict[str, str], session: SessionDep, principal: PrincipalDep
) -> dict[str, str]:
    """Declara disponibilidad: ``available``, ``away`` u ``offline``."""
    presence = body.get("presence", "available")
    if presence not in {"available", "away", "offline"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Presencia no válida"
        )
    if principal.agent is not None:
        await repo.touch_agent_presence(session, principal.agent.id, presence)
    return {"status": "ok", "presence": presence}


def _serialize(agent: Any) -> dict[str, Any]:
    return {
        "id": str(agent.id),
        "email": agent.email,
        "display_name": agent.display_name,
        "role": agent.role,
        "is_supervisor": agent.is_supervisor,
        "presence": agent.presence,
    }


def generate_temporary_password(length: int = 12) -> str:
    """Contraseña inicial para un agente recién creado."""
    return secrets.token_urlsafe(length)[:length]
