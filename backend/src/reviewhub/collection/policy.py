"""Retry with exponential backoff, and request pacing shared by all workers."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from .signals import CollectionError

T = TypeVar("T")

#: A server-supplied Retry-After longer than this is not waited for beyond the cap.
RETRY_AFTER_CAP = 300.0


class _Rng(Protocol):
    def uniform(self, a: float, b: float) -> float: ...


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4  # total tries, including the first
    base_delay: float = 2.0
    max_delay: float = 120.0
    jitter: float = 0.25  # +/- fraction, so parallel workers do not retry in lockstep

    def delay(
        self, retry_number: int, *, rng: _Rng = random, retry_after: float | None = None
    ) -> float:
        """Seconds to wait before retry ``retry_number`` (1 = the first retry)."""
        delay = float(min(self.max_delay, self.base_delay * 2 ** (retry_number - 1)))
        delay *= 1 + float(rng.uniform(-self.jitter, self.jitter))
        if retry_after is not None:
            delay = max(delay, min(retry_after, RETRY_AFTER_CAP))
        return max(0.0, delay)


EventHook = Callable[[str, dict[str, object]], None]


def run_with_retries(
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    describe: str,
    recover: Callable[[], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rng: _Rng = random,
    on_event: EventHook | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> T:
    """Run ``operation``; retry only :class:`CollectionError` that are ``retryable``.

    ``recover`` runs after the wait and before the next attempt (e.g. reload the page and
    return to the page we were on). Non-retryable errors, and the last attempt's error, propagate.
    """
    attempt = 1
    while True:
        try:
            return operation()
        except CollectionError as exc:
            if not exc.retryable or attempt >= policy.max_attempts or should_stop():
                raise
            wait = policy.delay(attempt, rng=rng, retry_after=exc.retry_after)
            if on_event:
                on_event(
                    "retry",
                    {
                        "operation": describe,
                        "attempt": attempt,
                        "outcome": exc.outcome.value,
                        "wait_s": round(wait, 1),
                        "error": str(exc)[:200],
                    },
                )
            sleep(wait)
            if recover is not None:
                recover()
            attempt += 1


class Pacer:
    """Global minimum spacing between page actions, with jitter.

    Shared by every worker, so ``concurrency`` lets several browsers render at once but never
    raises the rate at which requests reach the site.
    """

    def __init__(
        self,
        low: float,
        high: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: _Rng = random,
    ) -> None:
        self._low, self._high = low, max(low, high)
        self._sleep, self._clock, self._rng = sleep, clock, rng
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> float:
        """Block until the next action is allowed. Returns the seconds slept."""
        with self._lock:
            now = self._clock()
            slept = max(0.0, self._next_at - now)
            if slept:
                self._sleep(slept)
            self._next_at = self._clock() + self._rng.uniform(self._low, self._high)
            return slept
