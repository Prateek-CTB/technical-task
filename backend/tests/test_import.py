"""P2 acceptance tests: the import pipeline on SQLite and PostgreSQL."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from reviewhub.ingestion import service
from reviewhub.ingestion.contracts import ForbiddenSource, ParsedFile
from reviewhub.ingestion.locality import find_local_date_mismatches, recompute_local_dates
from reviewhub.persistence.models import (
    DataQualityIssue,
    Property,
    Review,
    ReviewRevision,
    Run,
    RunProperty,
)
from reviewhub.persistence.runs import RunAlreadyRunning, start_run
from reviewhub.persistence.scopes import (
    DatasetIsolationError,
    assert_can_load,
    dataset_provenance,
    reportable,
)
from tests import factories as f
from tests.ingest_helpers import FIXED_NOW, PROPERTY, count, make_csv, row, run_import


def reviews(engine: Engine) -> list[Review]:
    with Session(engine) as s:
        return list(s.scalars(select(Review).order_by(Review.id)))


# =============================================================== basics
def test_first_import_loads_reviews_and_records_the_run(engine: Engine) -> None:
    report = run_import(
        engine, make_csv([row(1), row(2, rating=3, disliked="Cold shower"), row(3)])
    )
    assert (report.status, report.new, report.updated, report.unchanged) == ("success", 3, 0, 0)
    assert (report.rows_total, report.rows_valid, report.rows_rejected) == (3, 3, 0)
    assert report.source == "csv_import" and report.per_property["chateau-de-venus"]["new"] == 3

    rows = reviews(engine)
    assert [r.source for r in rows] == ["csv_import"] * 3
    assert [r.dedupe_key for r in rows] == [
        "ext:booking:r0001",
        "ext:booking:r0002",
        "ext:booking:r0003",
    ]
    first = rows[0]
    assert first.published_at == datetime(2026, 11, 19, 22, 15, tzinfo=UTC)
    assert (first.published_date_local, first.published_week_local) == (
        date(2026, 11, 20),
        date(2026, 11, 16),
    )
    assert first.language is None and first.language_source == "en"  # never assumed
    assert first.source_payload and first.source_payload["line"] == 2

    with Session(engine) as s:
        run = s.scalars(select(Run)).one()
        assert (run.kind, run.status, run.items_total, run.items_ok, run.items_failed) == (
            "import",
            "success",
            3,
            3,
            0,
        )
        assert run.scope_key == f"import:{report.file_sha256}" and run.finished_at is not None
        rp = s.scalars(select(RunProperty)).one()
        assert (rp.status, rp.received, rp.new) == ("success", 3, 3)


def test_importing_the_same_file_twice_changes_nothing(engine: Engine) -> None:
    content = make_csv([row(n) for n in range(1, 21)])
    run_import(engine, content)
    before = [(r.id, r.updated_at, r.content_hash) for r in reviews(engine)]
    second = run_import(engine, content)
    assert (second.status, second.new, second.updated, second.unchanged) == ("success", 0, 0, 20)
    assert [(r.id, r.updated_at, r.content_hash) for r in reviews(engine)] == before
    assert count(engine, ReviewRevision) == 0 and count(engine, Review) == 20
    assert count(engine, Run) == 2


def test_an_edited_row_is_one_update_and_one_revision(engine: Engine) -> None:
    run_import(engine, make_csv([row(1), row(2), row(3)]))
    edited = make_csv([row(1), row(2, rating=3, disliked="Aircon too loud"), row(3)])
    report = run_import(engine, edited, clock=lambda: FIXED_NOW + timedelta(hours=1))
    assert (report.new, report.updated, report.unchanged) == (0, 1, 2)
    with Session(engine) as s:
        rev = s.scalars(select(ReviewRevision)).one()
        assert sorted(rev.changed_fields) == ["negative_text", "rating"]
        assert rev.previous_hash != rev.new_hash
        changed = s.scalars(select(Review).where(Review.dedupe_key == "ext:booking:r0002")).one()
        assert (changed.rating, changed.negative_text) == (3.0, "Aircon too loud")
        assert changed.updated_at == FIXED_NOW + timedelta(hours=1)
    again = run_import(engine, edited, clock=lambda: FIXED_NOW + timedelta(hours=2))
    assert (again.updated, again.unchanged) == (0, 3) and count(engine, ReviewRevision) == 1


def test_a_management_reply_added_later_is_detected(engine: Engine) -> None:
    run_import(engine, make_csv([row(1)]))
    report = run_import(engine, make_csv([row(1, management_reply="Thank you for the feedback.")]))
    assert report.updated == 1
    with Session(engine) as s:
        assert s.scalars(select(ReviewRevision)).one().changed_fields == ["management_reply"]


def test_a_corrected_publication_date_updates_local_dates(engine: Engine) -> None:
    run_import(engine, make_csv([row(1, review_date="2026-11-22T13:30:00Z")]))
    assert reviews(engine)[0].published_date_local == date(2026, 11, 23)  # Monday in Sydney
    run_import(engine, make_csv([row(1, review_date="2026-11-22T12:30:00Z")]))
    r = reviews(engine)[0]
    assert (r.published_date_local, r.published_week_local) == (
        date(2026, 11, 22),
        date(2026, 11, 16),
    )


# =============================================================== bad rows vs bad files
def test_bad_rows_are_reported_precisely_while_valid_rows_commit(engine: Engine) -> None:
    rows = [
        row(1),
        row(2, rating="abc"),
        row(3),
        row(4, review_date="2027-06-01"),
        row(5, property="Nowhere Inn"),
        row(6, review_date="31/12/2026"),
        row(7),
    ]
    content = (
        make_csv(rows) + b"r0008,chateau-de-venus,8,2026-11-20,a,b,c,en,AU,x,y,d,e,2,r,EXTRA\n"
    )
    report = run_import(engine, content)
    assert report.status == "partial" and (
        report.rows_total,
        report.rows_valid,
        report.rows_rejected,
    ) == (8, 3, 5)
    got = sorted((i.line, i.code, i.column) for i in report.issues if i.severity == "error")
    assert got == [
        (3, "DQ002", "rating"),
        (5, "DQ003", "review_date"),
        (6, "DQ012", "property"),
        (7, "DQ007", "review_date"),
        (9, "DQ007", None),
    ]
    assert count(engine, Review) == 3
    with Session(engine) as s:
        stored = s.scalars(
            select(DataQualityIssue).where(DataQualityIssue.severity == "error")
        ).all()
        assert len(stored) == 5 and {i.details["line"] for i in stored if i.details} == {
            3,
            5,
            6,
            7,
            9,
        }
        assert s.scalars(select(Run)).one().status == "partial"
    # re-importing the same bad file does not multiply stored issues
    run_import(engine, content)
    assert count(engine, DataQualityIssue) == 5 and count(engine, Review) == 3


@pytest.mark.parametrize(
    ("content", "options", "code"),
    [
        ("café;1\n".encode("cp1252"), {}, "bad_encoding"),
        (b"title,liked\nx,y\n", {}, "missing_columns"),
        (b"", {}, "empty"),
        (b"rating,review_date\n", {"property": PROPERTY}, "empty"),
        (b"rating,review_date\n8,2026-11-20\n", {}, "missing_property"),
        (b"rating,review_date\n8,2026-11-20\n", {"property": "nope"}, "unknown_property"),
        (b'[{"rating": 8', {"format": "json"}, "bad_json"),
        (make_csv([row(1)]), {"id_namespace": "BAD NAMESPACE"}, "bad_namespace"),
    ],
)
def test_whole_file_errors_load_nothing(
    engine: Engine, content: bytes, options: dict[str, str], code: str
) -> None:
    report = run_import(engine, content, **options)
    assert (
        report.status == "failed"
        and report.file_error is not None
        and report.file_error.code == code
    )
    assert (
        count(engine, Review) == 0
        and count(engine, DataQualityIssue) == 0
        and count(engine, RunProperty) == 0
    )
    with Session(engine) as s:  # one failed run row is kept for audit; it holds no review data
        run = s.scalars(select(Run)).one()
        assert run.status == "failed" and run.error and code in run.error


def test_oversized_file_is_rejected_before_parsing(engine: Engine) -> None:
    report = service.import_file(
        __import__("reviewhub.persistence.session", fromlist=["x"]).create_session_factory(engine),
        content=make_csv([row(n) for n in range(50)]),
        filename="big.csv",
        max_bytes=500,
        clock=lambda: FIXED_NOW,
    )
    assert (
        report.file_error is not None
        and report.file_error.code == "too_large"
        and count(engine, Review) == 0
    )


def test_all_rows_invalid_is_a_failed_import_with_no_reviews(engine: Engine) -> None:
    report = run_import(engine, make_csv([row(1, rating="x"), row(2, rating="y")]))
    assert report.status == "failed" and report.rows_valid == 0 and report.rows_rejected == 2
    assert count(engine, Review) == 0


def test_dry_run_reports_exactly_what_a_real_run_would_do_and_writes_nothing(
    engine: Engine,
) -> None:
    run_import(engine, make_csv([row(1), row(2)]))
    content = make_csv([row(1), row(2, rating=2), row(3), row(4, rating="bad")])
    snapshot = (
        count(engine, Review),
        count(engine, Run),
        count(engine, DataQualityIssue),
        count(engine, ReviewRevision),
    )
    dry = run_import(engine, content, dry_run=True)
    assert (
        count(engine, Review),
        count(engine, Run),
        count(engine, DataQualityIssue),
        count(engine, ReviewRevision),
    ) == snapshot
    assert dry.dry_run and dry.run_id is None
    real = run_import(engine, content)
    for field in (
        "status",
        "rows_total",
        "rows_valid",
        "rows_rejected",
        "new",
        "updated",
        "unchanged",
        "issue_counts",
        "per_property",
    ):
        assert getattr(dry, field) == getattr(real, field), field


# =============================================================== duplicates and identity
def test_repeated_id_in_one_file_keeps_the_first(engine: Engine) -> None:
    report = run_import(engine, make_csv([row(1), row(1, rating=2), row(2)]))
    assert (report.new, report.duplicates_in_file) == (2, 1) and report.rows_total == 3
    assert next(i for i in report.issues if i.code == "DQ017").line == 3
    assert reviews(engine)[0].rating == 8.0  # first occurrence won


def test_same_text_under_different_ids_is_flagged_not_merged(engine: Engine) -> None:
    same = {
        "liked": "Identical praise that is long enough to count.",
        "title": "Same title",
        "rating": 9,
    }
    report = run_import(engine, make_csv([row(1, **same), row(2, **same), row(3)]))
    assert report.new == 3 and report.issue_counts.get("DQ006") == 1
    with Session(engine) as s:
        dq = s.scalars(select(DataQualityIssue).where(DataQualityIssue.code == "DQ006")).one()
        assert dq.severity == "warning"
    run_import(engine, make_csv([row(1, **same), row(2, **same), row(3)]))
    assert count(engine, DataQualityIssue) == 1


def test_short_texts_are_not_called_duplicates(engine: Engine) -> None:
    report = run_import(
        engine, make_csv([row(1, liked="Good", title=""), row(2, liked="Good", title="")])
    )
    assert "DQ006" not in report.issue_counts


def test_rows_without_ids_use_a_stable_fingerprint(engine: Engine) -> None:
    content = make_csv([row(n, review_id="") for n in range(1, 4)])
    first = run_import(engine, content)
    assert (
        first.new == 3 and first.issue_counts["DQ001"] == 3 and count(engine, DataQualityIssue) == 0
    )  # info is only counted
    second = run_import(engine, content)
    assert (second.new, second.unchanged) == (0, 3)
    assert all(r.dedupe_key.startswith("fp:") for r in reviews(engine))


def test_rating_precision_is_stable_across_reimport_on_both_engines(engine: Engine) -> None:
    content = make_csv([row(1, rating="7,26"), row(2, rating="7.5")])
    run_import(engine, content)
    again = run_import(engine, content)
    assert (again.updated, again.unchanged) == (
        0,
        2,
    )  # NUMERIC(3,1) rounding must not cause endless updates
    assert [r.rating for r in reviews(engine)] == [7.3, 7.5]


def test_over_long_and_unusual_values_never_abort_the_import(engine: Engine) -> None:
    rows = [
        row(1, review_id="x" * 90, traveller_type="T" * 300, room_type="R" * 500),
        row(2, review_id="y" * 150),
        row(3, liked="Nul\x00byte and 日本語 text — café \U0001f600"),
    ]
    report = run_import(engine, make_csv(rows))
    assert report.status == "success" and report.new == 3
    got = {r.external_id: r for r in reviews(engine)}
    assert len(got["x" * 90].dedupe_key) <= 80 and len(got["x" * 90].traveller_type or "") == 64
    assert got["y" * 100].dedupe_key.startswith("ext:booking:h:")  # id truncated to 100, key hashed
    assert report.issue_counts.get("DQ019") == 1
    assert "\x00" not in (got["r0003"].positive_text or "") and "日本語" in (
        got["r0003"].positive_text or ""
    )


# =============================================================== formats
def test_json_import_in_three_shapes(engine: Engine) -> None:
    recs = [{k: v for k, v in row(n).items() if v != ""} for n in (1, 2)]
    a = run_import(engine, json.dumps(recs).encode(), "a.json")
    assert (a.source, a.file_format, a.new) == ("json_import", "json", 2)
    b = run_import(
        engine,
        json.dumps({"reviews": [*recs, {k: v for k, v in row(3).items() if v != ""}]}).encode(),
        "b.json",
    )
    assert (b.new, b.unchanged) == (1, 2)
    lines = (
        "\n".join(json.dumps(r) for r in [{k: v for k, v in row(4).items() if v != ""}])
        + "\n{broken\n"
    )
    c = run_import(engine, lines.encode(), "c.jsonl")
    assert (c.status, c.new, c.rows_rejected) == ("partial", 1, 1)


def test_semicolon_csv_with_bom_and_european_dates(engine: Engine) -> None:
    body = make_csv(
        [row(1, review_date="20/11/2026", checkin_date="17/11/2026", checkout_date="19/11/2026")],
        delimiter=";",
    )
    report = run_import(engine, b"\xef\xbb\xbf" + body, date_format="%d/%m/%Y")
    assert report.status == "success" and report.issue_counts.get("DQ016") == 1
    assert reviews(engine)[0].published_date_local == date(2026, 11, 20)
    # Without --date-format a dd/mm/yyyy date is never guessed: the row is rejected (DQ007).
    guessed = run_import(engine, body, dry_run=True)
    assert guessed.status == "failed" and guessed.rows_valid == 0
    assert [(i.code, i.column) for i in guessed.issues] == [("DQ007", "review_date")]


def test_default_property_option_for_files_without_a_property_column(engine: Engine) -> None:
    header = [h for h in ("review_id", "rating", "review_date", "liked")]
    report = run_import(engine, make_csv([row(1), row(2)], header=header), property="Potts Point")
    assert report.status == "success" and set(report.per_property) == {"venus-potts-point-sydney"}


def test_multiple_properties_in_one_file(engine: Engine) -> None:
    content = make_csv(
        [row(1), row(2, property="olympic-paddington"), row(3, property="Central Sydney")]
    )
    report = run_import(engine, content)
    assert report.per_property == {
        "chateau-de-venus": {"received": 1, "new": 1},
        "olympic-paddington": {"received": 1, "new": 1},
        "venus-surry-hills": {"received": 1, "new": 1},
    }
    assert count(engine, RunProperty) == 3


def test_a_larger_file_is_loaded_in_chunks_and_is_idempotent(engine: Engine) -> None:
    content = make_csv([row(n, rating=(n % 10) + 1) for n in range(1, 1201)])
    first = run_import(engine, content)
    assert (first.status, first.new) == ("success", 1200) and count(engine, Review) == 1200
    second = run_import(engine, content)
    assert (second.new, second.updated, second.unchanged) == (0, 0, 1200)


# =============================================================== concurrency (ADR 0012)
def test_the_same_file_cannot_be_imported_twice_at_once(engine: Engine, db_url: str) -> None:
    content = make_csv([row(1)])
    sha = run_import(engine, content, dry_run=True).file_sha256
    with Session(engine) as s:
        start_run(
            s,
            kind="import",
            source="csv_import",
            scope_key=f"import:{sha}",
            mode="file",
            now=FIXED_NOW,
        )
    with pytest.raises(RunAlreadyRunning):
        run_import(engine, content)
    assert count(engine, Review) == 0
    assert (
        run_import(engine, content, dry_run=True).status == "success"
    )  # dry runs are never blocked


def test_a_different_file_is_not_blocked_by_a_running_import(engine: Engine) -> None:
    sha = run_import(engine, make_csv([row(1)]), dry_run=True).file_sha256
    with Session(engine) as s:
        start_run(
            s,
            kind="import",
            source="csv_import",
            scope_key=f"import:{sha}",
            mode="file",
            now=FIXED_NOW,
        )
    other = run_import(engine, make_csv([row(2)]))
    assert other.status == "success" and other.new == 1


def test_parallel_imports_of_overlapping_files_never_duplicate_or_fail(engine: Engine) -> None:
    a = make_csv([row(n) for n in range(1, 61)])
    b = make_csv([row(n) for n in range(31, 91)])  # overlaps 31..60
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def worker(name: str, content: bytes) -> None:
        try:
            barrier.wait(timeout=30)
            results[name] = run_import(engine, content, f"{name}.csv")
        except BaseException as exc:
            results[name] = exc

    threads = [threading.Thread(target=worker, args=(n, c)) for n, c in (("a", a), ("b", b))]
    [t.start() for t in threads]
    [t.join(timeout=120) for t in threads]
    ra, rb = results["a"], results["b"]
    assert not isinstance(ra, BaseException) and not isinstance(rb, BaseException), results
    assert count(engine, Review) == 90
    assert ra.new + rb.new == 90  # type: ignore[union-attr]  # each review is "new" in exactly one import
    assert ra.new + ra.unchanged + ra.updated == 60 and rb.new + rb.unchanged + rb.updated == 60  # type: ignore[union-attr]
    with Session(engine) as s:
        assert {r.status for r in s.scalars(select(Run))} == {"success"}


def test_a_lost_insert_race_is_reconciled_not_raised(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deterministic version of the race: this import's lookup is stale, so every row conflicts."""
    content = make_csv([row(1), row(2), row(3, rating=4)])
    run_import(engine, make_csv([row(1), row(2), row(3)]))  # someone else got there first
    real, calls = service._load_existing, {"n": 0}

    def stale_first(s: Session, rows):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return {} if calls["n"] == 1 else real(s, rows)

    monkeypatch.setattr(service, "_load_existing", stale_first)
    report = run_import(engine, content, "raced.csv")
    assert report.status == "success" and report.raced == 3
    assert (report.new, report.updated, report.unchanged) == (
        0,
        1,
        2,
    )  # row 3 differs -> an update, not a crash
    assert count(engine, Review) == 3 and count(engine, ReviewRevision) == 1


