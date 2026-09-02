"""Inicio de sesión único con Google, alternativa personal al SSO SAML.

Reutiliza el mismo aprovisionamiento «justo a tiempo» que SAML
(``app/core/saml.py``: cuenta nueva con el rol básico de agente, nunca con
más permisos) para no duplicar esa política en dos sitios.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.auth import issue_session
from app.api.deps import SessionDep, SettingsDep
from app.config import Settings
from app.core.google_oauth import (
    GoogleAuthError,
    build_authorization_url,
    exchange_code_for_userinfo,
)
from app.core.saml import AgentInactiveError, login_or_provision_agent
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/auth/google", tags=["sso google"])

#: Cookie de corta vida, propia de la ida y vuelta a Google; no es la sesión.
STATE_COOKIE = "chatbox_google_state"
STATE_TTL_S = 600


def _require_google(settings: Settings) -> None:
    if not settings.google_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El inicio de sesión con Google no está configurado",
        )


def _safe_relay(value: str | None) -> str:
    """Solo se permite volver a una ruta propia; nunca a otro sitio.

    Mismo filtro que ``app/api/saml.py``: sin él, alguien podría fabricar un
    enlace que, tras iniciar sesión de verdad, redirija el navegador a un
    dominio ajeno.
    """
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/console"


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.public_base_url}/auth/google/callback"


@router.get("/login", include_in_schema=False)
async def login(
    response: Response, settings: SettingsDep, next: str | None = None
) -> RedirectResponse:
    """Arranca el inicio de sesión: redirige a Google con la solicitud OAuth."""
    _require_google(settings)
    state = secrets.token_urlsafe(24)
    redirect = RedirectResponse(
        build_authorization_url(settings=settings, redirect_uri=_redirect_uri(settings), state=state),
        status_code=status.HTTP_302_FOUND,
    )
    # El estado viaja también en una cookie propia de esta visita: al volver,
    # compararlo con el que envía Google confirma que la respuesta corresponde
    # a esta solicitud y no a un enlace fabricado por un tercero (CSRF).
    redirect.set_cookie(
        STATE_COOKIE,
        f"{state}:{_safe_relay(next)}",
        max_age=STATE_TTL_S,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/auth/google",
    )
    return redirect


@router.get("/callback", include_in_schema=False)
async def callback(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Recibe el código de Google, valida el estado y abre la sesión."""
    _require_google(settings)
    if error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Acceso cancelado")

    cookie_value = request.cookies.get(STATE_COOKIE) or ""
    expected_state, _, relay = cookie_value.partition(":")
    if (
        not code
        or not state
        or not expected_state
        or not secrets.compare_digest(state, expected_state)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Solicitud de acceso no válida o expirada",
        )

    try:
        profile = await exchange_code_for_userinfo(
            code=code, redirect_uri=_redirect_uri(settings), settings=settings
        )
    except GoogleAuthError as exc:
        log.warning("google_login_rejected", error=str(exc))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    try:
        agent, is_new = await login_or_provision_agent(
            session,
            tenant_id=tenant.id,
            email=profile["email"],
            display_name=profile["display_name"],
        )
    except AgentInactiveError as exc:
        log.warning("google_login_rejected_inactive", email=profile["email"])
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="La cuenta está desactivada"
        ) from exc

    await issue_session(
        agent=agent, tenant=tenant, request=request, response=response,
        session=session, settings=settings,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant.id,
        actor=f"agent:{agent.email}",
        action="agent_created" if is_new else "google_login",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={"via": "google"} if is_new else None,
    )
    log.info("google_login_ok", agent=agent.email, role=agent.role, provisioned=is_new)

    target = RedirectResponse(_safe_relay(relay), status_code=status.HTTP_303_SEE_OTHER)
    target.delete_cookie(STATE_COOKIE, path="/auth/google")
    return target
