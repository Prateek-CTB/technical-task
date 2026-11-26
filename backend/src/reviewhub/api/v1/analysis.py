"""Run review analysis from the dashboard.

* ``rule_based``: the offline analyzer. Local, free, finishes in seconds, so it runs inside the
  request and returns the result.
* ``ai``: the OpenRouter model. Slow and rate-limited (free tier ~50 requests/day), so it runs
  in the background; the dashboard follows it through the run history. Real guest text is only
  sent when the operator has set ``AI_ALLOW_REAL_GUEST_TEXT=true`` (a data-protection decision,
  ADR 0023); ``GET /analysis/config`` tells the UI whether the button can be used and why not.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from reviewhub.api import background
from reviewhub.core.errors import problem
from reviewhub.core.settings import Settings
from reviewhub.intelligence.factory import build_config, build_provider
from reviewhub.intelligence.providers import ProviderError
from reviewhub.intelligence.service import run_analysis
from reviewhub.persistence.runs import RunAlreadyRunning
from reviewhub.persistence.scopes import PRODUCTION_SOURCES, dataset_provenance

router = APIRouter(prefix="/analysis", tags=["analysis"])

#: Upper bound for one dashboard-triggered AI run (the free tier allows ~50 requests a day).
AI_RUN_MAX = 50


class AnalysisRunRequest(BaseModel):
    method: Literal["rule_based", "ai"] = "rule_based"
    limit: int = Field(default=20, ge=1, le=AI_RUN_MAX, description="AI only: reviews to analyse")


def _ai_status(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    model = settings.openrouter_model or None
    out: dict[str, Any] = {
        "provider": "openrouter",
        "model": model,
        "configured": False,
        "real_text_allowed": settings.ai_allow_real_guest_text,
        "can_run": False,
        "reason": None,
        "max_per_run": AI_RUN_MAX,
    }
    if not settings.openrouter_api_key or not model:
        out["reason"] = (
            "No AI provider is configured. Add OPENROUTER_API_KEY and OPENROUTER_MODEL (a :free model) to the .env file, then restart."
        )
        return out
    try:
        build_provider(settings, "openrouter")
    except ProviderError as exc:
        out["reason"] = f"The AI configuration is not usable: {exc}"
        return out
    out["configured"] = True
    with request.app.state.session_factory() as s:
        sources = dataset_provenance(s)["sources"]
    has_real = any(src in PRODUCTION_SOURCES for src in sources)
    if has_real and not settings.ai_allow_real_guest_text:
        out["reason"] = (
            "Sending real review text to the external AI provider is switched off. An administrator "
            "can allow it with AI_ALLOW_REAL_GUEST_TEXT=true in the .env file (a data-protection decision)."
        )
        return out
    out["can_run"] = True
    return out


@router.get("/config")
def analysis_config(request: Request) -> dict[str, Any]:
    """Which analysis methods the dashboard can start, and why one is unavailable. No secrets."""
    return {"data": {"rule_based": {"can_run": True}, "ai": _ai_status(request)}}


@router.post(
    "/runs",
    response_model=None,
    responses={
        202: {"description": "AI analysis started in the background"},
        409: {"description": "Already running, or AI not available"},
    },
)
def start_analysis(
    request: Request, body: AnalysisRunRequest | None = None
) -> dict[str, Any] | JSONResponse:
    body = body or AnalysisRunRequest()
    settings: Settings = request.app.state.settings
    factory = request.app.state.session_factory
    stale = timedelta(minutes=settings.run_stale_after_minutes)

    if body.method == "rule_based":
        try:
            report = run_analysis(
                factory,
                build_provider(settings, "builtin"),
                build_config(settings, "builtin"),
                batch_size=200,
                concurrency=1,
                requests_per_minute=600_000,
                stale_after=stale,
            )
        except RunAlreadyRunning as exc:
            return problem(409, "Analysis already running", detail=str(exc), run_id=exc.run_id)
        return {"data": asdict(report)}

    status = _ai_status(request)
    if not status["can_run"]:
        return problem(409, "AI analysis is not available", detail=status["reason"])
    with factory() as s:
        running = background.active_run(s, "analyse", stale)
    if running is not None:
        return problem(
            409,
            "Analysis already running",
            detail=f"run #{running.id} is still in progress",
            run_id=running.id,
        )

    def job() -> None:
        run_analysis(
            factory,
            build_provider(settings, "openrouter"),
            build_config(settings, "openrouter"),
            batch_size=settings.ai_batch_size,
            concurrency=settings.ai_max_concurrency,
            requests_per_minute=settings.ai_requests_per_minute,
            max_retries=settings.ai_max_retries,
            limit=body.limit,
            allow_real_text=settings.ai_allow_real_guest_text,
            stale_after=stale,
        )

    background.start_job("ai-analysis", job)
    return JSONResponse(
        status_code=202,
        content={
            "data": {"started": True, "method": "ai", "limit": body.limit, "model": status["model"]}
        },
    )
