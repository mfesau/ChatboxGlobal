"""Motor y sesiones asíncronas de SQLAlchemy."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def build_engine(settings: Settings | None = None) -> AsyncEngine:
    """Crea el motor. En Postgres fija un ``statement_timeout`` por conexión."""
    settings = settings or get_settings()
    kwargs: dict[str, object] = {
        "echo": settings.db_echo,
        "pool_pre_ping": True,
        "future": True,
    }
    if settings.is_sqlite:
        # Espera en lugar de fallar de inmediato cuando otra conexión escribe.
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs |= {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "connect_args": {
                "server_settings": {
                    "application_name": settings.app_name,
                    "statement_timeout": str(settings.db_statement_timeout_ms),
                }
            },
        }

    engine = create_async_engine(settings.database_url, **kwargs)
    if settings.is_sqlite:
        _apply_sqlite_pragmas(engine)
    return engine


def _apply_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Activa WAL y las claves ajenas en cada conexión SQLite.

    Sin WAL, un único escritor bloquea a todos los lectores y las peticiones
    concurrentes fallan con «database is locked». Las claves ajenas están
    desactivadas por omisión en SQLite, de modo que sin este ajuste las pruebas
    no comprobarían las mismas restricciones que impone PostgreSQL.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # pragma: no cover - nivel de driver
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def init_engine(settings: Settings | None = None) -> AsyncEngine:
    """Inicializa el motor global de forma idempotente."""
    global _engine, _session_factory
    if _engine is None:
        _engine = build_engine(settings)
        _session_factory = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        init_engine()
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    """Cierra el pool. Se invoca durante el apagado ordenado."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Unidad de trabajo: confirma al salir y revierte ante cualquier error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI."""
    async with session_scope() as session:
        yield session
