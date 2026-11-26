"""Builders for import tests. All review text is invented."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from reviewhub.ingestion.contracts import FileAdapter, ImportReport
from reviewhub.ingestion.service import ImportOptions, import_file
from reviewhub.persistence.session import create_session_factory

FIXED_NOW = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
PROPERTY = "chateau-de-venus"

HEADER = [
    "review_id",
    "property",
    "rating",
    "review_date",
    "title",
    "liked",
    "disliked",
    "language",
    "country",
    "traveller_type",
    "room_type",
    "checkin_date",
    "checkout_date",
    "nights",
    "management_reply",
]


def row(n: int, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "review_id": f"r{n:04d}",
        "property": PROPERTY,
        "rating": 8,
        "review_date": "2026-11-20T09:15:00+11:00",
        "title": f"Invented title {n}",
        "liked": f"Invented praise number {n}.",
        "disliked": "",
        "language": "en",
        "country": "AU",
        "traveller_type": "Couple",
        "room_type": "Double Room",
        "checkin_date": "2026-11-17",
        "checkout_date": "2026-11-19",
        "nights": 2,
        "management_reply": "",
    }
    base.update(overrides)
    return base


def make_csv(
    rows: list[dict[str, Any]], header: list[str] | None = None, delimiter: str = ","
) -> bytes:
    header = header or HEADER
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=header, delimiter=delimiter, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def run_import(
    engine: Engine,
    content: bytes,
    filename: str = "reviews.csv",
    *,
    clock: Callable[[], datetime] = lambda: FIXED_NOW,
    adapter: FileAdapter | None = None,
    **options: Any,
) -> ImportReport:
    return import_file(
        create_session_factory(engine),
        content=content,
        filename=filename,
        options=ImportOptions(**options),
        adapter=adapter,
        clock=clock,
    )


def count(engine: Engine, model: Any) -> int:
    with Session(engine) as s:
        return int(s.scalar(select(func.count()).select_from(model)) or 0)
