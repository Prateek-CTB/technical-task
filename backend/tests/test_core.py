"""Settings, logging and error-format tests (no database needed)."""

from __future__ import annotations

import io
import json
import logging
from importlib.metadata import version

import pytest
from pydantic import ValidationError

import reviewhub
from reviewhub.core.logging import JsonFormatter, configure_logging
from reviewhub.core.settings import REPORTING_TIMEZONE, Settings


def make(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_defaults_use_sqlite_and_no_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "REVIEWHUB_DATABASE_URL",
        "REVIEWHUB_AI_PROVIDER",
        "AI_PROVIDER",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    s = make()
    assert s.database_url.startswith("sqlite:///")
    assert s.ai_provider == "none"
    assert s.openrouter_api_key is None


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEWHUB_DATABASE_URL", "postgresql://u:p@db:5432/rh")
    monkeypatch.setenv("REVIEWHUB_LOG_LEVEL", "debug")
    s = make()
    assert s.database_url == "postgresql+psycopg://u:p@db:5432/rh"  # driver made explicit
    assert s.log_level == "DEBUG"


@pytest.mark.parametrize("bad", ["mysql://x/y", "http://example.com", ""])
def test_unsupported_database_url_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        make(database_url=bad)


def test_openrouter_key_is_read_from_env_and_never_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-0123456789")
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    s = make()
    assert s.openrouter_api_key is not None
    assert s.openrouter_api_key.get_secret_value() == "sk-or-test-0123456789"
    assert s.ai_provider == "openrouter"
    assert "sk-or-test" not in repr(s) and "sk-or-test" not in str(s.model_dump())


def test_reporting_timezone_is_sydney() -> None:
    assert str(REPORTING_TIMEZONE) == "Australia/Sydney"


def test_json_logging_emits_one_object_per_line_with_extras() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("reviewhub.test.json")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("run finished", extra={"run_id": 7, "property": "chateau-de-venus"})
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.exception("failed")

    first, second = (json.loads(line) for line in stream.getvalue().splitlines())
    assert first["msg"] == "run finished"
    assert first["level"] == "INFO"
    assert first["run_id"] == 7
    assert first["property"] == "chateau-de-venus"
    assert first["ts"].endswith("+00:00")
    assert "RuntimeError: boom" in second["exc"]


def test_configure_logging_replaces_handlers_and_sets_level() -> None:
    configure_logging("WARNING", "json")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)


def test_package_version_matches_installed_metadata() -> None:
    # /health reports reviewhub.__version__; it must equal pyproject's version.
    assert reviewhub.__version__ == version("reviewhub")
