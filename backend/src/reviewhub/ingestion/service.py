"""The import pipeline: file bytes -> validated, de-duplicated reviews in the database.

    adapter.parse -> normalise/validate each row -> de-duplicate -> plan -> apply

Guarantees
* **Idempotent**: re-importing a file creates nothing; an edited row becomes exactly
  one update plus one ``review_revision``.
* **Atomic**: reviews, revisions, issues and run-property rows are written in ONE
  transaction. A rejected file, an error or a crash writes no review data.
* **Safe under concurrency**: inserts use ``ON CONFLICT DO NOTHING`` and conflicts are
  re-read and reconciled, so two imports with overlapping reviews never fail or duplicate.
* **Single-flight per file** (not per kind): the same file cannot run twice at once,
  different files can (ADR 0012).
* **Isolated**: an import can only be recorded as ``csv_import``/``json_import`` and
  refuses to mix with demo or experimental data (ADR 0017).
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, sessionmaker

from reviewhub.persistence.models import (
    DataQualityIssue,
    Property,
    Review,
    ReviewAnalysis,
    ReviewRevision,
    RunProperty,
)
from reviewhub.persistence.runs import (
    DEFAULT_STALE_AFTER,
    Clock,
    finish_run,
    heartbeat,
    start_run,
    utc_now,
)
from reviewhub.persistence.scopes import IMPORT_SOURCES, assert_can_load, dataset_provenance

from .adapters.files import choose_adapter
from .columns import REQUIRED, ColumnMap, build_column_map
from .contracts import (
    FileAdapter,
    FileError,
    FileRejected,
    ForbiddenSource,
    IgnoredColumn,
    ImportReport,
    IssueOut,
    ParsedFile,
    PropertyInfo,
    ReviewIn,
)
from .dedupe import CONTENT_FIELDS, valid_namespace
from .normalise import NormaliseContext, normalise_row
from .quality import RowIssue, issue

log = logging.getLogger(__name__)

T = TypeVar("T")

VERSION = "import/1.0.0"
MAX_ISSUES_IN_REPORT = 200
MAX_ISSUES_STORED = 5000
_CHUNK = 200
Progress = Callable[[str], None]

_MUTABLE = (
    *CONTENT_FIELDS,
    "published_date_local",
    "published_week_local",
    "search_text",
    "negative_is_filler",
    "content_hash",
    "source_payload",
)


class RunLost(RuntimeError):
    """This run was marked abandoned while it was still working; it must not write."""


@dataclass(frozen=True)
class ImportOptions:
    property: str | None = None  # default property for rows without a property column
    dry_run: bool = False
    encoding: str | None = None
    date_format: str | None = None
    id_namespace: str = "booking"
    format: str = "auto"
    trigger: str = "cli"


@dataclass
class Plan:
    new: list[ReviewIn] = field(default_factory=list)
    updates: list[tuple[Review, ReviewIn, list[str]]] = field(default_factory=list)
    unchanged: list[Review] = field(default_factory=list)


def _chunks(items: list[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


# --------------------------------------------------------------------------- reading
def _load_properties(s: Session) -> dict[str, PropertyInfo]:
    out: dict[str, PropertyInfo] = {}
    for p in s.scalars(select(Property).where(Property.active)):
        info = PropertyInfo(p.id, p.slug, p.name, p.timezone)
        out[p.slug.lower()] = info
        out[p.name.lower()] = info
    return out


def _load_existing(s: Session, rows: Iterable[ReviewIn]) -> dict[tuple[int, str], Review]:
    by_property: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        by_property[r.property.id].append(r.dedupe_key)
    found: dict[tuple[int, str], Review] = {}
    for pid, keys in by_property.items():
        for chunk in _chunks(keys, 500):
            for review in s.scalars(
                select(Review).where(Review.property_id == pid, Review.dedupe_key.in_(chunk))
            ):
                found[(pid, review.dedupe_key)] = review
    return found


def _changed_fields(review: Review, incoming: ReviewIn) -> list[str]:
    return [f for f in CONTENT_FIELDS if getattr(review, f) != getattr(incoming, f)]


def _make_plan(rows: list[ReviewIn], existing: dict[tuple[int, str], Review]) -> Plan:
    plan = Plan()
    for r in rows:
        current = existing.get((r.property.id, r.dedupe_key))
        if current is None:
            plan.new.append(r)
            continue
        changed = _changed_fields(current, r)
        if changed:
            plan.updates.append((current, r, changed))
        else:
            plan.unchanged.append(current)
    return plan


# --------------------------------------------------------------------------- writing
def _insert_for(s: Session) -> Any:
    return (
        postgresql.insert(Review)
        if s.get_bind().dialect.name == "postgresql"
        else sqlite.insert(Review)
    )


def _row_values(r: ReviewIn, source: str, run_id: int | None, now: datetime) -> dict[str, Any]:
    return {
        "property_id": r.property.id,
        "source": source,
        "dedupe_key": r.dedupe_key,
        "external_id": r.external_id,
        "source_url": r.source_url,
        "rating": r.rating,
        "published_at": r.published_at,
        "published_date_local": r.published_date_local,
        "published_week_local": r.published_week_local,
        "title": r.title,
        "positive_text": r.positive_text,
        "negative_text": r.negative_text,
        "negative_is_filler": r.negative_is_filler,
        "search_text": r.search_text,
        "language_source": r.language_source,
        "language": None,  # resolved by analysis (P4), never assumed from the source tag
        "language_method": None,
        "guest_country": r.guest_country,
        "traveller_type": r.traveller_type,
        "room_type": r.room_type,
        "checkin_date": r.checkin_date,
        "checkout_date": r.checkout_date,
        "nights": r.nights,
        "stay_status": r.stay_status,
        "management_reply": r.management_reply,
        "content_hash": r.content_hash,
        "source_payload": r.payload,
        "first_run_id": run_id,
        "last_run_id": run_id,
        "collected_at": now,
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def _apply_updates(s: Session, plan: Plan, run_id: int | None, now: datetime) -> None:
    for review, incoming, changed in plan.updates:
        previous_hash = review.content_hash
        for name in _MUTABLE:
            setattr(
                review, name, getattr(incoming, name if name != "source_payload" else "payload")
            )
        review.updated_at = now
        review.last_seen_at = now
        review.last_run_id = run_id
        s.add(
            ReviewRevision(
                review_id=review.id,
                run_id=run_id,
                previous_hash=previous_hash,
                new_hash=incoming.content_hash,
                changed_fields=changed,
                detected_at=now,
            )
        )
    text_changed = [
        rev.id
        for rev, _, changed in plan.updates
        if {"title", "positive_text", "negative_text"} & set(changed)
    ]
    if (
        text_changed
    ):  # the analysis no longer describes this text: it becomes pending again (history is kept)
        s.execute(
            update(ReviewAnalysis)
            .where(ReviewAnalysis.review_id.in_(text_changed))
            .values(is_current=False)
        )
    for chunk in _chunks([r.id for r in plan.unchanged], 500):
        s.execute(
            update(Review).where(Review.id.in_(chunk)).values(last_seen_at=now, last_run_id=run_id)
        )
    s.flush()


def _insert_new(
    s: Session, plan: Plan, source: str, run_id: int | None, now: datetime
) -> tuple[list[ReviewIn], int]:
    """Insert new rows; rows another import inserted first are reconciled. Returns (inserted, raced)."""
    inserted_rows: list[ReviewIn] = []
    raced_total = 0
    for chunk in _chunks(plan.new, _CHUNK):
        stmt = (
            _insert_for(s).on_conflict_do_nothing().returning(Review.property_id, Review.dedupe_key)
        )
        done = {
            (pid, key)
            for pid, key in s.execute(
                stmt, [_row_values(r, source, run_id, now) for r in chunk]
            ).all()
        }
        inserted_rows.extend(r for r in chunk if (r.property.id, r.dedupe_key) in done)
        raced = [r for r in chunk if (r.property.id, r.dedupe_key) not in done]
        if raced:
            raced_total += len(raced)
            existing = _load_existing(s, raced)
            for r in raced:
                current = existing.get((r.property.id, r.dedupe_key))
                if current is None:  # conflict on something other than our key: do not guess
                    raise RuntimeError(
                        f"insert conflict for {r.dedupe_key!r} but no such review found"
                    )
                changed = _changed_fields(current, r)
                if changed:
                    plan.updates.append((current, r, changed))
                else:
                    plan.unchanged.append(current)
    return inserted_rows, raced_total


def _flag_duplicate_content(
    s: Session, inserted: list[ReviewIn], properties: dict[str, PropertyInfo]
) -> list[RowIssue]:
    """DQ006: same text and rating under a different id, within one property."""
    candidates: dict[tuple[int, str], ReviewIn] = {}
    for r in inserted:
        if len((r.title or "") + (r.positive_text or "") + (r.negative_text or "")) >= 20:
            candidates[(r.property.id, r.content_hash)] = r
    by_property: dict[int, list[str]] = defaultdict(list)
    for pid, h in candidates:
        by_property[pid].append(h)
    found: list[RowIssue] = []
    for pid, hashes in by_property.items():
        for chunk in _chunks(hashes, 500):
            rows = s.execute(
                select(Review.content_hash, func.count(Review.id))
                .where(Review.property_id == pid, Review.content_hash.in_(chunk))
                .group_by(Review.content_hash)
                .having(func.count(Review.id) > 1)
            ).all()
            for h, n in rows:
                r = candidates[(pid, h)]
                found.append(
                    RowIssue(
                        line=r.line,
                        code="DQ006",
                        message=f"{n} reviews at this property share identical text and rating",
                        property_slug=r.property.slug,
                        key=f"{pid}|{h}",
                    )
                )
    return found


def _store_issues(
    s: Session,
    issues: list[RowIssue],
    *,
    file_sha: str,
    run_id: int | None,
    now: datetime,
    properties: dict[str, PropertyInfo],
) -> None:
    """Persist warnings and errors once each; re-importing the same file adds nothing."""
    by_slug = {p.slug: p for p in properties.values()}
    rows: dict[str, dict[str, Any]] = {}
    for i in issues:
        if i.severity == "info" or len(rows) >= MAX_ISSUES_STORED:
            continue
        ident = i.key or f"{file_sha[:16]}|{i.line}|{i.column}"
        fp = hashlib.sha256(f"{i.code}|{ident}|{i.property_slug}".encode()).hexdigest()[:40]
        prop = by_slug.get(i.property_slug or "")
        rows.setdefault(
            fp,
            {
                "code": i.code,
                "severity": i.severity,
                "run_id": run_id,
                "property_id": prop.id if prop else None,
                "review_id": None,
                "details": {
                    "line": i.line,
                    "column": i.column,
                    "message": i.message,
                    "file_sha256": file_sha,
                },
                "fingerprint": fp,
                "detected_at": now,
            },
        )
    if not rows:
        return
    open_now: set[str] = set()
    for chunk in _chunks(list(rows), 500):
        open_now.update(
            s.scalars(
                select(DataQualityIssue.fingerprint).where(
                    DataQualityIssue.fingerprint.in_(chunk), DataQualityIssue.resolved_at.is_(None)
                )
            )
        )
    fresh = [v for k, v in rows.items() if k not in open_now]
    if fresh:
        dialect_insert = (
            postgresql.insert if s.get_bind().dialect.name == "postgresql" else sqlite.insert
        )
        s.execute(dialect_insert(DataQualityIssue).on_conflict_do_nothing(), fresh)


# --------------------------------------------------------------------------- orchestration
def _noop(_: str) -> None:
    return None


def import_file(
    session_factory: sessionmaker[Session],
    *,
    content: bytes,
    filename: str,
    options: ImportOptions = ImportOptions(),
    adapter: FileAdapter | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    max_bytes: int = 20 * 1024 * 1024,
    clock: Clock = utc_now,
    progress: Progress = _noop,
    allowed_sources: tuple[str, ...] = IMPORT_SOURCES,
) -> ImportReport:
    """Import one file. Raises ``RunAlreadyRunning`` / ``DatasetIsolationError`` / ``ForbiddenSource``;
    file-level problems come back as ``status="failed"`` with ``file_error`` set."""
    sha = hashlib.sha256(content).hexdigest()
    report = ImportReport(
        status="failed", dry_run=options.dry_run, run_id=None, file_name=filename, file_sha256=sha
    )
    rejection: FileRejected | None = None
    try:
        if len(content) > max_bytes:
            raise FileRejected("too_large", f"File is larger than {max_bytes // (1024 * 1024)} MB.")
        if not valid_namespace(options.id_namespace):
            raise FileRejected(
                "bad_namespace", "id namespace must be 1-20 chars of a-z, 0-9, _ or -."
            )
        adapter = adapter or choose_adapter(filename, content, options.format)
    except FileRejected as exc:
        rejection = exc
    source = adapter.source if adapter else "csv_import"
    if source not in allowed_sources:
        raise ForbiddenSource(
            f"this load may only be recorded as {allowed_sources}, not {source!r}: demo and "
            "experimental data cannot enter through a file import"
        )
    report.source = source
    report.file_format = adapter.name if adapter else None

    run_id: int | None = None
    if not options.dry_run:
        with session_factory() as s:
            run = start_run(
                s,
                kind="import",
                source=source,
                scope_key=f"import:{sha}",
                mode="file",
                trigger=options.trigger,
                version=VERSION,
                params={"filename": filename, "bytes": len(content), "options": asdict(options)},
                file_sha256=sha,
                now=clock(),
                stale_after=stale_after,
            )
            run_id = run.id
        report.run_id = run_id

    error: str | None = None
    try:
        if rejection is not None:
            raise rejection
        assert adapter is not None
        _execute(session_factory, adapter, content, options, run_id, report, clock, progress)
    except FileRejected as exc:
        report.status = "failed"
        report.file_error = FileError(code=exc.code, message=exc.message)
        error = f"{exc.code}: {exc.message}"
    except BaseException as exc:
        if run_id is not None:
            with session_factory() as s:
                finish_run(
                    s, run_id, status="failed", now=clock(), error=f"{type(exc).__name__}: {exc}"
                )
        raise

    if run_id is not None:
        with session_factory() as s:
            finish_run(
                s,
                run_id,
                status=report.status,
                now=clock(),
                items_total=report.rows_total,
                items_ok=report.rows_valid,
                items_failed=report.rows_rejected,
                error=error,
                params={
                    "filename": filename,
                    "bytes": len(content),
                    "options": asdict(options),
                    "result": {
                        "new": report.new,
                        "updated": report.updated,
                        "unchanged": report.unchanged,
                        "duplicates_in_file": report.duplicates_in_file,
                        "issue_counts": report.issue_counts,
                        "ignored_columns": [c.name for c in report.ignored_columns],
                    },
                },
            )
    progress("done")
    return report


def _execute(
    session_factory: sessionmaker[Session],
    adapter: FileAdapter,
    content: bytes,
    options: ImportOptions,
    run_id: int | None,
    report: ImportReport,
    clock: Clock,
    progress: Progress,
) -> None:
    parsed: ParsedFile = adapter.parse(content, options.encoding)
    columns: ColumnMap = build_column_map(parsed.header)
    report.ignored_columns = [IgnoredColumn(name=n, reason=r) for n, r in columns.ignored]
    missing = [c for c in REQUIRED if not columns.has(c)]
    if missing:
        raise FileRejected(
            "missing_columns",
            f"Required column(s) not found: {', '.join(missing)}. "
            "Expected e.g. 'rating' and 'review_date' (see docs/IMPORT_FORMAT.md).",
        )
    if not columns.has("property") and options.property is None:
        raise FileRejected(
            "missing_property",
            "The file has no property column; choose the property with --property <slug>.",
        )
    malformed_lines = {i.line for i in parsed.row_issues}
    report.rows_total = len(parsed.rows) + len(malformed_lines)
    if report.rows_total == 0:
        raise FileRejected("empty", "The file has a header but no data rows.")
    progress("parsed")

    with session_factory() as s:
        if run_id is not None and not heartbeat(s, run_id, now=clock(), commit=False):
            raise RunLost(f"run {run_id} was marked abandoned; refusing to write")
        assert_can_load(s, adapter.source)
        properties = _load_properties(s)
        default_property = None
        if options.property is not None:
            default_property = properties.get(options.property.lower())
            if default_property is None:
                raise FileRejected("unknown_property", f"Unknown property {options.property!r}.")
        now = clock()
        ctx = NormaliseContext(
            properties, default_property, options.date_format, options.id_namespace, now
        )

        issues: list[RowIssue] = list(parsed.row_issues)
        valid: list[ReviewIn] = []
        seen: set[tuple[int, str]] = set()
        for raw in parsed.rows:
            review, row_issues = normalise_row(raw, columns, ctx)
            issues.extend(row_issues)
            if review is None:
                continue
            ident = (review.property.id, review.dedupe_key)
            if ident in seen:
                report.duplicates_in_file += 1
                issues.append(
                    issue(
                        raw.line,
                        "DQ017",
                        f"repeated review id {review.external_id!r}; first occurrence kept",
                        columns.mapping.get("external_id"),
                        review.property.slug,
                    )
                )
                continue
            seen.add(ident)
            valid.append(review)

        rejected_lines = {i.line for i in issues if i.rejects_row}
        report.rows_valid = len(valid)
        report.rows_rejected = len(rejected_lines)

        plan = _make_plan(valid, _load_existing(s, valid))
        progress("planned")
        per_property: dict[str, Counter[str]] = defaultdict(Counter)
        for r in valid:
            per_property[r.property.slug]["received"] += 1
        for i in issues:
            if i.rejects_row and i.property_slug:
                per_property[i.property_slug]["rejected"] += 1

        slug_of = {p.id: p.slug for p in properties.values()}
        if options.dry_run:
            report.new, report.updated, report.unchanged = (
                len(plan.new),
                len(plan.updates),
                len(plan.unchanged),
            )
            for r in plan.new:
                per_property[r.property.slug]["new"] += 1
            for _, incoming, _ in plan.updates:
                per_property[incoming.property.slug]["updated"] += 1
            for existing_review in plan.unchanged:
                per_property[slug_of[existing_review.property_id]]["unchanged"] += 1
            _finalise(report, issues, per_property, s)
            return

        inserted, raced = _insert_new(s, plan, adapter.source, run_id, now)
        _apply_updates(s, plan, run_id, now)
        report.new, report.updated, report.unchanged = (
            len(inserted),
            len(plan.updates),
            len(plan.unchanged),
        )
        report.raced = raced
        for r in inserted:
            per_property[r.property.slug]["new"] += 1
        for _, incoming, _ in plan.updates:
            per_property[incoming.property.slug]["updated"] += 1
        for existing_review in plan.unchanged:
            per_property[slug_of[existing_review.property_id]]["unchanged"] += 1

        issues.extend(_flag_duplicate_content(s, inserted, properties))
        _store_issues(
            s, issues, file_sha=report.file_sha256, run_id=run_id, now=now, properties=properties
        )
        if run_id is not None:
            ids = {p.slug: p.id for p in properties.values()}
            for slug, counts in per_property.items():
                s.add(
                    RunProperty(
                        run_id=run_id,
                        property_id=ids[slug],
                        status="partial" if counts["rejected"] else "success",
                        pages=1,
                        received=counts["received"] + counts["rejected"],
                        new=counts["new"],
                        updated=counts["updated"],
                        unchanged=counts["unchanged"],
                        rejected=counts["rejected"],
                        started_at=now,
                        finished_at=clock(),
                    )
                )
        s.flush()
        progress("applied")  # still inside the transaction: nothing is committed yet
        s.commit()
        _finalise(report, issues, per_property, s)


def _finalise(
    report: ImportReport,
    issues: list[RowIssue],
    per_property: dict[str, Counter[str]],
    s: Session,
) -> None:
    report.issue_counts = dict(sorted(Counter(i.code for i in issues).items()))
    rank = {"error": 0, "warning": 1, "info": 2}
    shown = sorted(
        (i for i in issues if i.severity != "info"),
        key=lambda i: (rank[i.severity], i.line, i.code),
    )
    report.issues_truncated = len(shown) > MAX_ISSUES_IN_REPORT
    report.issues = [
        IssueOut(
            line=i.line,
            code=i.code,
            severity=i.severity,
            message=i.message,
            column=i.column,
            property=i.property_slug,
        )
        for i in shown[:MAX_ISSUES_IN_REPORT]
    ]
    report.per_property = {
        k: {n: v for n, v in c.items() if v} for k, c in sorted(per_property.items())
    }
    report.status = (
        "failed" if report.rows_valid == 0 else ("partial" if report.rows_rejected else "success")
    )
    report.dataset = dataset_provenance(s)
