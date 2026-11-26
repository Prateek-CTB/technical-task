"""Start a review collection from the dashboard ("Get latest reviews").

The run uses exactly the same collector, rules and records as ``reviewhub collect``: incremental
by default (stops at reviews already held), paced, single-flight, a CAPTCHA/WAF/refusal stops it
and is recorded as BLOCKED with a cooldown, and nothing is ever worked around. It runs in the
background; the dashboard follows it through the run history. When it finishes, the new reviews
are analysed with the offline rule-based analyzer so the insights update straight away.

``mode=headed`` opens a visible browser window on the computer running the API (the mode Booking.com
currently serves reviews to); ``headless`` is for servers and is currently refused by the site.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from reviewhub.api import background
from reviewhub.core.errors import problem
from reviewhub.core.settings import Settings

router = APIRouter(prefix="/collection", tags=["collection"])


class CollectionRunRequest(BaseModel):
    mode: Literal["headed", "headless"] = "headed"
    max_pages: int = Field(default=5, ge=1, le=30, description="10 reviews per page, per property")
    full: bool = Field(default=False, description="re-read from the newest even if already held")


@router.post(
    "/runs",
    response_model=None,
    responses={
        202: {"description": "Collection started in the background"},
        409: {"description": "A collection is already running"},
    },
)
def start_collection(request: Request, body: CollectionRunRequest | None = None) -> JSONResponse:
    body = body or CollectionRunRequest()
    settings: Settings = request.app.state.settings
    factory = request.app.state.session_factory
    stale = timedelta(minutes=settings.run_stale_after_minutes)
    with factory() as s:
        running = background.active_run(s, "collect", stale)
    if running is not None:
        return problem(
            409,
            "A collection is already running",
            detail=f"run #{running.id} is still in progress",
            run_id=running.id,
        )

    def job() -> None:
        from reviewhub.collection.service import CollectOptions, collect
        from reviewhub.intelligence.factory import build_config, build_provider
        from reviewhub.intelligence.service import run_analysis

        collect(
            factory,
            settings,
            CollectOptions(mode=body.mode, max_pages=body.max_pages, full=body.full, concurrency=1),
        )
        # Analyse whatever is new, offline and in seconds, so the dashboard updates at once.
        run_analysis(
            factory,
            build_provider(settings, "builtin"),
            build_config(settings, "builtin"),
            batch_size=200,
            concurrency=1,
            requests_per_minute=600_000,
            stale_after=stale,
        )

    background.start_job("collection", job)
    payload: dict[str, Any] = {"started": True, "mode": body.mode, "max_pages": body.max_pages}
    return JSONResponse(status_code=202, content={"data": payload})
