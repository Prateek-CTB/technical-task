"""Shared fixtures: every database test runs on SQLite and on PostgreSQL.

PostgreSQL comes from ``REVIEWHUB_TEST_PG_URL`` (CI service container) or, when
that is unset, from an embedded server started for the session. Set
``REVIEWHUB_REQUIRE_POSTGRES=1`` (CI does) to fail instead of skip when no
PostgreSQL is available, so a green run can never silently mean "SQLite only".
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from reviewhub.persistence import migrate
from reviewhub.persistence.session import create_db_engine, create_session_factory


def _to_sqlalchemy_url(url: str) -> str:
    return (
        url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql://")
        else url
    )


@pytest.fixture(scope="session")
def pg_admin_url() -> Iterator[str]:
    """libpq-style URL of an administrative PostgreSQL connection."""
    external = os.environ.get("REVIEWHUB_TEST_PG_URL")
    if external:
        yield external.replace("postgresql+psycopg://", "postgresql://", 1)
        return
    try:
        import pixeltable_pgserver
    except ImportError:
        msg = "PostgreSQL unavailable: set REVIEWHUB_TEST_PG_URL or install pixeltable-pgserver"
        if os.environ.get("REVIEWHUB_REQUIRE_POSTGRES") == "1":
            pytest.fail(msg)
        pytest.skip(msg)
    data_dir = tempfile.mkdtemp(prefix="reviewhub-pg-")
    server = pixeltable_pgserver.get_server(Path(data_dir), cleanup_mode="stop")
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@contextmanager
def _fresh_database(kind: str, tmp_path: Path, admin: str | None, label: str) -> Iterator[str]:
    """An empty database on the requested engine; dropped on exit."""
    if kind == "sqlite":
        yield f"sqlite:///{(tmp_path / f'{label}.db').as_posix()}"
        return
    assert admin is not None
    name = f"rh_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = (
        make_url(_to_sqlalchemy_url(admin)).set(database=name).render_as_string(hide_password=False)
    )
    try:
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    """A fresh, empty database URL on each engine."""
    admin = request.getfixturevalue("pg_admin_url") if request.param == "postgres" else None
    with _fresh_database(request.param, tmp_path, admin, "main") as url:
        yield url


@pytest.fixture
def second_db_url(request: pytest.FixtureRequest, db_url: str, tmp_path: Path) -> Iterator[str]:
    """A second empty database on the *same* engine as ``db_url``."""
    kind = "sqlite" if db_url.startswith("sqlite") else "postgres"
    admin = request.getfixturevalue("pg_admin_url") if kind == "postgres" else None
    with _fresh_database(kind, tmp_path, admin, "second") as url:
        yield url


@pytest.fixture
def dialect(db_url: str) -> str:
    return make_url(db_url).get_backend_name()


@pytest.fixture
def raw_engine(db_url: str) -> Iterator[Engine]:
    """Engine on an *empty* database (no migrations applied)."""
    engine = create_db_engine(db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    """Engine on a database migrated to head."""
    migrate.upgrade(db_url)
    engine = create_db_engine(db_url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with create_session_factory(engine)() as s:
        yield s
