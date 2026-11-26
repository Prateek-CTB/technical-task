"""Application settings, read from environment variables (prefix ``REVIEWHUB_``) and ``.env``.

Secrets use ``SecretStr`` so they never appear in ``repr()`` or logs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATA_DIR = REPO_ROOT / "data"

#: All four properties are in Sydney and all reporting periods use this zone.
REPORTING_TIMEZONE = ZoneInfo("Australia/Sydney")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REVIEWHUB_",
        env_file=(str(REPO_ROOT / ".env"), ".env"),
        populate_by_name=True,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"

    # --- database -----------------------------------------------------------
    #: SQLite by default (zero setup). PostgreSQL: postgresql+psycopg://user:pw@host:5432/db
    database_url: str = f"sqlite:///{(DEFAULT_DATA_DIR / 'reviewhub.db').as_posix()}"

    # --- logging ------------------------------------------------------------
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    # --- AI (ADR 0021): OpenRouter free models, or the deterministic mock for tests ----
    # Names follow the project's .env: AI_PROVIDER, OPENROUTER_*, AI_* (REVIEWHUB_ prefix also works).
    #: ``builtin`` = the offline rule-based analyzer (no key, no network).
    ai_provider: Literal["none", "mock", "builtin", "openrouter"] = Field(
        default="none", validation_alias=AliasChoices("AI_PROVIDER", "REVIEWHUB_AI_PROVIDER")
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    openrouter_model: str = Field(default="", validation_alias="OPENROUTER_MODEL")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL"
    )
    ai_max_concurrency: int = Field(
        default=2,
        ge=1,
        le=8,
        validation_alias=AliasChoices("AI_MAX_CONCURRENCY", "REVIEWHUB_AI_CONCURRENCY"),
    )
    ai_batch_size: int = Field(default=10, ge=1, le=100, validation_alias="AI_BATCH_SIZE")
    ai_timeout_seconds: float = Field(
        default=60, ge=1, le=300, validation_alias="AI_TIMEOUT_SECONDS"
    )
    ai_max_retries: int = Field(default=2, ge=0, le=5, validation_alias="AI_MAX_RETRIES")
    ai_prompt_version: str = Field(default="v1", validation_alias="AI_PROMPT_VERSION")
    ai_taxonomy_version: str = Field(default="v1", validation_alias="AI_TAXONOMY_VERSION")
    #: Real (non-demo) guest text is NOT sent to the external provider unless this is set deliberately
    #: after the data-protection question (D7) has been answered.
    ai_allow_real_guest_text: bool = Field(
        default=False, validation_alias="AI_ALLOW_REAL_GUEST_TEXT"
    )
    #: OpenRouter's free tier allows ~20 requests/minute; stay under it.
    ai_requests_per_minute: int = Field(
        default=15, ge=1, le=600, validation_alias="REVIEWHUB_AI_REQUESTS_PER_MINUTE"
    )

    # --- collection (ADR 0022) -------------------------------------------------------
    #: Headless is the default so the collector runs on servers. Booking.com currently refuses the
    #: review list to headless clients; that is reported as ``blocked`` and never worked around.
    collect_browser_mode: Literal["headless", "headed"] = "headless"
    #: Persistent browser profile (cookies, local storage). Empty string = ephemeral session.
    #: Holds session data: git-ignored, never logged. Delete the folder to forget the session.
    collect_profile_dir: str = str(DEFAULT_DATA_DIR / "collect" / "profile")
    collect_spool_dir: str = str(DEFAULT_DATA_DIR / "collect" / "spool")
    collect_log_dir: str = str(DEFAULT_DATA_DIR / "collect" / "logs")
    collect_inbox_dir: str = str(DEFAULT_DATA_DIR / "inbox")
    collect_nav_timeout_seconds: float = Field(default=60, ge=5, le=300)
    collect_action_timeout_seconds: float = Field(default=45, ge=5, le=300)
    collect_max_attempts: int = Field(default=4, ge=1, le=10)
    collect_backoff_base_seconds: float = Field(default=2.0, ge=0, le=60)
    collect_backoff_max_seconds: float = Field(default=120.0, ge=0, le=900)
    #: Pause between page turns / between properties (uniform random in [min, max]).
    collect_page_delay_min: float = Field(default=2.0, ge=0)
    collect_page_delay_max: float = Field(default=4.0, ge=0)
    collect_property_delay_min: float = Field(default=8.0, ge=0)
    collect_property_delay_max: float = Field(default=15.0, ge=0)
    #: Parallel browsers. Pacing is global, so more workers do not mean more requests per second.
    collect_concurrency: int = Field(default=1, ge=1, le=3)
    #: After a CAPTCHA or explicit refusal, do not contact the site again in the same browser
    #: mode for this long.
    collect_block_cooldown_hours: float = Field(default=24, ge=0)
    #: Authorised feed (channel manager / owner API) returning the JSON import format.
    collect_feed_url: str = ""
    collect_feed_token: SecretStr | None = None

    # --- runs and imports ---------------------------------------------------------
    run_stale_after_minutes: int = Field(default=15, ge=1)
    import_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = "postgresql://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
        if not value.startswith(("sqlite:", "postgresql+psycopg:")):
            raise ValueError("database_url must be a sqlite: or postgresql: URL")
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("invalid log level")
        return level


@lru_cache
def get_settings() -> Settings:
    return Settings()
