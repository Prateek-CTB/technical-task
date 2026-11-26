"""Resumable review analysis: pending selection -> model call -> verification -> persistence.

* Pending = eligible reviews with no usable analysis for this exact
  (provider, model, prompt version, taxonomy version, input text). Cached results are never re-requested.
* Each review is saved in its own transaction, so a failure or crash never loses finished work;
  re-running simply continues with what is still pending.
* Fatal provider conditions (bad key, quota exhausted, model gone, paid request) stop the run
  safely; per-review problems are recorded as ``failed`` and never replace a good analysis.
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from reviewhub.ingestion.text import is_filler
from reviewhub.persistence.models import Property, Review, ReviewAnalysis, ReviewAspect
from reviewhub.persistence.runs import (
    DEFAULT_STALE_AFTER,
    Clock,
    finish_run,
    heartbeat,
    start_run,
    utc_now,
)
from reviewhub.persistence.scopes import DEMO_SOURCES

from .prompt import SYSTEM_PROMPT, build_user_message
from .providers import ProviderError, ProviderResponse, ReviewAnalyzer, redact
from .schema import Verified, parse_model_json, verify

log = logging.getLogger(__name__)

MAX_ATTEMPTS_PER_REVIEW = 3  # failed reviews are not retried forever (protects the free quota)
MAX_CONSECUTIVE_FAILURES = 5


#: Providers that run on this machine: no guest text leaves it.
LOCAL_PROVIDERS = frozenset({"mock", "none", "builtin"})
#: Which analysis is used for reporting when a review has several (higher wins, ties: newest).
#: An LLM analysis replaces the rule-based one; neither the lexicon nor the test mock can
#: displace an LLM result.
PRECEDENCE = {"mock": 0, "builtin": 1}
LLM_PRECEDENCE = 2


def precedence(provider: str) -> int:
    return PRECEDENCE.get(provider, LLM_PRECEDENCE)


@dataclass(frozen=True)
class AnalysisConfig:
    provider: str
    model: str
    prompt_version: str
    taxonomy_version: str

    @property
    def analyzer(self) -> str:
        """``lexicon`` for the built-in rule-based analyzer, ``llm`` for model providers."""
        return "lexicon" if self.provider == "builtin" else "llm"

    @property
    def scope_key(self) -> str:
        return f"analyse:{self.provider}:{self.model}:{self.prompt_version}:{self.taxonomy_version}"


@dataclass
class Candidate:
    review_id: int
    property_id: int
    slug: str
    title: str | None
    liked: str | None
    disliked: str | None
    rating: float
    input_hash: str
    attempts: int = 0


@dataclass
class Selection:
    eligible: int = 0
    no_text: int = 0
    cached: int = 0
    exhausted: int = 0
    blocked_real_text: int = 0
    pending: list[Candidate] = field(default_factory=list)


@dataclass
class RunReport:
    run_id: int | None
    dry_run: bool
    status: str
    eligible: int
    pending_at_start: int
    cached: int
    no_text: int
    exhausted: int = 0
    blocked_real_text: int = 0
    attempted: int = 0
    succeeded: int = 0
    needs_review: int = 0
    failed: int = 0
    remaining: int = 0
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    provider: str = ""


def input_hash(title: str | None, liked: str | None, disliked: str | None) -> str:
    return hashlib.sha256(f"{title or ''}\n{liked or ''}\n{disliked or ''}".encode()).hexdigest()


def has_text(title: str | None, liked: str | None, disliked: str | None) -> bool:
    return bool(title or liked or (disliked and not is_filler(disliked)))


# --------------------------------------------------------------------------- selection
def select_pending(
    s: Session,
    cfg: AnalysisConfig,
    *,
    property_slug: str | None = None,
    limit: int | None = None,
    retry_failed: bool = False,
    allow_real_text: bool = False,
) -> Selection:
    q = (
        select(
            Review.id,
            Review.property_id,
            Property.slug,
            Review.title,
            Review.positive_text,
            Review.negative_text,
            Review.rating,
            Review.source,
        )
        .join(Property, Property.id == Review.property_id)
        .order_by(Review.id)
    )
    if property_slug:
        q = q.where(Property.slug == property_slug)
    known = {
        (rid, h): (status, attempts)
        for rid, h, status, attempts in s.execute(
            select(
                ReviewAnalysis.review_id,
                ReviewAnalysis.input_hash,
                ReviewAnalysis.status,
                ReviewAnalysis.attempts,
            ).where(
                ReviewAnalysis.analyzer == cfg.analyzer,
                ReviewAnalysis.provider == cfg.provider,
                ReviewAnalysis.model_id == cfg.model,
                ReviewAnalysis.prompt_version == cfg.prompt_version,
                ReviewAnalysis.taxonomy_version == cfg.taxonomy_version,
            )
        )
    }
    sel = Selection()
    per_property: dict[int, list[Candidate]] = defaultdict(list)
    external = cfg.provider not in LOCAL_PROVIDERS
    for rid, pid, slug, title, liked, disliked, rating, source in s.execute(q):
        if not has_text(title, liked, disliked):
            sel.no_text += 1
            continue
        if external and not allow_real_text and source not in DEMO_SOURCES:
            sel.blocked_real_text += (
                1  # real guest text stays local until permissions are established
            )
            continue
        sel.eligible += 1
        h = input_hash(title, liked, disliked)
        status, attempts = known.get((rid, h), (None, 0))
        if status in ("ok", "needs_review"):
            sel.cached += 1
        elif status == "failed" and attempts >= MAX_ATTEMPTS_PER_REVIEW and not retry_failed:
            sel.exhausted += 1
        else:
            per_property[pid].append(
                Candidate(rid, pid, slug, title, liked, disliked, rating, h, attempts)
            )
    # Round-robin across properties so a small --limit is a representative sample.
    queues = [per_property[k] for k in sorted(per_property)]
    while any(queues):
        for queue in queues:
            if queue:
                sel.pending.append(queue.pop(0))
    if limit is not None:
        sel.pending = sel.pending[:limit]
    return sel


# --------------------------------------------------------------------------- calling the model
class Limiter:
    """Keeps request starts at least 60/rpm seconds apart (thread-safe)."""

    def __init__(
        self,
        rpm: int,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gap, self._sleep, self._now = 60.0 / rpm, sleep, now
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            t = self._now()
            delay = max(0.0, self._next - t)
            self._next = max(t, self._next) + self._gap
        if delay:
            self._sleep(delay)


@dataclass
class Outcome:
    cand: Candidate
    verified: Verified | None = None
    response: ProviderResponse | None = None
    error: str | None = None
    fatal: ProviderError | None = None
    attempts: int = 1
    input_tokens: int = 0
    output_tokens: int = 0


def _call(
    provider: ReviewAnalyzer,
    user: str,
    limiter: Limiter,
    max_retries: int,
    sleep: Callable[[float], None],
    rng: random.Random,
) -> ProviderResponse:
    tries = 0
    while True:
        limiter.wait()
        try:
            return provider.complete(SYSTEM_PROMPT, user)
        except ProviderError as exc:
            if exc.fatal or not exc.retryable or tries >= max_retries:
                raise
            wait = getattr(exc, "retry_after", None)
            sleep(min(60.0, wait) if wait else min(30.0, 2.0 * 2**tries) + rng.random())
            tries += 1


def analyse_one(
    provider: ReviewAnalyzer,
    cand: Candidate,
    limiter: Limiter,
    max_retries: int,
    sleep: Callable[[float], None],
    rng: random.Random,
) -> Outcome:
    out = Outcome(cand)
    rating = cand.rating if getattr(provider, "wants_rating", False) else None
    base = build_user_message(cand.title, cand.liked, cand.disliked, rating=rating)
    fields = {
        k: v
        for k, v in (("title", cand.title), ("liked", cand.liked), ("disliked", cand.disliked))
        if v
    }
    user = base
    for attempt in (1, 2):  # one re-ask if the answer is invalid or its evidence cannot be verified
        out.attempts = attempt
        try:
            resp = _call(provider, user, limiter, max_retries, sleep, rng)
        except ProviderError as exc:
            if exc.fatal:
                out.fatal = exc
            out.error = redact(f"{exc.code}: {exc}")[:500]
            return out
        out.response = resp
        out.input_tokens += resp.input_tokens or 0
        out.output_tokens += resp.output_tokens or 0
        try:
            verified = verify(parse_model_json(resp.content), fields, cand.rating)
        except ValueError as exc:
            out.error = f"invalid_output: {exc}"[:500]
        else:
            if verified.status != "failed":
                out.verified, out.error = verified, None
                return out
            out.error = f"unverifiable_evidence: {verified.error}"
        user = (
            base
            + f"\n\nYour previous answer was rejected ({out.error}). Return ONLY a valid JSON object; quote evidence exactly as written in the review."
        )
    return out


# --------------------------------------------------------------------------- persistence
def _cache_filter(cfg: AnalysisConfig, review_id: int, h: str) -> Any:
    return (
        ReviewAnalysis.review_id == review_id,
        ReviewAnalysis.analyzer == cfg.analyzer,
        ReviewAnalysis.provider == cfg.provider,
        ReviewAnalysis.model_id == cfg.model,
        ReviewAnalysis.prompt_version == cfg.prompt_version,
        ReviewAnalysis.taxonomy_version == cfg.taxonomy_version,
        ReviewAnalysis.input_hash == h,
    )


def save_outcome(
    s: Session, cfg: AnalysisConfig, o: Outcome, run_id: int | None, now: datetime
) -> str:
    """Persist one result in its own transaction. Returns 'ok' | 'needs_review' | 'failed' | 'skipped'."""
    existing = s.scalar(
        select(ReviewAnalysis).where(*_cache_filter(cfg, o.cand.review_id, o.cand.input_hash))
    )
    if existing is not None and existing.status != "failed":
        return "skipped"  # another worker finished it first
    r = o.response
    flags: list[str] = list(o.verified.flags) if o.verified else []
    if r is not None and r.upstream:
        flags.append(f"upstream:{r.upstream}")
    if r is not None and r.model != cfg.model:
        flags.append(f"served_by:{r.model}")
    common: dict[str, Any] = {
        "run_id": run_id,
        "flags": flags,
        "attempts": (existing.attempts if existing else 0) + o.attempts,
        "input_tokens": o.input_tokens or None,
        "output_tokens": o.output_tokens or None,
        "cost_usd": r.cost_usd if r else None,
        "created_at": now,
    }
    if o.verified is None:
        values = {
            **common,
            "status": "failed",
            "overall_sentiment": None,
            "error": o.error or "unknown failure",
            "is_current": False,
        }
        row = existing
        if row is None:
            row = ReviewAnalysis(
                review_id=o.cand.review_id,
                analyzer=cfg.analyzer,
                provider=cfg.provider,
                model_id=cfg.model,
                prompt_version=cfg.prompt_version,
                taxonomy_version=cfg.taxonomy_version,
                input_hash=o.cand.input_hash,
                **values,
            )
            s.add(row)
        else:
            for k, v in values.items():
                setattr(row, k, v)
        s.commit()
        return "failed"

    a = o.verified.analysis
    values = {
        **common,
        "status": o.verified.status,
        "overall_sentiment": a.overall_sentiment,
        "summary_en": a.summary_en,
        "main_complaint": a.main_complaint,
        "main_complaint_topic": a.main_complaint_topic,
        "suggested_action": a.suggested_action,
        "language": a.language[:8],
        "confidence": a.confidence,
        "off_taxonomy": [],
        "error": None,
        "is_current": False,
    }
    row = existing
    if row is None:
        row = ReviewAnalysis(
            review_id=o.cand.review_id,
            analyzer=cfg.analyzer,
            provider=cfg.provider,
            model_id=cfg.model,
            prompt_version=cfg.prompt_version,
            taxonomy_version=cfg.taxonomy_version,
            input_hash=o.cand.input_hash,
            **values,
        )
        s.add(row)
    else:
        for k, v in values.items():
            setattr(row, k, v)
    s.flush()
    for asp in o.verified.aspects:
        s.add(
            ReviewAspect(
                analysis_id=row.id,
                review_id=row.review_id,
                topic=asp["topic"],
                sentiment=asp["sentiment"],
                certainty=asp["certainty"],
                confidence=asp["confidence"],
                evidence=asp["evidence"],
            )
        )
    s.flush()
    current = s.scalar(
        select(ReviewAnalysis).where(
            ReviewAnalysis.review_id == row.review_id, ReviewAnalysis.is_current
        )
    )
    # Precedence: LLM > rule-based lexicon > test mock. A lower-ranked analyzer never
    # displaces a higher-ranked current analysis (the mock never displaces anything real).
    if current is None or precedence(cfg.provider) >= precedence(current.provider):
        if current is not None and current.id != row.id:
            current.is_current = False
            s.flush()
        row.is_current = True
    s.commit()
    return o.verified.status


# --------------------------------------------------------------------------- the run
def run_analysis(
    factory: sessionmaker[Session],
    provider: ReviewAnalyzer,
    cfg: AnalysisConfig,
    *,
    batch_size: int = 10,
    concurrency: int = 2,
    requests_per_minute: int = 15,
    max_retries: int = 2,
    property_slug: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    retry_failed: bool = False,
    allow_real_text: bool = False,
    clock: Clock = utc_now,
    sleep: Callable[[float], None] = time.sleep,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    seed: int | None = None,
) -> RunReport:
    with factory() as s:
        sel = select_pending(
            s,
            cfg,
            property_slug=property_slug,
            limit=limit,
            retry_failed=retry_failed,
            allow_real_text=allow_real_text,
        )
    report = RunReport(
        None,
        dry_run,
        "dry_run" if dry_run else "running",
        sel.eligible,
        len(sel.pending),
        sel.cached,
        sel.no_text,
        sel.exhausted,
        sel.blocked_real_text,
        model=cfg.model,
        provider=cfg.provider,
        remaining=len(sel.pending),
    )
    if dry_run:
        return report

    with factory() as s:
        run = start_run(
            s,
            kind="analyse",
            source=cfg.provider,
            scope_key=cfg.scope_key,
            mode="resume",
            version=cfg.prompt_version,
            params={
                "model": cfg.model,
                "taxonomy": cfg.taxonomy_version,
                "batch_size": batch_size,
                "concurrency": concurrency,
                "property": property_slug,
                "limit": limit,
            },
            now=clock(),
            stale_after=stale_after,
        )
        run_id = run.id
    report.run_id = run_id
    limiter, rng = Limiter(requests_per_minute, sleep), random.Random(seed)
    consecutive = 0
    queue = list(sel.pending)
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            while queue and report.stop_reason is None:
                chunk, queue = queue[:batch_size], queue[batch_size:]
                outcomes = list(
                    pool.map(
                        lambda c: analyse_one(provider, c, limiter, max_retries, sleep, rng), chunk
                    )
                )
                with factory() as s:
                    for o in outcomes:
                        report.input_tokens += o.input_tokens
                        report.output_tokens += o.output_tokens
                        report.cost_usd += (o.response.cost_usd or 0.0) if o.response else 0.0
                        if o.fatal is not None:
                            report.stop_reason = f"{o.fatal.code}: {redact(str(o.fatal))}"[:300]
                            queue.insert(0, o.cand)
                            continue
                        report.attempted += 1
                        result = save_outcome(s, cfg, o, run_id, clock())
                        if result == "failed":
                            report.failed += 1
                            consecutive += 1
                        else:
                            consecutive = 0
                            if result == "needs_review":
                                report.needs_review += 1
                            if result in ("ok", "needs_review"):
                                report.succeeded += 1
                    if consecutive >= MAX_CONSECUTIVE_FAILURES and report.stop_reason is None:
                        report.stop_reason = f"{consecutive} consecutive failures; stopping to protect the free quota"
                    if not heartbeat(s, run_id, now=clock()):
                        report.stop_reason = "run was marked abandoned"
    except BaseException as exc:
        with factory() as s:
            finish_run(
                s,
                run_id,
                status="failed",
                now=clock(),
                error=redact(f"{type(exc).__name__}: {exc}")[:500],
            )
        raise
    report.remaining = len(queue)
    report.status = (
        "success"
        if not report.failed and report.stop_reason is None
        else ("partial" if report.succeeded else "failed")
    )
    with factory() as s:
        finish_run(
            s,
            run_id,
            status=report.status,
            now=clock(),
            items_total=len(sel.pending),
            items_ok=report.succeeded,
            items_failed=report.failed,
            error=report.stop_reason,
            input_tokens=report.input_tokens,
            output_tokens=report.output_tokens,
            cost_usd=report.cost_usd,
            params={
                "model": cfg.model,
                "remaining": report.remaining,
                "needs_review": report.needs_review,
                "stop_reason": report.stop_reason,
            },
        )
    return report


def coverage_counts(s: Session, cfg: AnalysisConfig | None = None) -> dict[str, Any]:
    """Whole-database analysis coverage (analytics adds scope filters on top)."""
    rows = s.execute(
        select(Review.id, Review.title, Review.positive_text, Review.negative_text)
    ).all()
    eligible = {r.id for r in rows if has_text(r.title, r.positive_text, r.negative_text)}
    current = {
        rid
        for (rid,) in s.execute(select(ReviewAnalysis.review_id).where(ReviewAnalysis.is_current))
    }
    failed_only = {
        rid
        for (rid,) in s.execute(
            select(ReviewAnalysis.review_id).where(ReviewAnalysis.status == "failed")
        )
    } - current
    by_provider = dict(
        s.execute(
            select(ReviewAnalysis.provider, func.count())
            .where(ReviewAnalysis.is_current)
            .group_by(ReviewAnalysis.provider)
        ).all()
    )
    return {
        "eligible": len(eligible),
        "analysed": len(eligible & current),
        "failed": len(eligible & failed_only),
        "pending": len(eligible - current - failed_only),
        "no_text": len(rows) - len(eligible),
        "current_by_provider": by_provider,
    }
