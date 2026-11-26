"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from reviewhub import __version__
from reviewhub.core.errors import install_error_handlers
from reviewhub.core.logging import configure_logging
from reviewhub.core.settings import Settings, get_settings
from reviewhub.persistence.session import create_db_engine, create_session_factory

from .v1 import analysis, analytics, collection, health, ingestion


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    owns_engine = engine is None
    engine = engine or create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_engine:
            engine.dispose()

    app = FastAPI(
        title="ReviewHub API",
        version=__version__,
        description="Review insights for Azzurro Hotels.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    install_error_handlers(app)
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(ingestion.router, prefix="/api/v1")
    app.include_router(analytics.router, prefix="/api/v1")
    app.include_router(analysis.router, prefix="/api/v1")
    app.include_router(collection.router, prefix="/api/v1")
    return app


def create_app_from_env() -> FastAPI:
    """Uvicorn factory entry point: ``uvicorn reviewhub.api.app:create_app_from_env --factory``."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    return create_app(settings)
