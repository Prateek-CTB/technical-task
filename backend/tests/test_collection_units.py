"""Collection reliability units: no browser, no database, no real waiting."""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reviewhub.collection.engine import Page, SourceInfo, collect_property
from reviewhub.collection.policy import Pacer, RetryPolicy, run_with_retries
from reviewhub.collection.signals import CollectionError, Outcome, Signals, classify
from reviewhub.collection.spool import Spool


# --------------------------------------------------------------------------- classification
@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        (Signals(cards_found=10, api_refusal="Request not supported"), Outcome.OK),
        (Signals(api_refusal='{"message":"Request not supported"}'), Outcome.BLOCKED),
        (Signals(http_status=200, page_text="Please solve this CAPTCHA"), Outcome.CAPTCHA),
        (Signals(http_status=405, headers={"x-amzn-waf-action": "captcha"}), Outcome.CAPTCHA),
        (Signals(http_status=429), Outcome.RATE_LIMITED),
        (Signals(http_status=403), Outcome.BLOCKED),
        (Signals(http_status=503), Outcome.TRANSIENT),
        (Signals(network_error="net::ERR_CONNECTION_RESET"), Outcome.TRANSIENT),
        (Signals(timed_out=True), Outcome.TRANSIENT),
        (Signals(timed_out=True, http_status=200, page_text="x" * 500), Outcome.STRUCTURE_CHANGED),
        # The WAF script on an ordinary, working page must not look like a CAPTCHA.
        (
            Signals(http_status=202, page_text="awswaf challenge.js " * 30),
            Outcome.STRUCTURE_CHANGED,
        ),
    ],
)
def test_classify(signals: Signals, expected: Outcome) -> None:
    assert classify(signals) == expected


def test_site_decisions_are_never_retryable() -> None:
    for outcome in (Outcome.CAPTCHA, Outcome.BLOCKED):
        err = CollectionError(outcome, "x")
        assert not err.retryable
        assert err.site_decision
        assert err.status == "blocked"
    assert CollectionError(Outcome.TRANSIENT, "x").retryable
    assert CollectionError(Outcome.TRANSIENT, "x").status == "failed"


# --------------------------------------------------------------------------- backoff
class _Mid:
    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def test_backoff_is_exponential_capped_and_honours_retry_after() -> None:
    p = RetryPolicy(max_attempts=6, base_delay=2, max_delay=20, jitter=0.25)
    assert [p.delay(n, rng=_Mid()) for n in (1, 2, 3, 4, 5)] == [2, 4, 8, 16, 20]
    assert p.delay(1, rng=_Mid(), retry_after=30) == 30
    assert p.delay(1, rng=_Mid(), retry_after=99999) == 300  # capped


def test_jitter_stays_within_bounds() -> None:
    p = RetryPolicy(base_delay=10, jitter=0.25)
    rng = random.Random(1)
    assert all(7.5 <= p.delay(1, rng=rng) <= 12.5 for _ in range(200))


def test_retries_transient_then_succeeds_and_reports_waits() -> None:
    calls, waits, events = [], [], []

    def op() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise CollectionError(Outcome.TRANSIENT, "reset")
        return "ok"

    out = run_with_retries(
        op,
        RetryPolicy(max_attempts=4, base_delay=1, max_delay=60, jitter=0),
        describe="t",
        sleep=waits.append,
        on_event=lambda ev, d: events.append(ev),
    )
    assert out == "ok"
    assert waits == [1, 2]
    assert events == ["retry", "retry"]


def test_gives_up_after_max_attempts_and_never_retries_a_block() -> None:
    n = 0

    def flaky() -> None:
        nonlocal n
        n += 1
        raise CollectionError(Outcome.TRANSIENT, "down")

    with pytest.raises(CollectionError):
        run_with_retries(
            flaky, RetryPolicy(max_attempts=3, jitter=0), describe="t", sleep=lambda _: None
        )
    assert n == 3

    m = 0

    def blocked() -> None:
        nonlocal m
        m += 1
        raise CollectionError(Outcome.BLOCKED, "no")

    with pytest.raises(CollectionError):
        run_with_retries(blocked, RetryPolicy(max_attempts=5), describe="t", sleep=lambda _: None)
    assert m == 1


def test_pacer_spaces_actions_globally() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    pacer = Pacer(3, 3, sleep=sleep, clock=lambda: now[0])
    assert pacer.wait() == 0  # first action is immediate
    pacer.wait()
    pacer.wait()
    assert slept == [3, 3]


# --------------------------------------------------------------------------- spool
def test_spool_survives_a_torn_last_line(tmp_path: Path) -> None:
    spool = Spool(tmp_path)
    spool.append_page("chateau-de-venus", 7, 1, [{"a": 1}])
    spool.append_page("chateau-de-venus", 7, 2, [{"a": 2}])
    with spool.path_for("chateau-de-venus", 7).open("a", encoding="utf-8") as fh:
        fh.write('{"page": 3, "records": [{"a"')  # power loss mid-write
    left = spool.leftovers()
    assert len(left) == 1
    assert left[0].last_page == 2
    assert left[0].records == [{"a": 1}, {"a": 2}]
    assert spool.leftovers(exclude_run=7) == []


# --------------------------------------------------------------------------- engine
def rec(day: int, text: str = "") -> dict[str, Any]:
    return {
        "property": "p",
        "rating": 8,
        "review_date": f"2026-11-{day:02d}",
        "title": "t",
        "liked": text or f"liked {day}",
    }


