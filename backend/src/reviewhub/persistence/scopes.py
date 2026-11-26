"""Dataset isolation (ADR 0013, ADR 0017).

Three kinds of rows must never be confused:

* **production**   - reviews from a permitted source (``booking_public``, imports)
* **demo**         - invented, deterministic demo data (``demo_synthetic``)
* **experimental** - records from the withdrawn browser experiment (``booking_experimental``)

Reporting code selects rows only through :func:`reportable`, whose default
excludes demo and experimental data. Loading code must call
:func:`assert_can_load`, which refuses to put different kinds in one database.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from .models import Review

PRODUCTION_SOURCES = ("booking_public", "csv_import", "json_import")
DEMO_SOURCES = ("demo_synthetic",)
EXPERIMENTAL_SOURCES = ("booking_experimental",)
#: sources a user-supplied file may be recorded as. Demo and experimental data can
#: never arrive through an import: a file cannot claim to be something it is not.
IMPORT_SOURCES = ("csv_import", "json_import")


class DatasetIsolationError(RuntimeError):
    """Loading this data would mix production, demo or experimental records."""


def reportable_sources(
    *, include_demo: bool = False, include_experimental: bool = False
) -> tuple[str, ...]:
    sources: tuple[str, ...] = PRODUCTION_SOURCES
    if include_demo:
        sources += DEMO_SOURCES
    if include_experimental:
        sources += EXPERIMENTAL_SOURCES
    return sources


def reportable(
    *, include_demo: bool = False, include_experimental: bool = False
) -> ColumnElement[bool]:
    """The one WHERE clause reporting queries use to choose their rows."""
    return Review.source.in_(
        reportable_sources(include_demo=include_demo, include_experimental=include_experimental)
    )


def dataset_provenance(session: Session) -> dict[str, Any]:
    """Counts per source plus flags; every analytics response carries this."""
    rows = session.execute(select(Review.source, func.count()).group_by(Review.source)).all()
    sources = {source: int(n) for source, n in sorted(rows)}
    return {
        "sources": sources,
        "contains_demo": any(s in DEMO_SOURCES for s in sources),
        "contains_experimental": any(s in EXPERIMENTAL_SOURCES for s in sources),
        "contains_non_production_data": any(s not in PRODUCTION_SOURCES for s in sources),
    }


def assert_can_load(session: Session, incoming_source: str) -> None:
    """Refuse to mix dataset kinds in one database.

    Production data may only be loaded into a database holding no demo or
    experimental rows, and vice versa, so a dashboard can never silently blend
    invented or unverified records with real ones.
    """
    present = set(session.scalars(select(Review.source).distinct()))
    if incoming_source in PRODUCTION_SOURCES:
        clash = present & set(DEMO_SOURCES + EXPERIMENTAL_SOURCES)
        remedy = "use a fresh database (or `reviewhub seed-demo --reset`) before loading real data"
    elif incoming_source in DEMO_SOURCES:
        clash = present - set(DEMO_SOURCES)
        remedy = "demo data is only loaded into an empty or demo-only database"
    elif incoming_source in EXPERIMENTAL_SOURCES:
        clash = present - set(EXPERIMENTAL_SOURCES)
        remedy = "experimental data is only loaded into an empty database"
    else:
        raise DatasetIsolationError(f"unknown source {incoming_source!r}")
    if clash:
        raise DatasetIsolationError(
            f"refusing to load {incoming_source!r} data: the database already contains "
            f"{sorted(clash)} rows; {remedy}"
        )
