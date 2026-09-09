"""Descubrimiento de migraciones y contrato del aplicador."""

from __future__ import annotations

from app.db import migrate
from app.db.engine import build_engine


def test_discover_returns_only_sql_in_name_order() -> None:
    files = migrate.discover()

    assert files, "el directorio db/migrations debe existir en el repositorio"
    assert all(path.suffix == ".sql" for path in files)
    assert [path.name for path in files] == sorted(path.name for path in files)
    # El script de restauración vive en la misma carpeta y no es una migración.
    assert not any(path.name.endswith(".sh") for path in files)


def test_legacy_baseline_is_one_of_the_files() -> None:
    """El corte histórico debe apuntar a un fichero real, o no cortaría nada."""
    assert migrate.LEGACY_BASELINE in {path.name for path in migrate.discover()}


async def test_apply_pending_is_a_no_op_on_sqlite(settings) -> None:
    """Las pruebas montan el esquema con ``create_all``; este SQL es de Postgres."""
    engine = build_engine(settings)
    try:
        assert await migrate.apply_pending(engine) == []
    finally:
        await engine.dispose()
