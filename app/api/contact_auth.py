"""Alta y sesión de los clientes del chatbox web.

Calcado de ``app/api/auth.py``: mismas garantías (derivación ``scrypt``,
verificación en tiempo constante, sesión en cookie ``HttpOnly``), pero para la
identidad de quien escribe en el chatbox público en vez de para el equipo.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field, field_validator

from app.api.deps import (
    CONTACT_SESSION_COOKIE,
    CONTACT_SESSION_TTL,
    ContactDep,
    SessionDep,
    SettingsDep,
)
from app.core.security import (
    WeakPasswordError,
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)
from app.core.storage import save_upload
from app.db import repositories as repo
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/contact", tags=["chatbox: cuenta"])

#: Coste de una verificación fallida, para no delatar si el correo existe.
_DUMMY_HASH = hash_password("contraseña-inexistente-de-relleno")
_EXPIRES_IN_S = int(CONTACT_SESSION_TTL.total_seconds())


def _normalise_email(value: str) -> str:
    value = value.strip().lower()
    if "@" not in value:
        raise ValueError("El correo debe incluir el signo @")
    return value


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str | None = Field(default=None, max_length=160)
    password: str = Field(min_length=8, max_length=256)
    tenant: str | None = None

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return _normalise_email(value)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    tenant: str | None = None

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return _normalise_email(value)


def _serialize(contact: Any) -> dict[str, Any]:
    return {
        "id": str(contact.id),
        "email": contact.primary_email,
        "display_name": contact.display_name,
    }


def _set_session_cookie(response: Response, settings: SettingsDep, token: str) -> None:
    response.set_cookie(
        CONTACT_SESSION_COOKIE,
        token,
        max_age=int(CONTACT_SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterIn,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Da de alta una cuenta de cliente y la deja autenticada de inmediato."""
    tenant = await repo.get_or_create_tenant(session, body.tenant or settings.default_tenant_slug)
    if await repo.find_contact_by_email(session, tenant_id=tenant.id, email=body.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una cuenta con ese correo"
        )
    try:
        # La derivación de la contraseña es intencionadamente costosa: se ejecuta
        # en un hilo para no bloquear el bucle de eventos.
        password_hash = await asyncio.to_thread(hash_password, body.password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    contact = await repo.create_contact_account(
        session,
        tenant_id=tenant.id,
        email=body.email,
        display_name=body.display_name,
        password_hash=password_hash,
    )
    token = new_session_token()
    await repo.open_contact_session(
        session,
        contact_id=contact.id,
        token_hash=hash_token(token),
        ttl=CONTACT_SESSION_TTL,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await repo.record_audit(
        session,
        tenant_id=tenant.id,
        actor=f"contact:{contact.primary_email}",
        action="contact_registered",
        subject_type="contact",
        subject_id=str(contact.id),
    )

    _set_session_cookie(response, settings, token)
    log.info("contact_registered", contact=contact.primary_email)
    return {"contact": _serialize(contact), "expires_in_s": _EXPIRES_IN_S}


async def issue_contact_session(
    *,
    contact: Any,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> None:
    """Abre una sesión de cliente y la deja en una cookie ``HttpOnly``.

    Aislada para que el login unificado de ``app/api/session.py`` pueda
    reutilizarla tal cual, sin duplicar la apertura de sesión.
    """
    token = new_session_token()
    await repo.open_contact_session(
        session,
        contact_id=contact.id,
        token_hash=hash_token(token),
        ttl=CONTACT_SESSION_TTL,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, settings, token)


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
    contact = await repo.find_contact_by_email(session, tenant_id=tenant.id, email=body.email)

    stored = contact.password_hash if contact is not None else _DUMMY_HASH
    # Se verifica siempre, incluso sin contacto: así el tiempo de respuesta no
    # revela qué correos están registrados.
    valid = await asyncio.to_thread(verify_password, body.password, stored)

    if contact is None or contact.is_blocked or not valid:
        log.warning("contact_login_rejected", email=body.email, tenant=tenant.slug)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales no válidas"
        )

    await issue_contact_session(
        contact=contact, request=request, response=response, session=session, settings=settings
    )
    log.info("contact_login_ok", contact=contact.primary_email)
    return {"contact": _serialize(contact), "expires_in_s": _EXPIRES_IN_S}


@router.post("/logout")
async def logout(request: Request, response: Response, session: SessionDep) -> dict[str, str]:
    token = request.cookies.get(CONTACT_SESSION_COOKIE)
    if token:
        await repo.close_contact_session(session, hash_token(token))
    response.delete_cookie(CONTACT_SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/me")
async def whoami(contact: ContactDep) -> dict[str, Any]:
    """Identidad efectiva de la petición, usada por el chatbox al cargar."""
    return {"contact": _serialize(contact)}


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile, contact: ContactDep, settings: SettingsDep
) -> dict[str, Any]:
    """Sube una imagen para adjuntarla al siguiente mensaje del chatbox."""
    attachment = await save_upload(file, namespace=str(contact.tenant_id), settings=settings)
    return attachment.to_dict()
