"""Analytics computed directly from persisted reviews and analyses.

Rules (brief section 10):
* Every number is calculated here from stored records. Model text (summaries, suggested
  actions) is displayed, never used to compute a KPI.
* Scope: production data only unless ``include_demo`` is set; provenance is always returned.
* A property's weeks and dates use its own timezone. Current-week comparisons use the SAME
  elapsed local time of the previous week (wall-clock, DST-safe), not a full week.
* Sample-size states (proposal §4.5): averages need n>=5 (low_sample until 15); proportions
  need a denominator >=10 (low_sample until 30). Insufficient data is never reported as zero.
* Aspect confidences are model-reported, not calibrated probabilities.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from reviewhub.core.settings import REPORTING_TIMEZONE
from reviewhub.core.timeutil import get_zone
from reviewhub.intelligence.service import has_text
from reviewhub.persistence.base import TOPIC_KEYS
from reviewhub.persistence.models import (
    Property,
    Review,
    ReviewAnalysis,
    ReviewAspect,
    Run,
    ScoreSnapshot,
)
from reviewhub.persistence.scopes import dataset_provenance, reportable

POSITIVE_MIN, NEGATIVE_MAX = 8.0, 6.0  # rating bands (D5): >=8 positive, <=6 negative, 7 neutral
USABLE = ("ok", "needs_review")
TOPIC_LABELS = {k: k.replace("_", " ").capitalize() for k in TOPIC_KEYS} | {
    "check_in": "Check-in",
    "room_condition": "Room condition",
}


@dataclass
class An:
    id: int
    overall: str
    language: str | None
    provider: str
    status: str
    confidence: float | None
    summary: str | None
    complaint: str | None
    complaint_topic: str | None
    action: str | None
    flags: list[str]
    aspects: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Rv:
    id: int
    property_id: int
    slug: str
    name: str
    tz: str
    rating: float
    published_at: datetime
    local_date: date
    week: date
    language_source: str | None
    title: str | None
    liked: str | None
    disliked: str | None
    reply: str | None
    traveller: str | None
    room_type: str | None = None
    nights: int | None = None
    source: str = ""
    analysis: An | None = None

    @property
    def band(self) -> str:
        return (
            "positive"
            if self.rating >= POSITIVE_MIN
            else "negative"
            if self.rating <= NEGATIVE_MAX
            else "neutral"
        )


def aspects_of(r: Rv) -> list[dict[str, Any]]:
    return r.analysis.aspects if r.analysis else []


# ----------------------------------------------------------------------------- loading
def load(
    s: Session, *, include_demo: bool = False, property_id: int | None = None
) -> tuple[list[Rv], int]:
    """Reviews in reporting scope (with their current usable analysis) and how many were excluded by scope."""
    q = (
        select(Review, Property)
        .join(Property, Property.id == Review.property_id)
        .where(reportable(include_demo=include_demo))
        .order_by(Review.published_at.desc(), Review.id)
    )
    if property_id is not None:
        q = q.where(Review.property_id == property_id)
    rows = [
        Rv(
            r.id,
            r.property_id,
            p.slug,
            p.name,
            p.timezone,
            float(r.rating),
            r.published_at,
            r.published_date_local,
            r.published_week_local,
            r.language_source,
            r.title,
            r.positive_text,
            r.negative_text,
            r.management_reply,
            r.traveller_type,
            r.room_type,
            r.nights,
            r.source,
        )
        for r, p in s.execute(q)
    ]
    by_id = {r.id: r for r in rows}
    if by_id:
        ans = s.execute(
            select(ReviewAnalysis).where(
                ReviewAnalysis.is_current,
                ReviewAnalysis.status.in_(USABLE),
                ReviewAnalysis.review_id.in_(list(by_id)),
            )
        ).scalars()
        a_by_review: dict[int, An] = {}
        for row in ans:
            if row.review_id in by_id and row.overall_sentiment:
                a_by_review[row.review_id] = An(
                    row.id,
                    row.overall_sentiment,
                    row.language,
                    row.provider,
                    row.status,
                    row.confidence,
                    row.summary_en,
                    row.main_complaint,
                    row.main_complaint_topic,
                    row.suggested_action,
                    [f for f in (row.flags or []) if not f.startswith(("upstream:", "served_by:"))],
                )
        by_analysis = {a.id: a for a in a_by_review.values()}
        for asp in s.execute(
            select(ReviewAspect).where(ReviewAspect.analysis_id.in_(by_analysis))
        ).scalars():
            by_analysis[asp.analysis_id].aspects.append(
                {
                    "topic": asp.topic,
                    "sentiment": asp.sentiment,
                    "certainty": asp.certainty,
                    "confidence": asp.confidence,
                    "evidence": asp.evidence,
                }
            )
        for rid, found in a_by_review.items():
            by_id[rid].analysis = found
    total = int(s.scalar(select(func.count()).select_from(Review)) or 0)
    return rows, total - int(
        s.scalar(
            select(func.count()).select_from(Review).where(reportable(include_demo=include_demo))
        )
        or 0
    )


def in_range(rows: list[Rv], date_from: date | None, date_to: date | None) -> list[Rv]:
    return [
        r
        for r in rows
        if (date_from is None or r.local_date >= date_from)
        and (date_to is None or r.local_date <= date_to)
    ]


# ----------------------------------------------------------------------------- metrics
def avg_metric(values: list[float]) -> dict[str, Any]:
    n = len(values)
    status = "insufficient_data" if n < 5 else "low_sample" if n < 15 else "ok"
    out: dict[str, Any] = {"value": None, "n": n, "status": status, "ci95": None}
    if n >= 5:
        mean = statistics.mean(values)
        half = 1.96 * statistics.stdev(values) / math.sqrt(n)
        out["value"], out["ci95"] = (
            round(mean, 2),
            [round(max(1.0, mean - half), 2), round(min(10.0, mean + half), 2)],
        )
    return out


def share_metric(num: int, den: int) -> dict[str, Any]:
    status = "insufficient_data" if den < 10 else "low_sample" if den < 30 else "ok"
    return {
        "value": round(num / den, 3) if den >= 10 else None,
        "count": num,
        "of": den,
        "status": status,
    }


def bands(rows: list[Rv]) -> dict[str, int]:
    c = Counter(r.band for r in rows)
    return {k: c.get(k, 0) for k in ("positive", "neutral", "negative")}


def ai_sentiment(rows: list[Rv]) -> dict[str, Any]:
    c = Counter(r.analysis.overall for r in rows if r.analysis)
    n = sum(c.values())
    return {
        "counts": {k: c.get(k, 0) for k in ("positive", "negative", "neutral", "mixed")},
        "analysed": n,
        "of": len(rows),
    }


def rating_distribution(rows: list[Rv]) -> dict[str, int]:
    c = Counter(int(r.rating) for r in rows)
    return {str(i): c.get(i, 0) for i in range(1, 11)}


def topic_table(rows: list[Rv]) -> dict[str, dict[str, int]]:
    """Unique reviews per topic and aspect sentiment."""
    t: dict[str, Counter[str]] = {k: Counter() for k in TOPIC_KEYS}
    for r in rows:
        if r.analysis:
            for a in aspects_of(r):
                t[a["topic"]][a["sentiment"]] += 1
    return {
        k: {s: v.get(s, 0) for s in ("positive", "negative", "neutral", "mixed")}
        for k, v in t.items()
    }


def _top(
    table: dict[str, dict[str, int]], sentiments: tuple[str, ...], k: int = 3
) -> list[dict[str, Any]]:
    scored = sorted(((sum(v[s] for s in sentiments), t) for t, v in table.items()), reverse=True)
    return [{"topic": t, "label": TOPIC_LABELS[t], "reviews": n} for n, t in scored[:k] if n > 0]


def evidence_refs(
    rows: list[Rv], topic: str, sentiments: tuple[str, ...], k: int = 3
) -> list[dict[str, Any]]:
    """Verbatim quotes supporting a topic with the wanted polarity.

    A *mixed* aspect quotes both sides (e.g. "comfortable bedding" and "tiny shower"), so for a
    complaint only a quote from the guest's "disliked" answer is used, and for praise only one
    from "liked"; a mixed aspect without such a quote is not used as evidence at all.
    """
    want_field = "disliked" if "negative" in sentiments else "liked"
    cands: list[tuple[float, dict[str, Any]]] = []
    for r in rows:
        if r.analysis:
            for a in aspects_of(r):
                if a["topic"] == topic and a["sentiment"] in sentiments and a["evidence"]:
                    evs = list(a["evidence"])
                    if a["sentiment"] == "mixed":
                        evs = [e for e in evs if e.get("field") == want_field]
                        if not evs:
                            continue
                    else:
                        evs.sort(key=lambda e: e.get("field") != want_field)
                    ev = evs[0]
                    cands.append(
                        (
                            a["confidence"] or 0,
                            {
                                "review_id": r.id,
                                "property": r.slug,
                                "published": r.local_date.isoformat(),
                                "rating": r.rating,
                                "sentiment": a["sentiment"],
                                "quote": ev["quote"],
                                "field": ev["field"],
                            },
                        )
                    )
    return [c for _, c in sorted(cands, key=lambda x: (-x[0], x[1]["review_id"]))[:k]]


def coverage_for(rows: list[Rv]) -> dict[str, Any]:
    eligible = [r for r in rows if has_text(r.title, r.liked, r.disliked)]
    analysed = [r for r in eligible if r.analysis]
    return {
        "eligible": len(eligible),
        "analysed": len(analysed),
        "pending": len(eligible) - len(analysed),
        "no_text": len(rows) - len(eligible),
        "share": round(len(analysed) / len(eligible), 3) if eligible else None,
    }


# ----------------------------------------------------------------------------- week windows (like-for-like)
def week_windows(as_of: datetime, tz_name: str) -> dict[str, datetime]:
    zone: ZoneInfo = get_zone(tz_name)
    local = as_of.astimezone(zone)
    monday = datetime.combine(
        local.date() - timedelta(days=local.weekday()), time.min
    )  # naive local wall-clock
    naive = local.replace(tzinfo=None)

    def u(x: datetime) -> datetime:
        return x.replace(tzinfo=zone).astimezone(UTC)

    return {
        "cur_start": u(monday),
        "cur_end": as_of.astimezone(UTC),
        "prev_start": u(monday - timedelta(days=7)),
        "prev_like_end": u(naive - timedelta(days=7)),
        "prev_full_end": u(monday),
    }


def week_comparison(rows: list[Rv], as_of: datetime) -> dict[str, Any]:
    cur: list[float] = []
    prev_l: list[float] = []
    prev_f: list[float] = []
    tz_used = set()
    for slug_rows in _by_property(rows).values():
        tz = slug_rows[0].tz
        tz_used.add(tz)
        w = week_windows(as_of, tz)
        for r in slug_rows:
            if w["cur_start"] <= r.published_at < w["cur_end"]:
                cur.append(r.rating)
            if w["prev_start"] <= r.published_at < w["prev_like_end"]:
                prev_l.append(r.rating)
            if w["prev_start"] <= r.published_at < w["prev_full_end"]:
                prev_f.append(r.rating)
    c, p = avg_metric(cur), avg_metric(prev_l)
    delta = (
        round(c["value"] - p["value"], 2)
        if c["value"] is not None and p["value"] is not None
        else None
    )
    return {
        "basis": "current week-to-date vs the same elapsed local time of the previous week (wall-clock, each property's timezone)",
        "timezones": sorted(tz_used),
        "current_week_to_date": c | {"reviews": len(cur)},
        "previous_week_same_elapsed": p | {"reviews": len(prev_l)},
        "previous_week_full": avg_metric(prev_f) | {"reviews": len(prev_f)},
        "avg_rating_delta": delta,
        "delta_status": "insufficient_data"
        if delta is None
        else "indicative"
        if min(c["n"], p["n"]) < 15
        else "ok",
        "note": "A difference is not evidence of a cause."
        + ("" if delta is not None else " Not enough reviews in one or both windows."),
    }


def _by_property(rows: list[Rv]) -> dict[int, list[Rv]]:
    d: dict[int, list[Rv]] = defaultdict(list)
    for r in rows:
        d[r.property_id].append(r)
    return d


def analysis_method(provider: str) -> str:
    """How an analysis was produced, in words the dashboard can show: never call the rule-based
    lexicon or the test mock "AI"."""
    return {"builtin": "rule_based", "mock": "mock"}.get(provider, "ai")


def meta(
    s: Session, rows: list[Rv], excluded: int, include_demo: bool, as_of: datetime | None = None
) -> dict[str, Any]:
    prov = dataset_provenance(s)
    providers = Counter(r.analysis.provider for r in rows if r.analysis)
    methods = Counter(analysis_method(r.analysis.provider) for r in rows if r.analysis)
    notice = None
    if prov["contains_demo"] and include_demo:
        notice = "Includes SYNTHETIC demo data: fictional reviews, not genuine guest feedback."
    elif excluded and not rows:
        notice = f"{excluded} review(s) exist but are excluded from reporting (demo/experimental). Use include_demo=true to see demo data."
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "as_of": (as_of or datetime.now(UTC)).isoformat(timespec="seconds"),
        "scope": {
            "include_demo": include_demo,
            "reviews_in_scope": len(rows),
            "excluded_by_scope": excluded,
        },
        "dataset": prov,
        "analysis": {
            "providers": dict(providers),
            "methods": {m: methods.get(m, 0) for m in ("ai", "rule_based", "mock")},
            "contains_mock_analysis": "mock" in providers,
            "contains_rule_based_analysis": "builtin" in providers,
        },
        "notice": notice,
        "reporting_timezone": str(REPORTING_TIMEZONE),
    }


# ----------------------------------------------------------------------------- A. overview
def overview(
    s: Session, *, include_demo: bool, date_from: date | None, date_to: date | None, as_of: datetime
) -> dict[str, Any]:
    all_rows, excluded = load(s, include_demo=include_demo)
    rows = in_range(all_rows, date_from, date_to)
    table = topic_table(rows)
    recs = recommendations_from(all_rows, as_of, window_weeks=12, min_reviews=3)
    return {
        "data": {
            "period": {"from": date_from, "to": date_to},
            "reviews": len(rows),
            "avg_rating": avg_metric([r.rating for r in rows]),
            "avg_rating_note": "Mean of individual guest ratings (1-10). Booking.com's own published score is reported separately per property.",
            "rating_distribution": rating_distribution(rows),
            "rating_bands": bands(rows),
            "ai_sentiment": ai_sentiment(rows),
            "week_comparison": week_comparison(all_rows, as_of),
            "volume": {
                "last_28_days": sum(
                    1 for r in all_rows if as_of - timedelta(days=28) <= r.published_at < as_of
                ),
                "previous_28_days": sum(
                    1
                    for r in all_rows
                    if as_of - timedelta(days=56) <= r.published_at < as_of - timedelta(days=28)
                ),
            },
            "coverage": coverage_for(rows),
            "top_positive_topics": _top(table, ("positive",)),
            "top_negative_topics": _top(table, ("negative", "mixed")),
            "operational_concerns": recs[:5],
        },
        "meta": meta(s, all_rows, excluded, include_demo, as_of),
    }


# ----------------------------------------------------------------------------- B/C. properties
def _property_block(rows: list[Rv], as_of: datetime) -> dict[str, Any]:
    table = topic_table(rows)
    recent = [r for r in rows if as_of - timedelta(days=28) <= r.published_at < as_of]
    prior = [
        r for r in rows if as_of - timedelta(days=56) <= r.published_at < as_of - timedelta(days=28)
    ]
    return {
        "reviews": len(rows),
        "avg_rating": avg_metric([r.rating for r in rows]),
        "rating_bands": bands(rows),
        "ai_sentiment": ai_sentiment(rows),
        "volume_trend": {
            "last_28_days": len(recent),
            "previous_28_days": len(prior),
            "direction": "insufficient_data"
            if len(recent) + len(prior) < 6
            else "up"
            if len(recent) - len(prior) >= 3
            else "down"
            if len(prior) - len(recent) >= 3
            else "steady",
        },
        "most_positive_aspects": _top(table, ("positive",)),
        "most_negative_aspects": _top(table, ("negative", "mixed")),
        "coverage": coverage_for(rows),
    }


def _booking_score(s: Session, property_id: int) -> dict[str, Any] | None:
    row = (
        s.execute(
            select(ScoreSnapshot)
            .where(ScoreSnapshot.property_id == property_id, ScoreSnapshot.metric == "overall")
            .order_by(ScoreSnapshot.captured_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    return (
        {
            "value": row.value,
            "captured_at": row.captured_at,
            "label": "Booking.com published score (calculated by Booking, not by us)",
        }
        if row
        else None
    )


def properties_comparison(
    s: Session, *, include_demo: bool, date_from: date | None, date_to: date | None, as_of: datetime
) -> dict[str, Any]:
    all_rows, excluded = load(s, include_demo=include_demo)
    props = s.execute(select(Property).where(Property.active).order_by(Property.id)).scalars().all()
    out = []
    for p in props:
        rows = in_range([r for r in all_rows if r.property_id == p.id], date_from, date_to)
        out.append(
            {
                "property": {"id": p.id, "slug": p.slug, "name": p.name},
                **_property_block(rows, as_of),
                "booking_score": _booking_score(s, p.id),
            }
        )
    return {"data": out, "meta": meta(s, all_rows, excluded, include_demo, as_of)}


def property_detail(
    s: Session,
    property_id: int,
    *,
    include_demo: bool,
    date_from: date | None,
    date_to: date | None,
    as_of: datetime,
) -> dict[str, Any] | None:
    p = s.get(Property, property_id)
    if p is None:
        return None
    all_rows, excluded = load(s, include_demo=include_demo, property_id=property_id)
    rows = in_range(all_rows, date_from, date_to)
    table = topic_table(rows)
    return {
        "data": {
            "property": {"id": p.id, "slug": p.slug, "name": p.name, "timezone": p.timezone},
            **_property_block(rows, as_of),
            "booking_score": _booking_score(s, p.id),
            "rating_distribution": rating_distribution(rows),
            "week_comparison": week_comparison(all_rows, as_of),
            "topics": [{"topic": t, "label": TOPIC_LABELS[t], **v} for t, v in table.items()],
            "recommendations": recommendations_from(
                all_rows, as_of, window_weeks=12, min_reviews=3
            ),
        },
        "meta": meta(s, all_rows, excluded, include_demo, as_of),
    }


# ----------------------------------------------------------------------------- D. weekly trends
def weekly_trends(
    s: Session,
    *,
    include_demo: bool,
    property_id: int | None,
    date_from: date | None,
    date_to: date | None,
    as_of: datetime,
) -> dict[str, Any]:
    all_rows, excluded = load(s, include_demo=include_demo, property_id=property_id)
    tz = all_rows[0].tz if property_id is not None and all_rows else str(REPORTING_TIMEZONE)
    today = as_of.astimezone(get_zone(tz)).date()
    end = date_to or today
    start = date_from or (end - timedelta(weeks=26))
    rows = in_range(all_rows, start, end)
    by_week: dict[date, list[Rv]] = defaultdict(list)
    for r in rows:
        by_week[r.week].append(r)
    first = start - timedelta(days=start.weekday())
    this_monday = today - timedelta(days=today.weekday())
    weeks, prev = [], None
    d = first
    while d <= end:
        wr = by_week.get(d, [])
        point: dict[str, Any] = {
            "week_start": d,
            "reviews": len(wr),
            "avg_rating": avg_metric([r.rating for r in wr]),
            "rating_bands": bands(wr),
            "ai_sentiment": ai_sentiment(wr)["counts"],
            "partial": d == this_monday,
        }
        if prev is not None:
            pv, cv = prev["avg_rating"]["value"], point["avg_rating"]["value"]
            point["week_on_week"] = {
                "volume_delta": point["reviews"] - prev["reviews"],
                "avg_rating_delta": round(cv - pv, 2)
                if cv is not None and pv is not None
                else None,
            }
        if point["partial"]:
            wc = week_comparison(all_rows, as_of)
            point["like_for_like_previous_week"] = {
                "reviews": wc["previous_week_same_elapsed"]["reviews"],
                "avg_rating": wc["previous_week_same_elapsed"],
            }
        weeks.append(point)
        prev, d = point, d + timedelta(days=7)
    return {
        "data": {
            "timezone": tz,
            "from": start,
            "to": end,
            "weeks": weeks,
            "note": "Weeks start Monday in the property's timezone. The current week is partial; compare it with like_for_like_previous_week, not the full previous week.",
        },
        "meta": meta(s, all_rows, excluded, include_demo, as_of),
    }


# ----------------------------------------------------------------------------- E. topics
def topics_insight(
    s: Session,
    *,
    include_demo: bool,
    property_id: int | None,
    date_from: date | None,
    date_to: date | None,
    as_of: datetime,
) -> dict[str, Any]:
    all_rows, excluded = load(s, include_demo=include_demo, property_id=property_id)
    rows = in_range(all_rows, date_from, date_to)
    analysed = [r for r in rows if r.analysis]
    neg_band = [r for r in analysed if r.band == "negative"]
    table = topic_table(rows)
    out = []
    for t in TOPIC_KEYS:
        complaints = Counter(
            (r.analysis.complaint or "").strip().lower()
            for r in analysed
            if r.analysis and r.analysis.complaint_topic == t and r.analysis.complaint
        )
        neg_reviews = (
            [
                r
                for r in neg_band
                if any(
                    a["topic"] == t and a["sentiment"] in ("negative", "mixed")
                    for a in aspects_of(r)
                )
            ]
            if neg_band
            else []
        )
        per_prop: dict[str, dict[str, int]] = {}
        for prs in _by_property(analysed).values():
            c = Counter(
                a["sentiment"]
                for r in prs
                for a in (aspects_of(r) if r.analysis else [])
                if a["topic"] == t
            )
            per_prop[prs[0].slug] = {
                k: c.get(k, 0) for k in ("positive", "negative", "neutral", "mixed")
            }
        out.append(
            {
                "topic": t,
                "label": TOPIC_LABELS[t],
                **table[t],
                "reviews_mentioning": sum(table[t].values()),
                "share_of_analysed_reviews": share_metric(sum(table[t].values()), len(analysed)),
                "share_of_negative_rated_reviews": share_metric(len(neg_reviews), len(neg_band)),
                "most_common_complaints": [
                    {"complaint": c, "count": n} for c, n in complaints.most_common(5)
                ],
                "praise_evidence": evidence_refs(rows, t, ("positive",)),
                "complaint_evidence": evidence_refs(rows, t, ("negative", "mixed")),
                "by_property": per_prop,
            }
        )
    return {
        "data": {
            "analysed_reviews": len(analysed),
            "reviews_in_period": len(rows),
            "coverage": coverage_for(rows),
            "most_praised": _top(table, ("positive",), 8),
            "most_criticised": _top(table, ("negative", "mixed"), 8),
            "topics": out,
            "note": "A review can mention several topics, so shares do not sum to 100%. Counts are unique reviews.",
        },
        "meta": meta(s, all_rows, excluded, include_demo, as_of),
    }


# ----------------------------------------------------------------------------- F. review explorer
def review_explorer(
    s: Session,
    *,
    include_demo: bool,
    property_id: int | None,
    rating_min: float | None,
    rating_max: float | None,
    sentiment: str | None,
    topic: str | None,
    topic_sentiment: str | None,
    date_from: date | None,
    date_to: date | None,
    language: str | None,
    page: int,
    page_size: int,
    as_of: datetime,
    q: str | None = None,
    sort: str = "newest",
) -> dict[str, Any]:
    all_rows, excluded = load(s, include_demo=include_demo, property_id=property_id)
    rows = in_range(all_rows, date_from, date_to)
    needle = q.strip().casefold() if q else ""

    def keep(r: Rv) -> bool:
        if (
            needle
            and needle not in " ".join(x for x in (r.title, r.liked, r.disliked) if x).casefold()
        ):
            return False
        if (rating_min is not None and r.rating < rating_min) or (
            rating_max is not None and r.rating > rating_max
        ):
            return False
        if sentiment and (r.analysis is None or r.analysis.overall != sentiment):
            return False
        if topic and (
            r.analysis is None
            or not any(
                a["topic"] == topic
                and (topic_sentiment is None or a["sentiment"] == topic_sentiment)
                for a in aspects_of(r)
            )
        ):
            return False
        return not (
            language
            and ((r.analysis.language if r.analysis else None) or r.language_source) != language
        )

    hits = [r for r in rows if keep(r)]
    if sort == "oldest":
        hits.sort(key=lambda r: r.published_at)
    elif sort == "rating_high":
        hits.sort(key=lambda r: (-r.rating, -r.published_at.timestamp()))
    elif sort == "rating_low":
        hits.sort(key=lambda r: (r.rating, -r.published_at.timestamp()))
    else:
        hits.sort(key=lambda r: r.published_at, reverse=True)
    total = len(hits)
    page_rows = hits[(page - 1) * page_size : page * page_size]
    items = [
        {
            "id": r.id,
            "property": {"id": r.property_id, "slug": r.slug, "name": r.name},
            "rating": r.rating,
            "rating_band": r.band,
            "published_at": r.published_at,
            "local_date": r.local_date,
            "language": (r.analysis.language if r.analysis else None) or r.language_source,
            "traveller_type": r.traveller,
            "room_type": r.room_type,
            "nights": r.nights,
            "source": r.source,
            "title": r.title,
            "liked": r.liked,
            "disliked": r.disliked,
            "management_reply": r.reply,
            "analysis": None
            if r.analysis is None
            else {
                "overall_sentiment": r.analysis.overall,
                "summary_en": r.analysis.summary,
                "provider": r.analysis.provider,
                "method": analysis_method(r.analysis.provider),
                "status": r.analysis.status,
                "confidence_model_reported": r.analysis.confidence,
                "flags": r.analysis.flags,
                "main_complaint": r.analysis.complaint,
                "main_complaint_topic": r.analysis.complaint_topic,
                "suggested_action": r.analysis.action,
                "aspects": aspects_of(r),
            },
        }
        for r in page_rows
    ]
    return {
        "data": items,
        "meta": {
            **meta(s, all_rows, excluded, include_demo, as_of),
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": math.ceil(total / page_size) if total else 0,
            },
        },
    }


# ----------------------------------------------------------------------------- G. recommendations
def recommendations_from(
    rows: list[Rv], as_of: datetime, *, window_weeks: int, min_reviews: int
) -> list[dict[str, Any]]:
    start = as_of - timedelta(weeks=window_weeks)
    window = [r for r in rows if start <= r.published_at <= as_of]
    recs: list[dict[str, Any]] = []
    for pid, prs in _by_property(window).items():
        analysed = [r for r in prs if r.analysis]
        for t in TOPIC_KEYS:
            support = [
                r
                for r in analysed
                if any(
                    a["topic"] == t and a["sentiment"] in ("negative", "mixed")
                    for a in aspects_of(r)
                )
            ]
            if len(support) < min_reviews:
                continue
            recent = sum(1 for r in support if r.published_at >= as_of - timedelta(weeks=4))
            earlier = sum(
                1
                for r in support
                if as_of - timedelta(weeks=8) <= r.published_at < as_of - timedelta(weeks=4)
            )
            trend = (
                "rising"
                if recent - earlier >= 2
                else "falling"
                if earlier - recent >= 2
                else "steady"
            )
            actions = Counter(
                r.analysis.action.strip()
                for r in support
                if r.analysis and r.analysis.action and r.analysis.complaint_topic == t
            )
            confs = [
                a["confidence"]
                for r in support
                for a in aspects_of(r)
                if a["topic"] == t and a["confidence"] is not None
            ]
            flagged = sum(1 for r in support if r.analysis and r.analysis.status == "needs_review")
            recs.append(
                {
                    "property": {"id": pid, "slug": prs[0].slug, "name": prs[0].name},
                    "topic": t,
                    "label": TOPIC_LABELS[t],
                    "problem": f"{len(support)} of {len(analysed)} analysed reviews in the last {window_weeks} weeks raise {TOPIC_LABELS[t].lower()} concerns.",
                    "supporting_reviews": len(support),
                    "analysed_reviews": len(analysed),
                    "share": share_metric(len(support), len(analysed)),
                    "trend": {
                        "direction": trend,
                        "last_4_weeks": recent,
                        "previous_4_weeks": earlier,
                        "note": "A trend in mentions is an association, not proof of cause.",
                    },
                    "evidence": evidence_refs(support, t, ("negative", "mixed")),
                    "review_ids": [r.id for r in support[:20]],
                    "suggested_action": actions.most_common(1)[0][0] if actions else None,
                    "action_basis": "most frequent action suggested by the model across the supporting reviews"
                    if actions
                    else "none of the supporting reviews carried a justified action",
                    "confidence": {
                        "model_reported_mean": round(statistics.mean(confs), 2) if confs else None,
                        "calibrated": False,
                    },
                    "uncertainty": ([] if len(support) >= 5 else ["low_sample"])
                    + (["some_reviews_flagged_needs_review"] if flagged else [])
                    + (
                        ["mock_analysis"]
                        if any(r.analysis and r.analysis.provider == "mock" for r in support)
                        else []
                    ),
                }
            )
    return sorted(recs, key=lambda x: (-x["supporting_reviews"], x["property"]["id"], x["topic"]))


def recommendations(
    s: Session,
    *,
    include_demo: bool,
    property_id: int | None,
    window_weeks: int,
    min_reviews: int,
    as_of: datetime,
) -> dict[str, Any]:
    rows, excluded = load(s, include_demo=include_demo, property_id=property_id)
    return {
        "data": recommendations_from(
            rows, as_of, window_weeks=window_weeks, min_reviews=min_reviews
        ),
        "meta": {
            **meta(s, rows, excluded, include_demo, as_of),
            "window_weeks": window_weeks,
            "min_supporting_reviews": min_reviews,
        },
    }


# ----------------------------------------------------------------------------- H. coverage
def coverage(
    s: Session, *, include_demo: bool, settings_cfg: dict[str, Any], as_of: datetime
) -> dict[str, Any]:
    rows, excluded = load(s, include_demo=include_demo)
    ids = [r.id for r in rows]
    cov = coverage_for(rows)
    analysed_ids = {r.id for r in rows if r.analysis}
    failed_ids = {
        rid
        for (rid,) in s.execute(
            select(ReviewAnalysis.review_id).where(ReviewAnalysis.status == "failed")
        )
        if rid in set(ids)
    } - analysed_ids
    usage = s.execute(
        select(
            ReviewAnalysis.provider,
            ReviewAnalysis.model_id,
            func.count(),
            func.coalesce(func.sum(ReviewAnalysis.input_tokens), 0),
            func.coalesce(func.sum(ReviewAnalysis.output_tokens), 0),
            func.coalesce(func.sum(ReviewAnalysis.cost_usd), 0.0),
        ).group_by(ReviewAnalysis.provider, ReviewAnalysis.model_id)
    ).all()
    last = (
        s.execute(select(Run).where(Run.kind == "analyse").order_by(Run.id.desc()).limit(1))
        .scalars()
        .first()
    )
    by_prop = {
        slug: coverage_for(prs)
        for slug, prs in {rs[0].slug: rs for rs in _by_property(rows).values()}.items()
    }
    return {
        "data": {
            "total_reviews": len(rows),
            "eligible": cov["eligible"],
            "analysed": cov["analysed"],
            "pending": cov["pending"]
            - len(failed_ids & {r.id for r in rows if has_text(r.title, r.liked, r.disliked)}),
            "failed": len(failed_ids),
            "no_text": cov["no_text"],
            "share_analysed": cov["share"],
            "active_configuration": settings_cfg,
            "analysis_providers": dict(Counter(r.analysis.provider for r in rows if r.analysis)),
            "analysis_methods": dict(
                Counter(analysis_method(r.analysis.provider) for r in rows if r.analysis)
            ),
            "last_run": None
            if last is None
            else {
                "id": last.id,
                "status": last.status,
                "provider": last.source,
                "started_at": last.started_at,
                "finished_at": last.finished_at,
                "ok": last.items_ok,
                "failed": last.items_failed,
                "total": last.items_total,
                "stopped_because": last.error,
            },
            "token_usage": [
                {
                    "provider": p,
                    "model": m,
                    "analyses": n,
                    "input_tokens": int(i or 0),
                    "output_tokens": int(o or 0),
                    "reported_cost_usd": float(c or 0),
                }
                for p, m, n, i, o, c in usage
            ],
            "by_property": by_prop,
            "note": "Each review uses one current analysis: an AI (LLM) analysis when one exists, "
            "otherwise the offline rule-based analyzer. Rule-based and mock analyses are not "
            "AI inference; mock analyses are test output. "
            "Every topic tag is backed by a verbatim quote; no accuracy is claimed without a "
            "human-labelled evaluation set.",
        },
        "meta": meta(s, rows, excluded, include_demo, as_of),
    }
