"""Inicio de sesión único (SAML 2.0) de la consola del equipo.

Convive con el login por contraseña de ``app/api/auth.py``: mientras el
proveedor de identidad (IdP) no esté configurado, ``Settings.saml_enabled``
es falso y las rutas de ``app/api/saml.py`` responden 404 sin afectar al
resto de la consola.

La primera vez que alguien entra por SSO con un correo que todavía no tiene
cuenta de agente, se le da de alta automáticamente («aprovisionamiento
just-in-time») con el rol básico de agente — nunca con más permisos, aunque
el IdP lo marque como alguien importante. Quien administre la consola ajusta
el rol después, igual que con cualquier otra cuenta.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import repositories as repo
from app.db.models import ROLE_AGENT, Agent

#: Reclamos habituales del correo según el proveedor. Se prueban en orden;
#: el primero que aparezca en la respuesta del IdP gana.
EMAIL_CLAIMS = (
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "email",
    "mail",
    "upn",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn",
)

#: Igual, pero para el nombre visible.
DISPLAY_NAME_CLAIMS = (
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/displayname",
    "displayname",
    "name",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
)


class AgentInactiveError(Exception):
    """La cuenta existe pero está desactivada; el IdP no reactiva cuentas."""


def build_saml_settings(settings: Settings) -> dict[str, Any]:
    """Ajustes de ``python3-saml`` derivados de la configuración de la app."""
    sp_entity_id = settings.saml_sp_entity_id or f"{settings.public_base_url}/saml/metadata"
    return {
        "strict": True,
        "sp": {
            "entityId": sp_entity_id,
            "assertionConsumerService": {
                "url": f"{settings.public_base_url}/saml/acs",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        },
        "idp": {
            "entityId": settings.saml_idp_entity_id,
            "singleSignOnService": {
                "url": settings.saml_idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": settings.saml_idp_x509_cert,
        },
    }


async def build_request_data(request: Request) -> dict[str, Any]:
    """Traduce una petición de FastAPI al formato que espera ``python3-saml``."""
    form: dict[str, str] = {}
    if request.method == "POST":
        posted = await request.form()
        form = {key: str(value) for key, value in posted.items()}
    return {
        "https": "on" if request.url.scheme == "https" else "off",
        "http_host": request.url.hostname or "",
        "server_port": str(request.url.port or (443 if request.url.scheme == "https" else 80)),
        "script_name": request.url.path,
        "get_data": dict(request.query_params),
        "post_data": form,
    }


async def build_auth(request: Request, settings: Settings) -> OneLogin_Saml2_Auth:
    request_data = await build_request_data(request)
    return OneLogin_Saml2_Auth(request_data, old_settings=build_saml_settings(settings))


def extract_claim(attributes: dict[str, list[str]], names: tuple[str, ...]) -> str | None:
    for name in names:
        values = attributes.get(name)
        if values and values[0]:
            return values[0].strip()
    return None


def extract_email(auth: OneLogin_Saml2_Auth, attributes: dict[str, list[str]]) -> str | None:
    """El correo puede llegar como atributo o como el propio NameID."""
    claimed = extract_claim(attributes, EMAIL_CLAIMS)
    if claimed:
        return claimed
    name_id = auth.get_nameid()
    if name_id and "@" in name_id:
        return name_id.strip()
    return None


async def login_or_provision_agent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    display_name: str | None,
) -> tuple[Agent, bool]:
    """Busca al agente por correo o lo da de alta con el rol básico.

    Devuelve ``(agente, se_acaba_de_crear)``: el segundo valor es explícito a
    propósito, para no tener que adivinarlo después por indicios como la
    presencia o la fecha de último acceso.

    Aislada de ``python3-saml`` a propósito: así se puede probar la política
    de aprovisionamiento (alta automática, sin elevar permisos, cuentas
    desactivadas no reviven) sin necesidad de firmar una aserción SAML real.
    """
    agent = await repo.find_agent_by_email(session, tenant_id=tenant_id, email=email)
    if agent is not None:
        if not agent.is_active:
            raise AgentInactiveError(email)
        return agent, False

    agent = await repo.create_agent(
        session,
        tenant_id=tenant_id,
        email=email,
        display_name=display_name,
        role=ROLE_AGENT,
        password_hash=None,
    )
    return agent, True