class FakeSource:
    """Pages of records, with scripted failures: {("read"|"advance"|"open", n): [errors]}."""

    def __init__(
        self,
        pages: list[list[dict[str, Any]]],
        failures: dict[Any, list[CollectionError]] | None = None,
    ):
        self.pages, self.failures = pages, failures or {}
        self.i = 0
        self.opened = 0
        self.closed = False
        self.reads = 0

    def _maybe_fail(self, key: Any) -> None:
        errs = self.failures.get(key)
        if errs:
            raise errs.pop(0)

    def open(self, start_page: int = 1) -> SourceInfo:
        self.opened += 1
        self._maybe_fail(("open", self.opened))
        self.i = start_page - 1
        return SourceInfo(headline_total=99, sorted_newest=True)

    def read_current(self) -> Page:
        self.reads += 1
        self._maybe_fail(("read", self.i + 1))
        return Page(self.i + 1, self.pages[self.i], has_next=self.i + 1 < len(self.pages))

    def advance(self) -> bool:
        self._maybe_fail(("advance", self.i + 1))
        if self.i + 1 >= len(self.pages):
            return False
        self.i += 1
        return True

    def close(self) -> None:
        self.closed = True


def run_engine(source: FakeSource, **kw: Any) -> tuple[Any, list[tuple[int, int]], list[str]]:
    spooled: list[tuple[int, int]] = []
    events: list[str] = []
    res = collect_property(
        "p",
        source,
        policy=RetryPolicy(max_attempts=3, base_delay=0, jitter=0),
        pacer=Pacer(0, 0),
        max_pages=kw.pop("max_pages", 10),
        stop_before=kw.pop("stop_before", None),
        spool_page=lambda n, r: spooled.append((n, len(r))),
        checkpoint=kw.pop("checkpoint", lambda r, p: True),
        on_event=lambda ev, d: events.append(ev),
        sleep=lambda _: None,
        **kw,
    )
    return res, spooled, events


def test_engine_reads_all_pages_in_order() -> None:
    src = FakeSource([[rec(20), rec(19)], [rec(18)], [rec(17)]])
    res, spooled, _ = run_engine(src)
    assert res.status == "success"
    assert spooled == [(1, 2), (2, 1), (3, 1)]
    assert src.closed


def test_engine_recovers_from_transient_failures_at_the_same_page() -> None:
    boom = lambda: CollectionError(Outcome.TRANSIENT, "reset")  # noqa: E731
    src = FakeSource(
        [[rec(20)], [rec(19)], [rec(18)]],
        failures={("read", 2): [boom(), boom()], ("advance", 1): [boom()]},
    )
    res, spooled, events = run_engine(src)
    assert res.status == "success"
    assert [n for n, _ in spooled] == [1, 2, 3]  # no page lost or repeated
    assert events.count("retry") == 3
    assert src.opened >= 2  # recovery reopened the page


def test_engine_stops_on_block_keeping_what_was_collected() -> None:
    src = FakeSource(
        [[rec(20)], [rec(19)]],
        failures={("read", 2): [CollectionError(Outcome.BLOCKED, "refused")]},
    )
    res, spooled, _ = run_engine(src)
    assert res.status == "blocked"
    assert res.outcome == Outcome.BLOCKED
    assert spooled == [(1, 1)]  # page 1 is safe
    assert src.reads == 2  # the refusal was not retried


def test_engine_retry_budget_exhausted_is_a_failure_not_a_crash() -> None:
    err = lambda: CollectionError(Outcome.TRANSIENT, "down")  # noqa: E731
    src = FakeSource([[rec(20)]], failures={("open", n): [err()] for n in (1, 2, 3)})
    res, _, _ = run_engine(src)
    assert res.status == "failed"
    assert res.outcome == Outcome.TRANSIENT
    assert src.closed


def test_engine_incremental_stop_rule() -> None:
    src = FakeSource([[rec(20)], [rec(10)], [rec(9)]])
    res, spooled, _ = run_engine(src, stop_before=date(2026, 11, 15))
    assert res.status == "success"
    assert [n for n, _ in spooled] == [1, 2]  # page 2 is older than the cut-off: stop there


def test_engine_respects_max_pages() -> None:
    src = FakeSource([[rec(20)], [rec(19)], [rec(18)]])
    res, spooled, _ = run_engine(src, max_pages=2)
    assert res.pages == 2
    assert len(spooled) == 2


def test_engine_detects_pagination_that_did_not_advance() -> None:
    src = FakeSource([[rec(20)], [rec(20)]])  # page 2 identical to page 1
    res, _, _ = run_engine(src)
    assert res.status == "failed"
    assert res.outcome == Outcome.STRUCTURE_CHANGED
    assert "did not advance" in (res.error or "")


def test_engine_resumes_from_a_start_page() -> None:
    src = FakeSource([[rec(20)], [rec(19)], [rec(18)]])
    res, spooled, _ = run_engine(src, start_page=2)
    assert [n for n, _ in spooled] == [2, 3]
    assert res.status == "success"


def test_engine_stops_when_the_run_is_no_longer_active() -> None:
    src = FakeSource([[rec(20)], [rec(19)]])
    res, spooled, _ = run_engine(src, checkpoint=lambda r, p: False)
    assert res.stopped_early
    assert len(spooled) == 1
