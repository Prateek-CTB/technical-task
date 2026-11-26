"""API foundation tests: health, error format, CLI."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

import reviewhub
from reviewhub import cli
from reviewhub.api.app import create_app
from reviewhub.core.settings import Settings, get_settings
from reviewhub.persistence import migrate
from reviewhub.persistence.session import create_db_engine


def settings_for(url: str) -> Settings:
    return Settings(_env_file=None, database_url=url)  # type: ignore[call-arg]


def test_health_ok_on_migrated_database(db_url: str, engine: Engine, dialect: str) -> None:
    with TestClient(create_app(settings_for(db_url), engine)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == reviewhub.__version__
    assert body["database"] == {
        "ok": True,
        "dialect": dialect,
        "revision": "0002",
        "expected_revision": "0002",
        "schema_current": True,
        "error": None,
    }


def test_health_degraded_when_schema_not_migrated(db_url: str, raw_engine: Engine) -> None:
    with TestClient(create_app(settings_for(db_url), raw_engine)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["ok"] is True
    assert body["database"]["schema_current"] is False


def test_health_degraded_when_database_unreachable() -> None:
    # Nothing listens on this port; the response must not leak the URL or password.
    url = "postgresql+psycopg://user:s3cret@127.0.0.1:1/nodb?connect_timeout=1"
    engine = create_db_engine(url)
    with TestClient(create_app(settings_for(url), engine)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"]["ok"] is False
    assert "s3cret" not in response.text
    assert "127.0.0.1" not in response.text


def test_errors_are_problem_json(db_url: str, engine: Engine) -> None:
    app = create_app(settings_for(db_url), engine)

    @app.get("/api/v1/_probe/{n}")
    def probe(n: int) -> dict[str, int]:
        return {"n": n}

    # A built dashboard registers a catch-all route; keep the probe ahead of it.
    app.router.routes.insert(0, app.router.routes.pop())

    with TestClient(app) as client:
        missing = client.get("/api/v1/nope")
        invalid = client.get("/api/v1/_probe/abc")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["status"] == 404
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/problem+json")
    assert invalid.json()["errors"][0]["loc"] == ["path", "n"]


def test_cli_db_upgrade_and_current(
    db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REVIEWHUB_DATABASE_URL", db_url)
    get_settings.cache_clear()
    try:
        assert cli.main(["db", "current"]) == 0
        assert "no migrations applied" in capsys.readouterr().out
        assert cli.main(["db", "upgrade"]) == 0
        capsys.readouterr()
        assert cli.main(["db", "current"]) == 0
        assert capsys.readouterr().out.strip() == "0002"
        assert cli.main(["db", "downgrade"]) == 0
        capsys.readouterr()
        engine = create_db_engine(db_url)
        assert migrate.current_revision(engine) is None
        engine.dispose()
    finally:
        get_settings.cache_clear()
