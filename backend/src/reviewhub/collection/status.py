"""Collection reliability report for the Data & Collection page (read-only).

Answers, per property and per strategy: when did we last try, what happened (SUCCESS / BLOCKED
/ FAILED, with the exact reason), when did we last succeed, when may we try again, and how much
of Booking.com's published review count we actually hold. Derived entirely from ``run`` /
``run_property`` / ``score_snapshot`` / ``review``; nothing here contacts any site.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reviewhub.core.settings import Settings
from reviewhub.persistence.models import Property, Review, Run, RunProperty, ScoreSnapshot

from .service import SOURCE

#: (key, label, how it works, run filter)
STRATEGIES: list[dict[str, str]] = [
    {
        "key": "browser_headed",
        "label": "Browser collector (visible window)",
        "detail": "Opens each public property page in an ordinary browser, sorts reviews newest "
        "first and pages through them slowly. Individual reviews. Needs a desktop session.",
    },
    {
        "key": "browser_headless",
        "label": "Browser collector (headless / server)",
        "detail": "Same collector without a window, for unattended servers. Booking.com refuses "
        'the review list to headless browsers ("Request not supported"); recorded as BLOCKED.',
    },
    {
        "key": "http_structured",
        "label": "Public page check (HTTPS + structured data)",
        "detail": "One plain request per property page for the published score in schema.org "
        "data. Booking.com answers non-browser clients with an AWS WAF challenge; recorded as "
        "BLOCKED, never solved.",
    },
]


def strategy_of(run: Run) -> str:
    params = run.params or {}
    if params.get("strategy") == "http_structured" or params.get("browser_mode") == "http":
        return "http_structured"
    return "browser_headed" if params.get("browser_mode") == "headed" else "browser_headless"


def block_reason(error: str | None) -> str | None:
    """Short machine-readable reason for a BLOCKED result, from the recorded message."""
    if not error:
        return None
    e = error.lower()
    if "waf" in e:
        return "aws_waf_challenge"
    if "captcha" in e or "human-verification" in e:
        return "captcha"
    if "request not supported" in e:
        return "headless_refused"
    if "http 4" in e or "access refused" in e or "access denied" in e:
        return "access_denied"
    if "cooldown" in e or "no request made" in e:
        return "cooldown"
    if "not requested" in e or "another property was refused" in e:
        return "circuit_breaker"
    return None


def _run_out(run: Run, rp: RunProperty | None = None) -> dict[str, Any]:
    error = (rp.error if rp is not None else None) or run.error
    status = rp.status if rp is not None else run.status
    return {
        "run_id": run.id,
        "strategy": strategy_of(run),
        "status": status,
        "started_at": run.started_at,
        "finished_at": (rp.finished_at if rp is not None else None) or run.finished_at,
        "pages": rp.pages if rp is not None else None,
        "received": rp.received if rp is not None else None,
        "new": rp.new if rp is not None else None,
        "unchanged": rp.unchanged if rp is not None else None,
        "rejected": rp.rejected if rp is not None else None,
        "error": error,
        "block_reason": block_reason(error) if status == "blocked" else None,
        "skipped_for_cooldown": bool((run.params or {}).get("cooldown_skip")),
    }


def collection_status(s: Session, settings: Settings, now: datetime) -> dict[str, Any]:
    runs = s.scalars(
        select(Run).where(Run.kind == "collect", Run.source == SOURCE).order_by(Run.id.desc())
    ).all()
    per_run_props: dict[int, dict[int, RunProperty]] = {}
    for rp in s.scalars(
        select(RunProperty).where(RunProperty.run_id.in_([r.id for r in runs] or [0]))
    ):
        per_run_props.setdefault(rp.run_id, {})[rp.property_id] = rp

    hours = settings.collect_block_cooldown_hours
    strategies = []
    for spec in STRATEGIES:
        mine = [r for r in runs if strategy_of(r) == spec["key"] and r.status != "running"]
        real = [r for r in mine if not (r.params or {}).get("cooldown_skip")]
        last = mine[0] if mine else None
        last_real = real[0] if real else None
        last_ok = next((r for r in real if r.status in ("success", "partial")), None)
        cooldown_until = None
        if last_real is not None and last_real.status == "blocked" and hours > 0:
            until = (last_real.finished_at or last_real.started_at) + timedelta(hours=hours)
            cooldown_until = until if until > now else None
        state = (
            "never_run"
            if last_real is None
            else "cooling_down"
            if cooldown_until
            else "blocked"
            if last_real.status == "blocked"
            else "ok"
            if last_real.status in ("success", "partial")
            else "failed"
        )
        strategies.append(
            {
                **spec,
                "state": state,
                "last_attempt": _run_out(last) if last else None,
                "last_real_attempt": _run_out(last_real) if last_real else None,
                "last_success_at": (last_ok.finished_at if last_ok else None),
                "cooldown_until": cooldown_until,
                "next_eligible_at": cooldown_until or now,
                "block_reason": block_reason(last_real.error)
                if last_real is not None and last_real.status == "blocked"
                else None,
            }
        )

    inbox = Path(settings.collect_inbox_dir)
    waiting = (
        sorted(
            p.name for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in {".csv", ".json"}
        )
        if inbox.is_dir()
        else []
    )
    imports = s.scalars(select(Run).where(Run.kind == "import").order_by(Run.id.desc())).all()
    file_imports = [r for r in imports if r.trigger in ("api", "cli", "inbox", "feed")]

    properties = []
    for prop in s.scalars(select(Property).where(Property.active).order_by(Property.id)):
        held = dict(
            s.execute(
                select(Review.source, func.count())
                .where(Review.property_id == prop.id)
                .group_by(Review.source)
            ).all()
        )
        newest, oldest = s.execute(
            select(
                func.max(Review.published_date_local), func.min(Review.published_date_local)
            ).where(Review.property_id == prop.id, Review.source == SOURCE)
        ).one()
        snap = s.scalars(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.property_id == prop.id, ScoreSnapshot.metric == "overall")
            .order_by(ScoreSnapshot.captured_at.desc())
            .limit(1)
        ).first()
        attempts = [
            (r, per_run_props[r.id][prop.id])
            for r in runs
            if prop.id in per_run_props.get(r.id, {}) and r.status != "running"
        ]
        last_attempt = attempts[0] if attempts else None
        last_success = next(((r, rp) for r, rp in attempts if rp.status == "success"), None)
        published_total = snap.review_count if snap else None
        if published_total is None:
            published_total = next(
                (rp.reported_headline_total for _, rp in attempts if rp.reported_headline_total),
                None,
            )
        public = held.get(SOURCE, 0)
        properties.append(
            {
                "property": {
                    "id": prop.id,
                    "slug": prop.slug,
                    "name": prop.name,
                    "url": prop.booking_url,
                },
                "reviews_held": {"total": sum(held.values()), "by_source": held},
                "collected_range": {"oldest": oldest, "newest": newest},
                "booking_published": {
                    "score": snap.value if snap else None,
                    "review_count": published_total,
                    "captured_at": snap.captured_at if snap else None,
                    "note": "Calculated and published by Booking.com; not an average of our data.",
                },
                "share_of_published_collected": round(public / published_total, 3)
                if published_total
                else None,
                "last_attempt": _run_out(*last_attempt) if last_attempt else None,
                "last_success": _run_out(*last_success) if last_success else None,
            }
        )
    return {
        "data": {
            "properties": properties,
            "strategies": strategies,
            "authorised_sources": {
                "file_import": {
                    "label": "Upload a CSV/JSON export (Extranet, channel manager)",
                    "runs": len(file_imports),
                    "last_at": file_imports[0].started_at if file_imports else None,
                },
                "inbox": {
                    "label": "Inbox folder for scheduled owner exports",
                    "path": str(inbox),
                    "waiting_files": waiting[:20],
                },
                "feed": {
                    "label": "Authorised partner / channel-manager feed",
                    "configured": bool(settings.collect_feed_url),
                },
            },
            "cooldown_hours": hours,
            "policy": "A CAPTCHA, WAF challenge or explicit refusal stops the run, is recorded as "
            "BLOCKED and starts a cooldown. Nothing is solved, spoofed or retried around it.",
        },
        "meta": {"generated_at": now},
    }
