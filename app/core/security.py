"""Credenciales locales de los agentes.

El servicio está pensado para la red interna, sin proveedor de identidad
externo, de modo que la autenticación se resuelve aquí. Se emplea ``scrypt`` de
la biblioteca estándar: resistente a hardware especializado y sin añadir
dependencias nativas al despliegue.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

#: Parámetros de coste. ``n`` es el factor dominante; duplicarlo duplica el
#: tiempo y la memoria. 2**14 mantiene la verificación por debajo de ~100 ms en
#: hardware corriente, suficiente para una consola interna.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
#: Longitud del token de sesión en bytes antes de codificar.
TOKEN_BYTES = 32
MIN_PASSWORD_LENGTH = 8


class WeakPasswordError(ValueError):
    """La contraseña no alcanza la longitud mínima exigida."""


def hash_password(password: str) -> str:
    """Devuelve ``scrypt$n$r$p$salt$clave``, con sal propia por contraseña."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
        )
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            _encode(salt),
            _encode(key),
        ]
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Comprueba la contraseña en tiempo constante.

    Devuelve ``False`` ante un agente sin contraseña fijada o con una derivación
    ilegible, en lugar de propagar la excepción: quien llama solo necesita saber
    si la credencial es válida.
    """
    if not stored:
        return False
    try:
        algorithm, n, r, p, salt, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(),
            salt=_decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_decode(expected)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _decode(expected))


def needs_rehash(stored: str | None) -> bool:
    """Indica si la derivación usa parámetros por debajo de los actuales."""
    if not stored:
        return False
    try:
        algorithm, n, r, p, _salt, _key = stored.split("$")
    except ValueError:
        return True
    return algorithm != "scrypt" or (int(n), int(r), int(p)) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)


def new_session_token() -> str:
    """Token de sesión opaco, apto para viajar en una cookie."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Resumen del token de sesión.

    En la base de datos solo se guarda este resumen: una lectura de la tabla no
    permite suplantar sesiones activas.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
