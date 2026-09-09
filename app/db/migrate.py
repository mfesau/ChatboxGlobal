"""Aplicación de las migraciones SQL durante el arranque.

Los ficheros de ``db/migrations`` están montados en el contenedor de PostgreSQL
como ``docker-entrypoint-initdb.d``: solo se ejecutan cuando el directorio de
datos está vacío. En un despliegue ya en marcha —el caso normal, con datos
dentro— una migración nueva no llegaría jamás a la base, y ``alembic`` no cubre
el hueco porque su carpeta de revisiones está vacía. Este módulo las aplica al
levantar el servicio y anota lo aplicado en ``schema_migrations``.

**Convención obligatoria para los ficheros nuevos**: deben ser idempotentes
(``CREATE TABLE IF NOT EXISTS``, ``ADD COLUMN IF NOT EXISTS``…), tal como ya lo
son los de la serie 0002-0014. La razón es que una instalación desde cero los
ejecuta dos veces: primero PostgreSQL al inicializar el directorio de datos, y
después este módulo, que no tiene forma de distinguir esa base recién creada de
una que venía de la serie antigua —en ambas existen las tablas y no existe el
registro—.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.logging_setup import get_logger

log = get_logger(__name__)

#: ``app/db/migrate.py`` → raíz del proyecto (o ``/app`` dentro de la imagen).
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

#: Última migración anterior a este control. Todo lo que llega hasta aquí ya
#: fue aplicado por PostgreSQL al inicializar la base, así que se da por hecho
#: sin volver a ejecutarlo; de la siguiente en adelante, se ejecuta.
LEGACY_BASELINE = "0014_department_logo.sql"

#: Su presencia indica que el esquema ya existe, venga de donde venga.
_SENTINEL_TABLE = "conversations"

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    VARCHAR(255) PRIMARY KEY,
    applied_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
)
"""


def discover() -> list[Path]:
    """Migraciones en orden de nombre.

    Solo ``.sql``: el directorio guarda además utilidades de operación —por
    ejemplo ``9999_restore_latest_backup.sh``— que no son migraciones.
    """
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(
        path for path in MIGRATIONS_DIR.iterdir() if path.is_file() and path.suffix == ".sql"
    )


async def _table_exists(conn: AsyncConnection, name: str) -> bool:
    found = await conn.execute(text("SELECT to_regclass(:name)"), {"name": name})
    return found.scalar() is not None


async def _run_script(conn: AsyncConnection, sql: str) -> None:
    """Ejecuta un fichero completo, con sus varias sentencias.

    Se baja hasta la conexión de ``asyncpg`` a propósito: SQLAlchemy prepara
    cada sentencia, y una sentencia preparada no admite más de una a la vez.
    El protocolo de consulta simple sí, y además respeta el ``BEGIN``/``COMMIT``
    que los propios ficheros traen escrito.
    """
    raw = await conn.get_raw_connection()
    await raw.driver_connection.execute(sql)


async def apply_pending(engine: AsyncEngine) -> list[str]:
    """Aplica las migraciones pendientes. Devuelve los nombres aplicados.

    En SQLite no hace nada: las pruebas levantan el esquema con ``create_all``
    y estos ficheros son SQL de PostgreSQL.
    """
    if engine.dialect.name != "postgresql":
        return []

    files = discover()
    if not files:
        log.warning("migrations_dir_missing", path=str(MIGRATIONS_DIR))
        return []

    # Sin transacción envolvente: cada fichero abre y cierra la suya.
    autocommit = engine.execution_options(isolation_level="AUTOCOMMIT")
    applied: list[str] = []

    async with autocommit.connect() as conn:
        schema_exists = await _table_exists(conn, _SENTINEL_TABLE)
        ledger_exists = await _table_exists(conn, "schema_migrations")
        await _run_script(conn, _LEDGER_DDL)

        if schema_exists and not ledger_exists:
            # Primera vez que se lleva la cuenta sobre una base que ya venía
            # funcionando: se da por aplicada la serie histórica.
            baseline = [path.name for path in files if path.name <= LEGACY_BASELINE]
            if baseline:
                await conn.execute(
                    text("INSERT INTO schema_migrations (filename) VALUES (:filename)"),
                    [{"filename": name} for name in baseline],
                )
                log.info("migrations_baseline_recorded", count=len(baseline))

        done = set(
            (await conn.execute(text("SELECT filename FROM schema_migrations"))).scalars()
        )

        for path in files:
            if path.name in done:
                continue
            log.info("migration_applying", filename=path.name)
            await _run_script(conn, path.read_text(encoding="utf-8"))
            await conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:filename)"),
                {"filename": path.name},
            )
            applied.append(path.name)

    if applied:
        log.info("migrations_applied", filenames=applied)
    return applied
