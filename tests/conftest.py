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

# Aislamiento del `.env` del desarrollador: las variables de entorno tienen
# precedencia sobre el fichero, y la configuración descarta los valores vacíos.
# Sin esto, una credencial presente en el entorno local alteraría el resultado
# de las pruebas y estas dejarían de ser reproducibles.
for _isolated in (
    "ADMIN_API_KEY",
    "ANTHROPIC_API_KEY",
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
