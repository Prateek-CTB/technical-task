"""Liveness and readiness."""

from __future__ import annotations

import logging
from typing import Literal

from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import Engine, text

from reviewhub import __version__
from reviewhub.persistence.migrate import MIGRATIONS_DIR, current_revision

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


class DatabaseHealth(BaseModel):
    ok: bool
    dialect: str
    revision: str | None
    expected_revision: str | None
    schema_current: bool
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: DatabaseHealth


def _expected_revision() -> str | None:
    script = ScriptDirectory(str(MIGRATIONS_DIR))
    return script.get_current_head()


def _check_database(engine: Engine) -> DatabaseHealth:
    expected = _expected_revision()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        revision = current_revision(engine)
    except Exception as exc:
        log.warning("database health check failed", extra={"error": type(exc).__name__})
        # Don't leak connection strings or driver messages to clients.
        return DatabaseHealth(
            ok=False,
            dialect=engine.dialect.name,
            revision=None,
            expected_revision=expected,
            schema_current=False,
            error=type(exc).__name__,
        )
    return DatabaseHealth(
        ok=True,
        dialect=engine.dialect.name,
        revision=revision,
        expected_revision=expected,
        schema_current=revision == expected,
    )


@router.get("/health", response_model=HealthResponse)
def health(request: Request, response: Response) -> HealthResponse:
    db = _check_database(request.app.state.engine)
    healthy = db.ok and db.schema_current
    if not healthy:
        response.status_code = 503
    return HealthResponse(status="ok" if healthy else "degraded", version=__version__, database=db)
