"""Cifrado reversible de credenciales de terceros.

Distinto de ``app/core/security.py``: las contraseñas y los tokens de sesión
se resumen con un hash de un solo sentido, porque nunca hace falta recuperar
el valor original. Un token de acceso de una página de Facebook o de un
número de WhatsApp, en cambio, hay que volver a leerlo tal cual para llamar a
la API del proveedor — de ahí el cifrado simétrico en vez de un resumen.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class EncryptionNotConfiguredError(RuntimeError):
    """Falta ``SECRET_ENCRYPTION_KEY``; no se puede cifrar ni descifrar."""


class DecryptionError(ValueError):
    """El texto cifrado no es válido para la clave configurada."""


def _fernet(settings: Settings) -> Fernet:
    key = settings.secret_encryption_key
    if key is None:
        raise EncryptionNotConfiguredError(
            "SECRET_ENCRYPTION_KEY no está configurada; no se pueden guardar "
            "credenciales propias por cuenta de canal"
        )
    return Fernet(key.get_secret_value().encode())


def encrypt_json(data: dict[str, Any], *, settings: Settings) -> str:
    """Cifra un objeto JSON pequeño (p. ej. ``{"access_token": "..."}``)."""
    payload = json.dumps(data, separators=(",", ":")).encode()
    return _fernet(settings).encrypt(payload).decode()


def decrypt_json(ciphertext: str, *, settings: Settings) -> dict[str, Any]:
    """Inversa de :func:`encrypt_json`."""
    try:
        payload = _fernet(settings).decrypt(ciphertext.encode())
    except InvalidToken as exc:
        raise DecryptionError(
            "No se pudo descifrar: la clave cambió o el valor está dañado"
        ) from exc
    return json.loads(payload)
