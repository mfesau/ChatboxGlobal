"""Crea el esquema y los datos mínimos. Pensado para desarrollo y pruebas.

En producción, aplique ``db/migrations/0001_init.sql`` o ejecute ``alembic upgrade head``.

Uso::

    python scripts/init_db.py
    python scripts/init_db.py --drop      # recrea el esquema desde cero
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import repositories as repo  # noqa: E402
from app.db.engine import build_engine, dispose_engine, init_engine, session_scope  # noqa: E402
from app.db.models import Base  # noqa: E402


async def main(drop: bool) -> None:
    settings = get_settings()
    engine = build_engine(settings)
    async with engine.begin() as connection:
        if drop:
            await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    init_engine(settings)
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
        print(f"Inquilino disponible: {tenant.slug} ({tenant.id})")
    await dispose_engine()
    print(f"Esquema listo en {_redact(settings.database_url)}")


def _redact(dsn: str) -> str:
    """Oculta la contraseña antes de escribir el DSN por consola."""
    if "@" not in dsn or "//" not in dsn:
        return dsn
    scheme, rest = dsn.split("//", 1)
    credentials, host = rest.split("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}//{user}:***@{host}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inicializa el esquema de la base de datos")
    parser.add_argument(
        "--drop", action="store_true", help="Elimina las tablas antes de crearlas"
    )
    args = parser.parse_args()
    asyncio.run(main(args.drop))
