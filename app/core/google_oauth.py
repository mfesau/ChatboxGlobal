"""Inicio de sesión único con Google OAuth 2.0, para el equipo de la consola.

Alternativa personal a SAML/Entra ID (pensada para quien ya tiene una cuenta
de Gmail o Google Workspace y no cuenta con un proveedor SAML propio):
mientras falten ``GOOGLE_CLIENT_ID`` y ``GOOGLE_CLIENT_SECRET``,
``Settings.google_enabled`` es falso y las rutas de ``app/api/google_auth.py``
responden 404, igual que ocurre con SAML cuando el IdP no está configurado.
"""

from __future__ import annotations

import httpx

from app.config import Settings

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleAuthError(Exception):
    """Google rechazó el código de autorización o no confirmó el correo."""


def build_authorization_url(*, settings: Settings, redirect_uri: str, state: str) -> str:
    """URL a la que se redirige al navegador para iniciar el consentimiento."""
    url = httpx.URL(
        AUTHORIZATION_ENDPOINT,
        params={
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        },
    )
    return str(url)


async def exchange_code_for_userinfo(
    *, code: str, redirect_uri: str, settings: Settings
) -> dict[str, str]:
    """Cambia el código por un token y devuelve el perfil verificado.

    Une el intercambio del código y la consulta del perfil en una sola
    función porque quien la usa (``app/api/google_auth.py``) solo necesita el
    resultado final: un correo que Google confirma como verificado.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_response = await client.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret.get_secret_value(),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_response.status_code != 200:
            raise GoogleAuthError(f"Google rechazó el código: {token_response.text[:200]}")
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise GoogleAuthError("Google no devolvió un token de acceso")

        userinfo_response = await client.get(
            USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
        )
        if userinfo_response.status_code != 200:
            raise GoogleAuthError("No se pudo obtener el perfil de Google")
        payload = userinfo_response.json()

    if not payload.get("email") or not payload.get("email_verified"):
        raise GoogleAuthError("Google no confirma un correo verificado para esta cuenta")
    return {
        "email": str(payload["email"]).strip().lower(),
        "display_name": payload.get("name") or payload["email"],
    }
