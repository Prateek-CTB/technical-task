"""Declarative base, shared column types and the value sets used in CHECK constraints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

# Deterministic constraint names: required for reliable Alembic migrations and
# readable error messages on both PostgreSQL and SQLite.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


#: BIGINT identity on PostgreSQL; plain INTEGER PRIMARY KEY on SQLite (required for rowid aliasing).
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every engine.

    SQLite has no timezone-aware type and silently drops tzinfo, so values are
    normalised to UTC on the way in and re-tagged as UTC on the way out. Naive
    datetimes are rejected rather than guessed at.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime rejected: provide a timezone-aware value")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        stamp: datetime = value
        if stamp.tzinfo is None:
            return stamp.replace(tzinfo=UTC)
        return stamp.astimezone(UTC)


# --- value sets (single definition; used by CHECK constraints and code) -------
SOURCES = ("booking_public", "booking_experimental", "csv_import", "json_import", "demo_synthetic")
RUN_KINDS = ("collect", "import", "analyse")
RUN_STATUSES = ("running", "success", "partial", "failed", "blocked", "abandoned")
RUN_PROPERTY_STATUSES = ("running", "success", "partial", "failed", "blocked")
SENTIMENTS = ("positive", "negative", "neutral", "mixed")
TOPIC_KEYS = (
    "cleanliness",
    "check_in",
    "staff",
    "noise",
    "facilities",
    "location",
    "room_condition",
    "value",
)
ANALYZERS = ("llm", "lexicon")
ANALYSIS_STATUSES = ("ok", "needs_review", "failed")
SEVERITIES = ("info", "warning", "error")


def in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"