# =============================================================== crash recovery (F2)
def test_a_process_killed_mid_import_loses_no_data_and_the_scope_recovers(
    engine: Engine, db_url: str, tmp_path: Path
) -> None:
    path = tmp_path / "crash.csv"
    content = make_csv([row(n) for n in range(1, 31)])
    path.write_bytes(content)
    script = textwrap.dedent(
        """
        import os, sys
        from pathlib import Path
        from reviewhub.ingestion.service import ImportOptions, import_file
        from reviewhub.persistence.session import create_db_engine, create_session_factory
        factory = create_session_factory(create_db_engine(sys.argv[1]))
        p = Path(sys.argv[2])
        def die_before_commit(stage):
            if stage == "applied":      # rows are written but NOT committed
                os._exit(9)
        import_file(factory, content=p.read_bytes(), filename=p.name, progress=die_before_commit)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, db_url, str(path)], capture_output=True, text=True
    )
    assert proc.returncode == 9, proc.stderr

    assert (
        count(engine, Review) == 0 and count(engine, RunProperty) == 0
    )  # the transaction died with the process
    with Session(engine) as s:
        dead = s.scalars(select(Run)).one()
        assert dead.status == "running"
    with pytest.raises(RunAlreadyRunning):  # still within the stale window
        run_import(engine, content, clock=lambda: datetime.now(UTC) + timedelta(minutes=5))

    later = lambda: datetime.now(UTC) + timedelta(minutes=16)  # noqa: E731
    report = run_import(engine, content, clock=later)
    assert (report.status, report.new) == ("success", 30) and count(engine, Review) == 30
    with Session(engine) as s:
        statuses = sorted(r.status for r in s.scalars(select(Run)))
        assert statuses == ["abandoned", "success"]


# =============================================================== privacy (ADR 0009)
def test_personal_data_columns_are_never_read_or_stored(engine: Engine) -> None:
    secrets = {
        "reviewer_name": "Zebediah Quillfeather",
        "email": "zeb.quillfeather@example.invalid",
        "avatar_url": "https://img.invalid/zq.png",
    }
    header = [*("review_id", "property", "rating", "review_date", "liked"), *secrets]
    content = make_csv([{**row(1), **secrets}], header=header)
    report = run_import(engine, content)
    assert {c.name: c.reason for c in report.ignored_columns} == {
        k: "personal data" for k in secrets
    }
    with engine.connect() as conn:
        tables = [
            t
            for (t,) in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                    if engine.dialect.name == "postgresql"
                    else "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            )
        ]
        dump = " ".join(
            str(v) for t in tables for r in conn.execute(text(f'SELECT * FROM "{t}"')) for v in r
        )
    for value in secrets.values():
        assert value not in dump
    assert "Zebediah" not in dump


# =============================================================== isolation (F5)
def test_a_file_cannot_claim_to_be_demo_or_experimental_data(engine: Engine) -> None:
    header = [*("review_id", "property", "rating", "review_date", "liked"), "source"]
    report = run_import(engine, make_csv([{**row(1), "source": "demo_synthetic"}], header=header))
    assert {c.name: c.reason for c in report.ignored_columns} == {"source": "unrecognised"}
    assert [r.source for r in reviews(engine)] == ["csv_import"]


class _Forged:
    name, source = "forged", "demo_synthetic"

    def parse(self, content: bytes, encoding: str | None) -> ParsedFile:  # pragma: no cover
        raise AssertionError("must be refused before parsing")


@pytest.mark.parametrize(
    "source", ["demo_synthetic", "booking_experimental", "booking_public", "anything"]
)
def test_the_pipeline_refuses_adapters_that_record_forbidden_sources(
    engine: Engine, source: str
) -> None:
    forged = _Forged()
    forged.source = source
    with pytest.raises(ForbiddenSource):
        run_import(engine, b"x", adapter=forged)
    assert count(engine, Run) == 0 and count(engine, Review) == 0


@pytest.mark.parametrize("existing", ["demo_synthetic", "booking_experimental"])
def test_real_data_is_refused_into_a_database_holding_demo_or_experimental_rows(
    engine: Engine, existing: str
) -> None:
    with Session(engine) as s:
        f.review(s, source=existing)
        s.commit()
    with pytest.raises(DatasetIsolationError, match="refusing to load"):
        run_import(engine, make_csv([row(1)]))
    assert count(engine, Review) == 1 and count(engine, DataQualityIssue) == 0
    with Session(engine) as s:
        run = s.scalars(select(Run)).one()
        assert run.status == "failed" and "DatasetIsolationError" in (run.error or "")


def test_isolation_matrix(engine: Engine) -> None:
    def load_into(existing: list[str], incoming: str) -> bool:
        with Session(engine) as s:
            s.execute(text("DELETE FROM review"))
            for src in existing:
                f.review(s, source=src)
            s.commit()
            try:
                assert_can_load(s, incoming)
                return True
            except DatasetIsolationError:
                return False

    assert (
        load_into([], "csv_import")
        and load_into([], "demo_synthetic")
        and load_into([], "booking_experimental")
    )
    assert load_into(["csv_import"], "json_import") and load_into(["booking_public"], "csv_import")
    assert load_into(["demo_synthetic"], "demo_synthetic")
    assert not load_into(["csv_import"], "demo_synthetic")
    assert not load_into(["demo_synthetic"], "csv_import")
    assert not load_into(["booking_experimental"], "csv_import")
    assert not load_into(["csv_import"], "booking_experimental")
    assert not load_into(["demo_synthetic"], "booking_experimental")


def test_reporting_scope_excludes_demo_and_experimental_by_default(engine: Engine) -> None:
    with Session(engine) as s:
        for src in (
            "csv_import",
            "json_import",
            "booking_public",
            "demo_synthetic",
            "booking_experimental",
        ):
            f.review(s, source=src)
        s.commit()

        def n(**kw: bool) -> int:
            return len(s.scalars(select(Review.id).where(reportable(**kw))).all())

        assert n() == 3
        assert n(include_demo=True) == 4 and n(include_experimental=True) == 4
        assert n(include_demo=True, include_experimental=True) == 5
        prov = dataset_provenance(s)
        assert (
            prov["contains_non_production_data"]
            and prov["contains_demo"]
            and prov["contains_experimental"]
        )
        assert prov["sources"]["csv_import"] == 1


def test_source_value_sets_are_a_partition() -> None:
    from reviewhub.persistence.base import SOURCES
    from reviewhub.persistence.scopes import (
        DEMO_SOURCES,
        EXPERIMENTAL_SOURCES,
        IMPORT_SOURCES,
        PRODUCTION_SOURCES,
    )

    groups = [set(PRODUCTION_SOURCES), set(DEMO_SOURCES), set(EXPERIMENTAL_SOURCES)]
    assert set().union(*groups) == set(SOURCES) and sum(len(g) for g in groups) == len(SOURCES)
    assert set(IMPORT_SOURCES) <= set(PRODUCTION_SOURCES)


def test_a_clean_import_reports_production_provenance(engine: Engine) -> None:
    report = run_import(engine, make_csv([row(1), row(2)]))
    assert report.dataset == {
        "sources": {"csv_import": 2},
        "contains_demo": False,
        "contains_experimental": False,
        "contains_non_production_data": False,
    }


# =============================================================== local dates per property timezone (F3)
def test_each_review_uses_its_own_propertys_timezone(engine: Engine) -> None:
    with Session(engine) as s:
        s.execute(
            text(
                "UPDATE property SET timezone = 'Australia/Perth' WHERE slug = 'olympic-paddington'"
            )
        )
        s.commit()
    instant = "2026-11-22T13:30:00Z"  # Mon 00:30 in Sydney, Sun 21:30 in Perth
    run_import(
        engine,
        make_csv(
            [
                row(1, review_date=instant),
                row(2, review_date=instant, property="olympic-paddington"),
            ]
        ),
    )
    with Session(engine) as s:
        got = {
            p.slug: (r.published_date_local, r.published_week_local)
            for r, p in s.execute(select(Review, Property).join(Property))
        }
    assert got == {
        "chateau-de-venus": (date(2026, 11, 23), date(2026, 11, 23)),
        "olympic-paddington": (date(2026, 11, 22), date(2026, 11, 16)),
    }
    with Session(engine) as s:
        assert find_local_date_mismatches(s) == []


def test_changing_a_propertys_timezone_is_caught_and_repaired(engine: Engine) -> None:
    run_import(engine, make_csv([row(n, review_date="2026-11-22T13:30:00Z") for n in (1, 2, 3)]))
    with Session(engine) as s:
        assert find_local_date_mismatches(s) == []
        s.execute(
            text("UPDATE property SET timezone = 'Australia/Perth' WHERE slug = 'chateau-de-venus'")
        )
        s.commit()
        bad = find_local_date_mismatches(s)
        assert (
            len(bad) == 3
            and bad[0]["stored"] == (date(2026, 11, 23), date(2026, 11, 23))
            and bad[0]["expected"] == (date(2026, 11, 22), date(2026, 11, 16))
        )
        assert recompute_local_dates(s) == 3
        s.commit()
        assert find_local_date_mismatches(s) == []
        assert {r.published_week_local for r in s.scalars(select(Review))} == {date(2026, 11, 16)}


def test_an_invalid_property_timezone_blocks_the_import(engine: Engine) -> None:
    with Session(engine) as s:
        s.execute(
            text("UPDATE property SET timezone = 'Mars/Olympus' WHERE slug = 'chateau-de-venus'")
        )
        s.commit()
    report = run_import(engine, make_csv([row(1)]))
    assert (
        report.status == "failed"
        and report.file_error
        and report.file_error.code == "invalid_property_timezone"
    )
    assert count(engine, Review) == 0


def test_every_imported_row_satisfies_the_local_date_invariant(engine: Engine) -> None:
    stamps = [
        "2026-10-03T14:30:00Z",
        "2026-10-04T13:30:00Z",
        "2026-04-04T13:30:00Z",
        "2026-11-22T12:59:00Z",
        "2026-11-22T13:00:00Z",
        "2026-03-01",
    ]
    run_import(engine, make_csv([row(i, review_date=s) for i, s in enumerate(stamps, 1)]))
    with Session(engine) as s:
        assert find_local_date_mismatches(s) == []
        assert [r.published_date_local for r in s.scalars(select(Review).order_by(Review.id))] == [
            date(2026, 10, 4),
            date(2026, 10, 5),
            date(2026, 4, 5),
            date(2026, 11, 22),
            date(2026, 11, 23),
            date(2026, 3, 1),
        ]
