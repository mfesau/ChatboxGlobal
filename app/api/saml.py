"""Inicio de sesión único (SAML 2.0) de la consola del equipo.

Convive con `app/api/auth.py`: mientras el proveedor de identidad no esté
configurado (``Settings.saml_enabled`` en falso), estas tres rutas responden
404 y el resto de la consola sigue funcionando exactamente igual.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.auth import issue_session
from app.api.deps import SessionDep, SettingsDep
from app.config import Settings
from app.core.saml import (
    DISPLAY_NAME_CLAIMS,
    AgentInactiveError,
    build_auth,
    extract_claim,
    extract_email,
    login_or_provision_agent,
)
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/saml", tags=["sso"])


def _require_saml(settings: Settings) -> None:
    if not settings.saml_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El inicio de sesión único no está configurado",
        )


def _safe_relay(value: str | None) -> str:
    """Solo se permite volver a una ruta propia; nunca a otro sitio.

    ``next`` viaja en la URL de ida al IdP: sin este filtro, alguien podría
    fabricar un enlace que, tras iniciar sesión de verdad, redirija al
    navegador a un dominio ajeno.
    """
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/console"


@router.get("/metadata", include_in_schema=False)
async def metadata(request: Request, settings: SettingsDep) -> Response:
    """Metadatos del proveedor de servicio (SP), para darlos de alta en el IdP."""
    _require_saml(settings)
    auth = await build_auth(request, settings)
    xml = auth.get_settings().get_sp_metadata()
    return Response(content=xml, media_type="application/xml")


@router.get("/login", include_in_schema=False)
async def login(
    request: Request, settings: SettingsDep, next: str | None = None
) -> RedirectResponse:
    """Arranca el inicio de sesión: redirige al IdP con la solicitud SAML."""
    _require_saml(settings)
    auth = await build_auth(request, settings)
    redirect_url = auth.login(return_to=_safe_relay(next))
    return RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)


@router.post("/acs", include_in_schema=False)
async def assertion_consumer_service(
    request: Request, session: SessionDep, settings: SettingsDep
) -> RedirectResponse:
    """Recibe la aserción del IdP, valida su firma y abre la sesión."""
    _require_saml(settings)
    auth = await build_auth(request, settings)
    auth.process_response()

    errors = auth.get_errors()
    if errors:
        log.warning("saml_acs_rejected", errors=errors, reason=auth.get_last_error_reason())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se pudo validar la respuesta del proveedor de identidad",
        )
    if not auth.is_authenticated():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Autenticación rechazada"
        )

    attributes = auth.get_attributes()
    email = extract_email(auth, attributes)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El proveedor de identidad no envió un correo utilizable",
        )
    display_name = extract_claim(attributes, DISPLAY_NAME_CLAIMS)

    tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
    try:
        agent, is_new = await login_or_provision_agent(
            session, tenant_id=tenant.id, email=email, display_name=display_name
        )
    except AgentInactiveError as exc:
        log.warning("saml_login_rejected_inactive", email=email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="La cuenta está desactivada"
        ) from exc

    # El enlace de retorno viaja como `RelayState` en el mismo POST del IdP
    # (encuadre HTTP-POST): es el valor que `login()` fijó al enviar a la
    # persona al proveedor de identidad.
    form = await request.form()
    target = RedirectResponse(
        _safe_relay(str(form.get("RelayState") or "")), status_code=status.HTTP_303_SEE_OTHER
    )
    # La cookie va sobre la respuesta que se devuelve, no sobre la que inyecta
    # FastAPI: al devolver un `Response` propio, FastAPI descarta las cabeceras
    # de la inyectada y la sesión se perdía por el camino.
    await issue_session(
        agent=agent, tenant=tenant, request=request, response=target,
        session=session, settings=settings,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant.id,
        actor=f"agent:{agent.email}",
        action="agent_created" if is_new else "sso_login",
        subject_type="agent",
        subject_id=str(agent.id),
        detail={"via": "saml"} if is_new else None,
    )
    log.info("saml_login_ok", agent=agent.email, role=agent.role, provisioned=is_new)

    return target
