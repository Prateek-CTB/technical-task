"""Command-line entry point: ``reviewhub <command>``.

Commands: ``db`` (migrations), ``import`` (CSV/JSON reviews), ``collect`` (public pages,
inbox, feed), ``sample`` (load/export the committed real sample), ``ai`` (analysis),
``seed-demo`` (fictional data), ``runs`` (history, stale-run recovery), ``serve`` (API).

Exit codes for ``import``: 0 success, 1 failed, 3 partial (some rows rejected),
4 same file already importing, 5 dataset-isolation refusal.
Exit codes for ``collect``: 0 success, 1 failed, 3 partial, 4 another collection running,
6 blocked (CAPTCHA / explicit refusal / cooldown: alert a human, do not retry in a loop).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from reviewhub.collection.service import CollectionReport
from reviewhub.core.logging import configure_logging
from reviewhub.core.settings import Settings, get_settings
from reviewhub.demo.seed import seed_demo, summarise
from reviewhub.ingestion.contracts import ForbiddenSource, ImportReport
from reviewhub.ingestion.service import ImportOptions, import_file
from reviewhub.persistence import migrate
from reviewhub.persistence.runs import RunAlreadyRunning, reap_stale_runs, utc_now
from reviewhub.persistence.scopes import DatasetIsolationError
from reviewhub.persistence.session import create_db_engine, create_session_factory


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reviewhub", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    db = sub.add_parser("db", help="database migrations")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    up = db_sub.add_parser("upgrade", help="apply migrations")
    up.add_argument("revision", nargs="?", default="head")
    down = db_sub.add_parser("downgrade", help="revert migrations")
    down.add_argument("revision", nargs="?", default="base")
    db_sub.add_parser("current", help="show the current revision")

    imp = sub.add_parser("import", help="import a CSV/JSON export of reviews")
    imp.add_argument("file", type=Path)
    imp.add_argument("--property", help="property slug or name, if the file has no property column")
    imp.add_argument("--dry-run", action="store_true", help="validate and report; write nothing")
    imp.add_argument("--format", choices=["auto", "csv", "json"], default="auto")
    imp.add_argument("--encoding", help="default utf-8; e.g. cp1252 for old Excel exports")
    imp.add_argument("--date-format", help="strptime format for non-ISO dates, e.g. %%d/%%m/%%Y")
    imp.add_argument(
        "--id-namespace", default="booking", help="namespace for the file's review ids"
    )
    imp.add_argument("--json", action="store_true", help="print the full report as JSON")

    demo = sub.add_parser("seed-demo", help="load the fictional, deterministic demo dataset")
    demo.add_argument("--seed", type=int, default=20261126)
    demo.add_argument(
        "--end-date", type=date.fromisoformat, help="last day (YYYY-MM-DD, Sydney); default today"
    )
    demo.add_argument("--days", type=int, default=364)
    demo.add_argument("--reset", action="store_true", help="delete existing demo rows first")
    demo.add_argument("--json", action="store_true", help="print the summary as JSON")

    ai = sub.add_parser("ai", help="AI review analysis (OpenRouter free models or the mock)")
    ai_sub = ai.add_subparsers(dest="ai_command", required=True)
    st = ai_sub.add_parser("status", help="configuration (no secrets) and analysis coverage")
    st.add_argument(
        "--check",
        action="store_true",
        help="ask OpenRouter's model list whether the model exists and is free",
    )
    an = ai_sub.add_parser(
        "analyze", help="analyse pending reviews (resumable; cached results are skipped)"
    )
    an.add_argument(
        "--dry-run", action="store_true", help="count what would be processed; no API calls"
    )
    an.add_argument(
        "--limit", type=int, help="process at most N reviews (spread across properties)"
    )
    an.add_argument("--property", help="property slug")
    an.add_argument(
        "--provider",
        choices=["builtin", "openrouter", "mock"],
        help="override AI_PROVIDER for this run; builtin = offline rule-based analyzer "
        "(no key, no network, analyses every review in seconds)",
    )
    an.add_argument(
        "--resume",
        action="store_true",
        help="accepted for clarity: analyze always continues with pending reviews",
    )
    an.add_argument(
        "--retry-failed", action="store_true", help="retry reviews that already failed 3 times"
    )
    ai_sub.add_parser("report", help="coverage, failures, token usage and cost")
    ai_sub.add_parser(
        "agreement",
        help="how often the rule-based analyzer agrees with the LLM on reviews analysed by both "
        "(consistency between two methods, not accuracy)",
    )

    col = sub.add_parser(
        "collect",
        help="collect reviews: public pages (headless by default), an owner inbox folder, or an authorised feed",
    )
    col.add_argument(
        "--source",
        choices=["booking", "http", "inbox", "feed"],
        default="booking",
        help="booking = public property pages in a browser (reviews); http = one plain HTTPS "
        "request per page for structured data (published score; detects WAF challenges); "
        "inbox = import files dropped in the inbox folder; "
        "feed = read the authorised feed URL (REVIEWHUB_COLLECT_FEED_URL)",
    )
    mode = col.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="browser without a window (default)")
    mode.add_argument(
        "--headed",
        action="store_true",
        help="visible browser window (needs a display). Booking.com currently serves reviews to "
        "this mode only; it is never switched to automatically after a refusal",
    )
    col.add_argument("--concurrency", type=int, help="parallel browsers (1-3); pacing stays global")
    col.add_argument(
        "--no-resume",
        action="store_true",
        help="do not continue an interrupted run's page position",
    )
    col.add_argument(
        "--property", action="append", help="property slug (repeatable); default all four"
    )
    col.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="pages of 10 reviews per property (default 3 = newest 30)",
    )
    col.add_argument(
        "--full", action="store_true", help="ignore stored reviews; read max-pages from the newest"
    )

    sample = sub.add_parser(
        "sample", help="the committed sample of real reviews collected from the public pages"
    )
    sample_sub = sample.add_subparsers(dest="sample_command", required=True)
    sl = sample_sub.add_parser(
        "load", help="load data/seed/sample_reviews.json (recorded as booking_public)"
    )
    sl.add_argument("--file", type=Path, default=None)
    se = sample_sub.add_parser(
        "export", help="write every collected booking_public review to the sample file"
    )
    se.add_argument("--file", type=Path, default=None)

    runs = sub.add_parser("runs", help="run bookkeeping")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    runs_sub.add_parser("reap", help="mark runs with no heartbeat as abandoned")
    rl = runs_sub.add_parser(
        "list", help="collection history: recent runs and per-property results"
    )
    rl.add_argument("--kind", default="collect", choices=["collect", "import", "analyse"])
    rl.add_argument("--limit", type=int, default=10)

    serve = sub.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    if args.command == "db":
        if args.db_command == "upgrade":
            migrate.upgrade(settings.database_url, args.revision)
        elif args.db_command == "downgrade":
            migrate.downgrade(settings.database_url, args.revision)
        else:
            engine = create_db_engine(settings.database_url)
            print(migrate.current_revision(engine) or "(no migrations applied)")
            engine.dispose()
        return 0

    if args.command == "import":
        return _import(args, settings)

    if args.command == "ai":
        return _ai(args, settings)

    if args.command == "collect":
        return _collect(args, settings)

    if args.command == "seed-demo":
        return _seed_demo(args, settings)

    if args.command == "sample":
        return _sample(args, settings)

    if args.command == "runs" and args.runs_command == "list":
        return _runs_list(args, settings)

    if args.command == "runs":
        engine = create_db_engine(settings.database_url)
        with create_session_factory(engine)() as session:
            ids = reap_stale_runs(
                session,
                now=utc_now(),
                stale_after=timedelta(minutes=settings.run_stale_after_minutes),
            )
        engine.dispose()
        print(f"marked {len(ids)} stale run(s) abandoned" + (f": {ids}" if ids else ""))
        return 0

    if args.command == "serve":
        import uvicorn

        uvicorn.run(
            "reviewhub.api.app:create_app_from_env",
            factory=True,
            host=args.host,
            port=args.port,
        )
        return 0
    return 2


def _print_report(report: ImportReport) -> None:
    mode = "DRY RUN (nothing written) " if report.dry_run else ""
    print(
        f"{mode}{report.file_name}: {report.status.upper()}  (run {report.run_id}, sha256 {report.file_sha256[:12]})"
    )
    if report.file_error:
        print(f"  file rejected [{report.file_error.code}]: {report.file_error.message}")
        return
    print(
        f"  rows: {report.rows_total} total, {report.rows_valid} valid, {report.rows_rejected} rejected, "
        f"{report.duplicates_in_file} repeated in file"
    )
    print(f"  reviews: {report.new} new, {report.updated} updated, {report.unchanged} unchanged")
    for p, counts in report.per_property.items():
        print(f"    {p}: {counts}")
    if report.ignored_columns:
        print(
            "  ignored columns: "
            + ", ".join(f"{c.name} ({c.reason})" for c in report.ignored_columns)
        )
    if report.issue_counts:
        print(f"  findings: {report.issue_counts}")
    for i in report.issues[:20]:
        where = f"line {i.line}" + (f", column {i.column}" if i.column else "")
        print(f"    [{i.severity}] {i.code} {where}: {i.message}")
    if len(report.issues) > 20 or report.issues_truncated:
        print("    ... (use --json for the full report)")


def _import(args: argparse.Namespace, settings: Settings) -> int:
    path: Path = args.file
    if not path.is_file():
        print(f"error: no such file: {path}", file=sys.stderr)
        return 1
    engine = create_db_engine(settings.database_url)
    try:
        report = import_file(
            create_session_factory(engine),
            content=path.read_bytes(),
            filename=path.name,
            options=ImportOptions(
                property=args.property,
                dry_run=args.dry_run,
                encoding=args.encoding,
                date_format=args.date_format,
                id_namespace=args.id_namespace,
                format=args.format,
            ),
            stale_after=timedelta(minutes=settings.run_stale_after_minutes),
            max_bytes=settings.import_max_bytes,
        )
    except RunAlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except DatasetIsolationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    except ForbiddenSource as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        _print_report(report)
    return {"success": 0, "partial": 3}.get(report.status, 1)


def _collect(args: argparse.Namespace, settings: Settings) -> int:
    from reviewhub.collection.service import CollectOptions, collect

    mode = (
        "headed" if args.headed else "headless" if args.headless else settings.collect_browser_mode
    )
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        if args.source == "inbox":
            return _collect_inbox(factory, settings)
        if args.source == "feed":
            return _collect_feed(factory, settings)
        if args.source == "http":
            from reviewhub.collection.http_page import collect_http

            return _print_collection(collect_http(factory, settings, args.property), "http")
        rep: CollectionReport = collect(
            factory,
            settings,
            CollectOptions(
                slugs=args.property,
                max_pages=args.max_pages,
                full=args.full,
                mode=mode,
                concurrency=args.concurrency or settings.collect_concurrency,
                resume=not args.no_resume,
            ),
        )
    except (RunAlreadyRunning, DatasetIsolationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    finally:
        engine.dispose()
    return _print_collection(rep, mode)


def _print_collection(rep: CollectionReport, mode: str) -> int:
    print(f"collection run #{rep.run_id} ({mode}): {rep.status.upper()}")
    if rep.note:
        print(f"  {rep.note}")
    for p in rep.properties:
        extra = f"  [{p['error']}]" if p["error"] else ""
        print(
            f"  {p['property']:26s} {p['status']:8s} pages={p['pages']} collected={p['collected']} "
            f"new={p['new']} updated={p['updated']} unchanged={p['unchanged']} "
            f"(Booking reports {p['headline_total']} reviews, score {p['headline_score']}){extra}"
        )
    if rep.log_path:
        print(f"  event log: {rep.log_path}")
    if rep.status == "partial":
        return 3
    return {"success": 0, "blocked": 6}.get(rep.status, 1)


SAMPLE_FILE = Path(__file__).resolve().parents[3] / "data" / "seed" / "sample_reviews.json"


def _sample(args: argparse.Namespace, settings: Settings) -> int:
    """Load or export the committed sample of real, publicly collected reviews.

    ``load`` records the rows as ``booking_public`` (their true origin: this project's collector
    on the public pages), through the normal import pipeline, so de-duplication and validation
    apply and re-loading is a no-op. ``export`` writes the collector's own fields only; reviewer
    names were never collected.
    """
    from sqlalchemy import select

    from reviewhub.collection.service import SOURCE, PublicAdapter
    from reviewhub.persistence.models import Property, Review

    path: Path = args.file or SAMPLE_FILE
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        if args.sample_command == "load":
            rep = import_file(
                factory,
                content=path.read_bytes(),
                filename=path.name,
                options=ImportOptions(format="json", id_namespace="booking", trigger="sample"),
                adapter=PublicAdapter(),
                allowed_sources=(SOURCE,),
            )
            print(
                f"sample {path.name}: {rep.status} rows={rep.rows_total} new={rep.new} "
                f"updated={rep.updated} unchanged={rep.unchanged} rejected={rep.rows_rejected}"
            )
            return 0 if rep.status == "success" else 3
        with factory() as s:
            rows = s.execute(
                select(Review, Property.slug)
                .join(Property, Property.id == Review.property_id)
                .where(Review.source == SOURCE)
                .order_by(Property.id, Review.published_date_local.desc(), Review.id)
            ).all()
            records = [
                {
                    "property": slug,
                    "rating": float(r.rating) if float(r.rating) % 1 else int(r.rating),
                    "review_date": r.published_date_local.isoformat(),
                    "title": r.title or "",
                    "liked": r.positive_text or "",
                    "disliked": r.negative_text or "",
                    "traveller_type": r.traveller_type or "",
                    "room_type": r.room_type or "",
                    "nights": r.nights if r.nights is not None else "",
                }
                for r, slug in rows
            ]
        path.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {len(records)} reviews to {path}")
        return 0
    except (RunAlreadyRunning, DatasetIsolationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    finally:
        engine.dispose()


def _collect_inbox(factory: sessionmaker[Session], settings: Settings) -> int:
    from reviewhub.collection.feeds import import_inbox

    results = import_inbox(
        factory, Path(settings.collect_inbox_dir), on_event=lambda ev, d: print(f"  {ev}: {d}")
    )
    if not results:
        print(f"inbox {settings.collect_inbox_dir}: no CSV/JSON files waiting")
        return 0
    for r in results:
        note = f"  [{r.error}]" if r.error else ""
        print(
            f"  {r.file:40s} {r.status:9s} new={r.new} updated={r.updated} "
            f"unchanged={r.unchanged}{note}"
        )
    return 0 if all(r.status == "imported" for r in results) else 3


def _collect_feed(factory: sessionmaker[Session], settings: Settings) -> int:
    from reviewhub.collection.feeds import fetch_feed, import_feed
    from reviewhub.collection.policy import RetryPolicy
    from reviewhub.collection.signals import CollectionError

    if not settings.collect_feed_url:
        print(
            "error: set REVIEWHUB_COLLECT_FEED_URL (and REVIEWHUB_COLLECT_FEED_TOKEN)",
            file=sys.stderr,
        )
        return 2
    policy = RetryPolicy(
        settings.collect_max_attempts,
        settings.collect_backoff_base_seconds,
        settings.collect_backoff_max_seconds,
    )
    token = settings.collect_feed_token
    try:
        body = fetch_feed(
            settings.collect_feed_url,
            token=token.get_secret_value() if token else None,
            policy=policy,
            timeout_s=settings.collect_nav_timeout_seconds,
            on_event=lambda ev, d: print(f"  {ev}: {d}"),
        )
    except CollectionError as exc:
        print(f"feed {exc.status.upper()}: {exc}", file=sys.stderr)
        return 6 if exc.site_decision else 1
    rep = import_feed(factory, body)
    print(
        f"feed import: {rep.status} new={rep.new} updated={rep.updated} unchanged={rep.unchanged}"
    )
    return 0 if rep.status == "success" else 3


def _runs_list(args: argparse.Namespace, settings: Settings) -> int:
    from sqlalchemy import select

    from reviewhub.persistence.models import Property, Run, RunProperty

    engine = create_db_engine(settings.database_url)
    with create_session_factory(engine)() as s:
        runs = s.scalars(
            select(Run)
            .where(Run.kind == args.kind)
            .order_by(Run.started_at.desc())
            .limit(args.limit)
        ).all()
        names = {p.id: p.slug for p in s.scalars(select(Property))}
        for r in runs:
            mode = (r.params or {}).get("browser_mode", "")
            err = f"  [{r.error}]" if r.error else ""
            print(
                f"#{r.id} {r.started_at:%Y-%m-%d %H:%M} {r.source} {r.mode} {mode} -> {r.status.upper()}{err}"
            )
            for rp in s.scalars(select(RunProperty).where(RunProperty.run_id == r.id)):
                perr = f"  [{rp.error}]" if rp.error else ""
                print(
                    f"    {names.get(rp.property_id, str(rp.property_id)):26s} {rp.status:8s} "
                    f"pages={rp.pages} received={rp.received} new={rp.new} updated={rp.updated}{perr}"
                )
    engine.dispose()
    return 0


def _seed_demo(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        report, dataset = seed_demo(
            factory, seed=args.seed, end=args.end_date, days=args.days, reset=args.reset
        )
        with factory() as session:
            summary = summarise(session, dataset)
    except DatasetIsolationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5
    except RunAlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    finally:
        engine.dispose()
    if args.json:
        payload = {"import": report.model_dump(mode="json", exclude={"issues"}), "summary": summary}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(
            f"DEMO DATA (fictional): {report.new} new, {report.updated} updated, "
            f"{report.unchanged} unchanged, {report.rows_rejected} rejected"
        )
        for key, value in summary.items():
            print(f"  {key}: {value}")
    return 0 if report.status == "success" else 1


def _ai(args: argparse.Namespace, settings: Settings) -> int:
    from sqlalchemy import func, select

    from reviewhub.intelligence.factory import build_config, build_provider
    from reviewhub.intelligence.providers import ProviderError, check_model_listed
    from reviewhub.intelligence.service import coverage_counts, run_analysis
    from reviewhub.persistence.models import ReviewAnalysis, Run

    override = getattr(args, "provider", None)
    cfg = build_config(settings, override)
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        if args.ai_command == "status":
            print(
                f"provider: {cfg.provider}   model: {cfg.model or '(none)'}   prompt {cfg.prompt_version} / taxonomy {cfg.taxonomy_version}"
            )
            if cfg.provider == "none":
                print(
                    "AI_PROVIDER is not set: LLM analysis is off. The offline rule-based analyzer "
                    "needs no configuration: reviewhub ai analyze --provider builtin"
                )
                with factory() as s:
                    print("coverage:", coverage_counts(s))
                return 0
            if cfg.provider == "openrouter":
                print(
                    f"api key configured: {'yes' if settings.openrouter_api_key else 'NO'}   (value never shown)"
                )
            try:
                build_provider(settings)
                print("configuration: valid")
                if args.check and cfg.provider == "openrouter":
                    info = check_model_listed(cfg.model, settings.openrouter_base_url)
                    print(
                        f"model check: listed, zero-priced ({info['pricing']}), context {info['context_length']}, "
                        f"json mode {info['supports_response_format']}"
                    )
            except ProviderError as exc:
                print(f"configuration problem [{exc.code}]: {exc}")
                return 1
            with factory() as s:
                print("coverage:", coverage_counts(s))
            return 0
        if args.ai_command == "agreement":
            from reviewhub.intelligence.agreement import rule_based_vs_llm

            def f(v: float | None) -> str:
                return "n/a" if v is None else f"{v:.2f}"

            with factory() as s:
                ag = rule_based_vs_llm(s)
            print(f"reviews analysed by both the LLM and the rule-based analyzer: {ag.reviews}")
            print(
                f"  topics (LLM as reference): precision {f(ag.precision)}, recall {f(ag.recall)}, "
                f"F1 {f(ag.f1)}  [both {ag.topic_tp}, rule-based only {ag.topic_fp}, LLM only {ag.topic_fn}]"
            )
            print(f"  polarity agreement on shared topics: {f(ag.polarity_rate)}")
            print(f"  overall sentiment agreement: {f(ag.overall_rate)}")
            print(
                "  note: agreement between two automated methods on a small sample; "
                "not accuracy (no human-labelled set yet)"
            )
            return 0
        if args.ai_command == "report":
            with factory() as s:
                print("coverage:", coverage_counts(s))
                rows = s.execute(
                    select(
                        ReviewAnalysis.provider,
                        ReviewAnalysis.model_id,
                        ReviewAnalysis.status,
                        func.count(),
                        func.coalesce(func.sum(ReviewAnalysis.input_tokens), 0),
                        func.coalesce(func.sum(ReviewAnalysis.output_tokens), 0),
                        func.coalesce(func.sum(ReviewAnalysis.cost_usd), 0.0),
                    ).group_by(
                        ReviewAnalysis.provider, ReviewAnalysis.model_id, ReviewAnalysis.status
                    )
                ).all()
                for prov, model, status, n, i, o, cost in rows:
                    print(
                        f"  {prov}/{model} [{status}]: {n} analyses, tokens in/out {i}/{o}, reported cost ${cost:.4f}"
                    )
                last = s.scalars(
                    select(Run).where(Run.kind == "analyse").order_by(Run.id.desc()).limit(1)
                ).first()
                if last:
                    print(
                        f"last run: #{last.id} {last.status} at {last.started_at:%Y-%m-%d %H:%M} UTC; {last.items_ok}/{last.items_total} ok, "
                        f"{last.items_failed} failed; {last.error or ''}"
                    )
            return 0
        try:
            provider = build_provider(settings, override)
        except ProviderError as exc:
            print(f"error [{exc.code}]: {exc}", file=sys.stderr)
            return 1
        local = cfg.provider == "builtin"  # no rate limit, no concurrency needed
        rep = run_analysis(
            factory,
            provider,
            cfg,
            batch_size=200 if local else settings.ai_batch_size,
            concurrency=1 if local else settings.ai_max_concurrency,
            requests_per_minute=600_000 if local else settings.ai_requests_per_minute,
            max_retries=settings.ai_max_retries,
            property_slug=args.property,
            limit=args.limit,
            dry_run=args.dry_run,
            retry_failed=args.retry_failed,
            allow_real_text=settings.ai_allow_real_guest_text,
            stale_after=timedelta(minutes=settings.run_stale_after_minutes),
        )
    except RunAlreadyRunning as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    finally:
        engine.dispose()
    label = "DRY RUN (no API calls)" if rep.dry_run else f"run #{rep.run_id} {rep.status.upper()}"
    print(f"{label}: {rep.provider}/{rep.model}")
    print(
        f"  eligible {rep.eligible}, cached {rep.cached}, no text {rep.no_text}, exhausted {rep.exhausted}, to process {rep.pending_at_start}"
    )
    if rep.blocked_real_text:
        print(
            f"  {rep.blocked_real_text} real guest review(s) NOT sent: external AI processing of real "
            "guest text is disabled (AI_ALLOW_REAL_GUEST_TEXT)"
        )
    if not rep.dry_run:
        print(
            f"  attempted {rep.attempted}: {rep.succeeded} succeeded ({rep.needs_review} flagged needs_review), {rep.failed} failed, {rep.remaining} remaining"
        )
        print(
            f"  tokens in/out {rep.input_tokens}/{rep.output_tokens}, reported cost ${rep.cost_usd:.4f}"
        )
        if rep.stop_reason:
            print(f"  STOPPED: {rep.stop_reason}  (results so far are saved; run again to resume)")
    return {"success": 0, "dry_run": 0, "partial": 3}.get(rep.status, 1)


if __name__ == "__main__":
    sys.exit(main())
