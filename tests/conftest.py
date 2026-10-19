"""Configuración de la batería de pruebas.

Las pruebas corren sobre SQLite mediante ``aiosqlite``: los modelos declaran
variantes portables, de modo que la lógica de dominio se verifica sin desplegar
un servidor PostgreSQL. Las consultas específicas de Postgres (``SKIP LOCKED``)
se activan por dialecto y quedan cubiertas por su rama alternativa.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

# El entorno se fija antes de importar la aplicación: ``get_settings`` almacena
# su resultado en caché tras la primera llamada.
_DB_FILE = Path(tempfile.mkdtemp(prefix="chatbox-tests-")) / "test.sqlite3"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_FILE.as_posix()}"
os.environ["ENVIRONMENT"] = "dev"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["OUTBOX_WORKERS"] = "1"
# El recogedor de correo no debe salir a la red durante las pruebas.
os.environ["IMAP_POLL_INTERVAL_S"] = "0"
# El transporte ASGI de las pruebas habla `http`, así que una cookie marcada
# `Secure` no se guardaría y ninguna sesión sobreviviría a la primera petición.
# Se fija aquí y no se hereda del `.env`: una instalación con TLS delante lo
# tiene en `true`, y eso no debe cambiar el resultado de la batería.
os.environ["SESSION_COOKIE_SECURE"] = "false"

# Aislamiento del `.env` del desarrollador: las variables de entorno tienen
# precedencia sobre el fichero, y la configuración descarta los valores vacíos.
# Sin esto, una credencial presente en el entorno local alteraría el resultado
# de las pruebas y estas dejarían de ser reproducibles.
for _isolated in (
    "ADMIN_API_KEY",
    # Al pasar el asistente de Claude a Gemini se cambió la clave en la
    # aplicación pero no aquí, de modo que la del entorno del desarrollador
    # llegaba entera a las pruebas: la base de conocimiento salía a la red de
    # verdad, con su factura.
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "AI_PROVIDER",
    "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "MICROSOFT_APP_ID",
    "MICROSOFT_APP_PASSWORD",
    "MICROSOFT_APP_TENANT_ID",
    "SAML_IDP_ENTITY_ID",
    "SAML_IDP_SSO_URL",
    "SAML_IDP_X509_CERT",
    "SAML_SP_ENTITY_ID",
    "FACEBOOK_APP_SECRET",
    "FACEBOOK_VERIFY_TOKEN",
    "SECRET_ENCRYPTION_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
):
    os.environ[_isolated] = ""

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import delete  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.engine import build_engine, dispose_engine, init_engine, session_scope  # noqa: E402
from app.db.models import Base  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest_asyncio.fixture(autouse=True)
async def database(settings) -> AsyncIterator[None]:
    """Esquema limpio en cada prueba: aísla el estado sin recrear el fichero."""
    engine = build_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    init_engine(settings)
    try:
        yield
    finally:
        async with session_scope() as session:
            for table in reversed(Base.metadata.sorted_tables):
                await session.execute(delete(table))
        await dispose_engine()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with session_scope() as active:
        yield active


# El andamiaje de ``tests/test_teamwork.py`` —la aplicación con la cadena de
# eco, el equipo y la fábrica de clientes autenticados— lo comparten otros
# módulos de pruebas. Se re-exporta aquí para que pytest lo encuentre sin que
# cada módulo tenga que importarlo: importado a mano, cada uso quedaría como
# una redefinición a ojos del analizador estático.
#
# La importación va al final a propósito: ese módulo importa la aplicación, y
# el entorno de arriba tiene que estar fijado antes.
from tests.test_teamwork import (  # noqa: E402, F401  fixtures compartidas
    anonymous,
    app,
    as_agent,
    team,
)
