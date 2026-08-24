"""Genera el DDL de PostgreSQL a partir de los modelos declarativos.

Uso::

    python scripts/generate_ddl.py > db/migrations/0001_init.sql

El fichero resultante es la referencia revisable del esquema. Para la evolución
posterior se emplea Alembic (``alembic revision --autogenerate``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from app.db.models import Base  # noqa: E402

HEADER = """-- Esquema inicial del orquestador omnicanal.
-- Generado con: python scripts/generate_ddl.py
-- No editar a mano: modifique app/db/models.py y vuelva a generar.

BEGIN;
"""

FOOTER = """
COMMIT;
"""


def main() -> None:
    dialect = postgresql.dialect()
    parts: list[str] = [HEADER]

    for table in Base.metadata.sorted_tables:
        parts.append(f"\n-- {table.name}")
        parts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + ";")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            parts.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")

    parts.append(FOOTER)
    sys.stdout.write("\n".join(parts))


if __name__ == "__main__":
    main()
