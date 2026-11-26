"""Analytics endpoints. Thin: validation and routing only; all numbers come from analytics.service."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from reviewhub.analytics import service as svc
from reviewhub.intelligence.factory import build_config
from reviewhub.persistence.base import TOPIC_KEYS

router = APIRouter(prefix="/analytics", tags=["analytics"])


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as s:
        yield s


Db = Annotated[Session, Depends(get_session)]
IncludeDemo = Annotated[
    bool, Query(description="Include SYNTHETIC demo data (excluded by default)")
]
DateFrom = Annotated[date | None, Query(description="Local publication date, inclusive")]
DateTo = Annotated[date | None, Query()]
AsOf = Annotated[
    datetime | None, Query(description="Reference time (default now); makes responses reproducible")
]
Topic = Literal[
    "cleanliness", "check_in", "staff", "noise", "facilities", "location", "room_condition", "value"
]
assert set(Topic.__args__) == set(TOPIC_KEYS)  # type: ignore[attr-defined]


def _as_of(v: datetime | None) -> datetime:
    if v is None:
        return datetime.now(UTC)
    return v if v.tzinfo else v.replace(tzinfo=UTC)


@router.get("/overview")
def overview(
    db: Db,
    include_demo: IncludeDemo = False,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    as_of: AsOf = None,
) -> dict[str, Any]:
    return svc.overview(
        db, include_demo=include_demo, date_from=date_from, date_to=date_to, as_of=_as_of(as_of)
    )


@router.get("/properties/comparison")
def properties_comparison(
    db: Db,
    include_demo: IncludeDemo = False,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    as_of: AsOf = None,
) -> dict[str, Any]:
    return svc.properties_comparison(
        db, include_demo=include_demo, date_from=date_from, date_to=date_to, as_of=_as_of(as_of)
    )


@router.get("/properties/{property_id}")
def property_detail(
    property_id: int,
    db: Db,
    include_demo: IncludeDemo = False,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    as_of: AsOf = None,
) -> dict[str, Any]:
    out = svc.property_detail(
        db,
        property_id,
        include_demo=include_demo,
        date_from=date_from,
        date_to=date_to,
        as_of=_as_of(as_of),
    )
    if out is None:
        raise HTTPException(404, f"property {property_id} not found")
    return out


@router.get("/trends/weekly")
def weekly(
    db: Db,
    include_demo: IncludeDemo = False,
    property_id: int | None = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    as_of: AsOf = None,
) -> dict[str, Any]:
    return svc.weekly_trends(
        db,
        include_demo=include_demo,
        property_id=property_id,
        date_from=date_from,
        date_to=date_to,
        as_of=_as_of(as_of),
    )


@router.get("/topics")
def topics(
    db: Db,
    include_demo: IncludeDemo = False,
    property_id: int | None = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    as_of: AsOf = None,
) -> dict[str, Any]:
    return svc.topics_insight(
        db,
        include_demo=include_demo,
        property_id=property_id,
        date_from=date_from,
        date_to=date_to,
        as_of=_as_of(as_of),
    )


@router.get("/reviews")
def reviews(
    db: Db,
    include_demo: IncludeDemo = False,
    property_id: int | None = None,
    rating_min: Annotated[float | None, Query(ge=1, le=10)] = None,
    rating_max: Annotated[float | None, Query(ge=1, le=10)] = None,
    sentiment: Literal["positive", "negative", "neutral", "mixed"] | None = None,
    topic: Topic | None = None,
    topic_sentiment: Literal["positive", "negative", "neutral", "mixed"] | None = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    language: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[
        str | None, Query(max_length=100, description="Text search in title/liked/disliked")
    ] = None,
    sort: Literal["newest", "oldest", "rating_high", "rating_low"] = "newest",
    as_of: AsOf = None,
) -> dict[str, Any]:
    return svc.review_explorer(
        db,
        include_demo=include_demo,
        property_id=property_id,
        rating_min=rating_min,
        rating_max=rating_max,
        sentiment=sentiment,
        topic=topic,
        topic_sentiment=topic_sentiment,
        date_from=date_from,
        date_to=date_to,
        language=language,
        page=page,
        page_size=page_size,
        q=q,
        sort=sort,
        as_of=_as_of(as_of),
    )


@router.get("/recommendations")
def recommendations(
    db: Db,
    include_demo: IncludeDemo = False,
    property_id: int | None = None,
    window_weeks: Annotated[int, Query(ge=1, le=52)] = 12,
    min_reviews: Annotated[int, Query(ge=2, le=50)] = 3,
    as_of: AsOf = None,
) -> dict[str, Any]:
    return svc.recommendations(
        db,
        include_demo=include_demo,
        property_id=property_id,
        window_weeks=window_weeks,
        min_reviews=min_reviews,
        as_of=_as_of(as_of),
    )


@router.get("/coverage")
def coverage(
    request: Request, db: Db, include_demo: IncludeDemo = False, as_of: AsOf = None
) -> dict[str, Any]:
    cfg = build_config(request.app.state.settings)
    return svc.coverage(
        db,
        include_demo=include_demo,
        as_of=_as_of(as_of),
        settings_cfg={
            "provider": cfg.provider,
            "model": cfg.model or None,
            "prompt_version": cfg.prompt_version,
            "taxonomy_version": cfg.taxonomy_version,
        },
    )
