"""Punto de entrada ASGI.

Ejecución en desarrollo::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import __version__
from app.api import (
    attachments,
    auth,
    console,
    contact_auth,
    google_auth,
    saml,
    session,
    webhooks,
    ws,
)
from app.channels.base import ChannelRegistry
from app.config import get_settings
from app.core.branding import brand_css, read_accent
from app.core.dispatcher import OutboxDispatcher
from app.core.orchestrator import Orchestrator
from app.db import repositories as repo
from app.db.engine import dispose_engine, init_engine, session_scope
from app.handlers import build_default_pipeline
from app.logging_setup import configure_logging, get_logger

# Importación con efecto lateral: inscribe los adaptadores en el registro.
import app.channels  # noqa: F401  isort:skip

WEB_DIR = Path(__file__).parent / "web"
log = get_logger(__name__)


def asset_version() -> str:
    """Huella de los estáticos, derivada de su fecha de modificación.

    Se añade como parámetro a los enlaces de CSS y JavaScript. Una versión fija
    escrita a mano no sirve: al cambiar un fichero sin tocarla, el navegador
    seguiría sirviendo la copia antigua de su caché y el equipo trabajaría con
    una consola desactualizada.
    """
    static = WEB_DIR / "static"
    if not static.is_dir():
        return __version__
    stamps = (path.stat().st_mtime_ns for path in static.iterdir() if path.is_file())
    return f"{max(stamps, default=0):x}"


@lru_cache(maxsize=16)
def render_page(name: str) -> str:
    """Sirve una plantilla con la huella de los estáticos sustituida."""
    return (
        (WEB_DIR / name)
        .read_text(encoding="utf-8")
        .replace("__ASSET_VERSION__", asset_version())
    )


async def render_branded_page(name: str) -> str:
    """Sirve una plantilla con el color de marca del inquilino ya dentro.

    La plantilla en si esta cacheada; lo que se consulta en cada peticion es el
    color, con una lectura de una fila. Cachearlo tambien obligaria a invalidar
    la cache al guardarlo desde la consola, y el ahorro no lo justifica: estas
    paginas se piden una vez por pestaña abierta, no una vez por mensaje.
    """
    settings = get_settings()
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
        accent = read_accent(tenant.settings)
    return render_page(name).replace("__BRAND_STYLE__", brand_css(accent))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque y apagado ordenados de los recursos de larga vida."""
    settings = get_settings()
    configure_logging(settings.log_level)

    init_engine(settings)
    async with session_scope() as session:
        tenant = await repo.get_or_create_tenant(session, settings.default_tenant_slug)
        await repo.ensure_default_admin(session, tenant_id=tenant.id)

    registry = ChannelRegistry(settings)
    pipeline = build_default_pipeline(settings)
    orchestrator = Orchestrator(settings=settings, registry=registry, pipeline=pipeline)
    dispatcher = OutboxDispatcher(settings=settings, registry=registry)
    orchestrator.on_enqueued = dispatcher.kick

    app.state.settings = settings
    app.state.registry = registry
    app.state.pipeline = pipeline
    app.state.orchestrator = orchestrator
    app.state.dispatcher = dispatcher

    await dispatcher.start()
    log.info(
        "application_started",
        environment=settings.environment,
        channels=[str(kind) for kind in registry.available],
    )
    try:
        yield
    finally:
        await dispatcher.stop()
        await registry.aclose()
        await dispose_engine()
        log.info("application_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        summary=(
            "Capa central de orquestación para WhatsApp Cloud API, Microsoft Bot "
            "Framework y el chatbox web propio."
        ),
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(webhooks.router)
    application.include_router(ws.router)
    application.include_router(auth.router)
    application.include_router(contact_auth.router)
    application.include_router(session.router)
    application.include_router(saml.router)
    application.include_router(google_auth.router)
    application.include_router(console.router)
    application.include_router(attachments.router)

    if WEB_DIR.is_dir():
        application.mount(
            "/static", StaticFiles(directory=WEB_DIR / "static"), name="static"
        )

        @application.get("/", include_in_schema=False)
        async def chatbox_page() -> HTMLResponse:
            return HTMLResponse(await render_branded_page("index.html"))

        @application.get("/console", include_in_schema=False)
        async def console_page() -> HTMLResponse:
            """Entrada única para agentes, supervisión y administración.

            Una sola página que se adapta según el rol de la sesión, ya
            autenticada: no hace falta elegir de antemano cuál URL visitar.
            """
            return HTMLResponse(await render_branded_page("console.html"))

        @application.get("/privacy", include_in_schema=False)
        async def privacy_page() -> HTMLResponse:
            """Política de privacidad exigida por Meta para el registro de la app."""
            return HTMLResponse(render_page("privacy.html"))

    # Los adjuntos NO se sirven como estáticos: cada descarga comprueba que
    # quien la pide puede ver la conversación que contiene el fichero.
    Path(settings.uploads_dir).mkdir(parents=True, exist_ok=True)

    @application.get("/health", tags=["operación"])
    async def health() -> dict[str, Any]:
        """Comprobación superficial: el proceso responde."""
        return {
            "status": "ok",
            "environment": settings.environment,
            "app": settings.app_name,
            "version": __version__,
        }

    @application.get("/health/ready", tags=["operación"])
    async def readiness() -> JSONResponse:
        """Comprobación profunda: la base de datos acepta consultas."""
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:
            return JSONResponse(
                status_code=503, content={"status": "degraded", "database": str(exc)[:200]}
            )
        return JSONResponse(content={"status": "ready", "database": "ok"})

    return application


app = create_app()
