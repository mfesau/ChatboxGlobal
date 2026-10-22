"""Servidor de verdad para las pruebas de navegador.

Las pruebas del chatbox en el navegador necesitan un proceso propio: Playwright
habla con un puerto real, con su WebSocket. Se levanta sobre una base SQLite
temporal y desde una carpeta vacía, para que no lea el ``.env`` de la
instalación —que apunta a producción— ni toque sus datos.

``python -m tests.e2e.server prepare`` crea el esquema, una administradora y un
departamento; lo llama el fixture antes de arrancar uvicorn.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
ADMIN_EMAIL = "admin.e2e@empresa.local"
ADMIN_PASSWORD = "clave-de-prueba-e2e"  # noqa: S105  solo existe en la base temporal
DEPARTMENT = "Hotel"


@dataclass
class Server:
    base_url: str
    process: subprocess.Popen


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _environment(workdir: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        # Nada de lo que la sesión tenga de la instalación real.
        if not key.endswith(("_KEY", "_SECRET", "_TOKEN", "_PASSWORD"))
    }
    env.update(
        {
            "PYTHONPATH": str(REPO),
            "DATABASE_URL": f"sqlite+aiosqlite:///{(workdir / 'e2e.sqlite3').as_posix()}",
            "ENVIRONMENT": "dev",
            "SESSION_COOKIE_SECURE": "false",
            "UPLOADS_DIR": str(workdir / "uploads"),
            "IMAP_POLL_INTERVAL_S": "0",
            "LOG_LEVEL": "WARNING",
        }
    )
    return env


def start(workdir: Path) -> Server:
    env = _environment(workdir)
    subprocess.run(  # noqa: S603  intérprete y módulo propios
        [sys.executable, "-m", "tests.e2e.server", "prepare"],
        cwd=workdir,
        env=env,
        check=True,
    )
    port = _free_port()
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=workdir,
        env=env,
    )
    base_url = f"http://127.0.0.1:{port}"
    limite = time.monotonic() + 30
    while time.monotonic() < limite:
        if process.poll() is not None:
            raise RuntimeError("El servidor de pruebas no arrancó")
        try:
            if httpx.get(f"{base_url}/health", timeout=1).status_code == 200:
                return Server(base_url=base_url, process=process)
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    process.terminate()
    raise RuntimeError("El servidor de pruebas no respondió a tiempo")


def stop(server: Server) -> None:
    server.process.terminate()
    try:
        server.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.process.kill()


async def _prepare() -> None:
    from app.config import get_settings
    from app.core.security import hash_password
    from app.db import repositories as repo
    from app.db.engine import build_engine, dispose_engine, init_engine, session_scope
    from app.db.models import Base

    settings = get_settings()
    engine = build_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    init_engine(settings)
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
        await repo.create_agent(
            session,
            tenant_id=tenant.id,
            email=ADMIN_EMAIL,
            display_name="Administración E2E",
            role="admin",
            password_hash=hash_password(ADMIN_PASSWORD),
        )
        await repo.create_department(session, tenant_id=tenant.id, name=DEPARTMENT)
    await dispose_engine()


if __name__ == "__main__" and sys.argv[1:] == ["prepare"]:
    asyncio.run(_prepare())
