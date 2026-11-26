"""The two ways to run an import: the HTTP API and the command line."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from reviewhub import cli
from reviewhub.api.app import create_app
from reviewhub.core.settings import Settings, get_settings
from reviewhub.persistence.models import Review, Run
from reviewhub.persistence.runs import start_run
from tests import factories as f
from tests.ingest_helpers import count, make_csv, row, run_import


@pytest.fixture
def client(db_url: str, engine: Engine) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, database_url=db_url), engine))  # type: ignore[call-arg]


def upload(client: TestClient, content: bytes, name: str = "reviews.csv", **form: str):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/ingestion/imports", files={"file": (name, content, "text/csv")}, data=form
    )


# ----------------------------------------------------------------------------- API
def test_api_import_returns_the_report(client: TestClient, engine: Engine) -> None:
    resp = upload(client, make_csv([row(1), row(2, rating="x"), row(3)]))
    assert resp.status_code == 200
    body = resp.json()
    assert (body["status"], body["new"], body["rows_rejected"]) == ("partial", 2, 1)
    assert body["issues"][0] == {
        "line": 3,
        "code": "DQ002",
        "severity": "error",
        "column": "rating",
        "property": "chateau-de-venus",
        "message": body["issues"][0]["message"],
    }
    assert body["dataset"]["sources"] == {"csv_import": 2} and count(engine, Review) == 2


def test_api_dry_run_writes_nothing(client: TestClient, engine: Engine) -> None:
    resp = upload(client, make_csv([row(1), row(2)]), dry_run="true")
    assert resp.status_code == 200 and resp.json()["new"] == 2 and resp.json()["dry_run"] is True
    assert count(engine, Review) == 0 and count(engine, Run) == 0


def test_api_rejected_file_is_422_problem_json(client: TestClient, engine: Engine) -> None:
    resp = upload(client, b"title,liked\nx,y\n")
    assert resp.status_code == 422 and resp.headers["content-type"].startswith(
        "application/problem+json"
    )
    body = resp.json()
    assert body["code"] == "missing_columns" and body["report"]["status"] == "failed"
    assert count(engine, Review) == 0


def test_api_same_file_already_importing_is_409(client: TestClient, engine: Engine) -> None:
    content = make_csv([row(1)])
    sha = run_import(engine, content, dry_run=True).file_sha256
    with Session(engine) as s:
        start_run(
            s,
            kind="import",
            source="csv_import",
            scope_key=f"import:{sha}",
            mode="file",
            now=datetime.now(UTC),
        )
    resp = upload(client, content)
    assert resp.status_code == 409 and resp.json()["run_id"] is not None
    assert upload(client, make_csv([row(2)])).status_code == 200  # a different file is fine


def test_api_refuses_to_mix_with_demo_data(client: TestClient, engine: Engine) -> None:
    with Session(engine) as s:
        f.review(s, source="demo_synthetic")
        s.commit()
    resp = upload(client, make_csv([row(1)]))
    assert resp.status_code == 409 and "refusing to load" in resp.json()["detail"]


def test_api_validation_error_when_no_file(client: TestClient) -> None:
    resp = client.post("/api/v1/ingestion/imports", data={})
    assert resp.status_code == 422 and resp.headers["content-type"].startswith(
        "application/problem+json"
    )


def test_api_options_are_applied(client: TestClient, engine: Engine) -> None:
    content = make_csv(
        [row(1, review_date="20/11/2026")], header=["review_id", "rating", "review_date"]
    )
    resp = upload(client, content, property="Potts Point", date_format="%d/%m/%Y")
    assert resp.status_code == 200 and resp.json()["per_property"] == {
        "venus-potts-point-sydney": {"received": 1, "new": 1}
    }


# ----------------------------------------------------------------------------- CLI
@pytest.fixture
def cli_db(db_url: str, engine: Engine, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("REVIEWHUB_DATABASE_URL", db_url)
    monkeypatch.setenv("REVIEWHUB_LOG_LEVEL", "ERROR")
    get_settings.cache_clear()
    yield engine
    get_settings.cache_clear()


def write(tmp: Path, name: str, content: bytes) -> str:
    path = tmp / name
    path.write_bytes(content)
    return str(path)


def test_cli_exit_codes_and_output(
    cli_db: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ok = write(tmp_path, "ok.csv", make_csv([row(1), row(2)]))
    assert cli.main(["import", ok]) == 0
    out = capsys.readouterr().out
    assert "SUCCESS" in out and "2 new, 0 updated, 0 unchanged" in out
    assert (
        cli.main(["import", ok]) == 0 and "0 new, 0 updated, 2 unchanged" in capsys.readouterr().out
    )

    partial = write(tmp_path, "partial.csv", make_csv([row(3), row(4, rating="bad")]))
    assert cli.main(["import", partial]) == 3
    assert "[error] DQ002 line 3, column rating" in capsys.readouterr().out

    broken = write(tmp_path, "broken.csv", b"title\nx\n")
    assert (
        cli.main(["import", broken]) == 1
        and "file rejected [missing_columns]" in capsys.readouterr().out
    )
    assert cli.main(["import", str(tmp_path / "missing.csv")]) == 1


def test_cli_dry_run_and_json(
    cli_db: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, "d.csv", make_csv([row(1)]))
    assert cli.main(["import", path, "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True and report["new"] == 1 and count(cli_db, Review) == 0


def test_cli_same_file_running_and_isolation_exit_codes(
    cli_db: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    content = make_csv([row(1)])
    path = write(tmp_path, "r.csv", content)
    sha = run_import(cli_db, content, dry_run=True).file_sha256
    with Session(cli_db) as s:
        start_run(
            s,
            kind="import",
            source="csv_import",
            scope_key=f"import:{sha}",
            mode="file",
            now=datetime.now(UTC),
        )
    assert cli.main(["import", path]) == 4 and "already in progress" in capsys.readouterr().err
    with Session(cli_db) as s:
        f.review(s, source="demo_synthetic")
        s.commit()
    other = write(tmp_path, "o.csv", make_csv([row(2)]))
    assert cli.main(["import", other]) == 5 and "refusing to load" in capsys.readouterr().err


def test_cli_runs_reap_recovers_stale_runs(
    cli_db: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    with Session(cli_db) as s:
        start_run(
            s,
            kind="import",
            source="csv_import",
            scope_key="import:old",
            mode="file",
            now=datetime.now(UTC) - timedelta(hours=1),
        )
        start_run(
            s,
            kind="import",
            source="csv_import",
            scope_key="import:new",
            mode="file",
            now=datetime.now(UTC),
        )
    assert cli.main(["runs", "reap"]) == 0
    assert "marked 1 stale run(s) abandoned" in capsys.readouterr().out
    with Session(cli_db) as s:
        assert {r.scope_key: r.status for r in s.scalars(select(Run))} == {
            "import:old": "abandoned",
            "import:new": "running",
        }
