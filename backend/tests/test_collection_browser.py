"""Browser layer with mocked Playwright objects: classification, profiles, display preflight."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from reviewhub.collection import browser as br
from reviewhub.collection.browser import BookingPageSource, BrowserUnavailable, browser_session
from reviewhub.collection.signals import CollectionError, Outcome


class TimeoutError(Exception):  # Playwright's error is also named TimeoutError
    pass


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page, self.selector = page, selector

    @property
    def first(self) -> FakeLocator:
        return self

    def wait_for(self, timeout: float = 0) -> None:
        if self.page.trigger_timeout:
            raise TimeoutError("waiting for trigger")

    def click(self) -> None:
        self.page.clicked.append(self.selector)

    def count(self) -> int:
        return len(self.page.cards) if "review-card" in self.selector else 0

    def is_disabled(self) -> bool:
        return False

    def inner_text(self, timeout: float = 0) -> str:
        return "Scored 8.4"


class FakePage:
    def __init__(
        self,
        *,
        cards: list[dict[str, str]],
        body: str = "",
        trigger_timeout: bool = False,
        card_timeout: bool = False,
        graphql_body: str | None = None,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.cards, self.body = cards, body
        self.trigger_timeout, self.card_timeout = trigger_timeout, card_timeout
        self.graphql_body, self.status, self.headers = graphql_body, status, headers or {}
        self.clicked: list[str] = []
        self.handler: Any = None
        self.main_frame = object()

    def on(self, _: str, handler: Any) -> None:
        self.handler = handler

    def goto(self, url: str, **_: Any) -> None:
        self.handler(
            SimpleNamespace(
                url=url,
                status=self.status,
                headers=self.headers,
                frame=self.main_frame,
                request=SimpleNamespace(resource_type="document"),
                text=lambda: "",
            )
        )

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    def wait_for_timeout(self, _: float) -> None:
        pass

    def wait_for_selector(self, selector: str, timeout: float = 0) -> None:
        if self.graphql_body is not None:  # the click triggers the review-list API call
            self.handler(
                SimpleNamespace(
                    url="https://www.booking.com/dml/graphql",
                    status=200,
                    headers={},
                    frame=object(),
                    request=SimpleNamespace(resource_type="fetch"),
                    text=lambda: self.graphql_body,
                )
            )
        if self.card_timeout or not self.cards:
            raise TimeoutError("no cards")

    def evaluate(self, script: str) -> Any:
        return self.cards if "review-card" in script else "All (123)"

    def inner_text(self, selector: str, timeout: float = 0) -> str:
        return self.body

    def close(self) -> None:
        pass


def card(day: int = 20) -> dict[str, str]:
    return {
        "date": f"Reviewed: {day} November 2026",
        "title": "Great",
        "score": "Scored 9.0",
        "positive": "Lovely staff",
        "negative": "",
        "room": "Double",
        "nights": "2 nights",
        "traveller": "Couple",
        "stay": "",
    }


def source_for(page: FakePage) -> BookingPageSource:
    ctx = SimpleNamespace(new_page=lambda: page)
    return BookingPageSource(ctx, "chateau-de-venus", "https://example.test/h", action_timeout_s=1)


def test_reads_cards_into_import_records() -> None:
    src = source_for(FakePage(cards=[card(20), card(19)]))
    info = src.open()
    assert info.headline_total == 123 and info.headline_score == 8.4
    page = src.read_current()
    assert [r["review_date"] for r in page.records] == ["2026-11-20", "2026-11-19"]
    assert page.records[0]["property"] == "chateau-de-venus"
    assert "reviewer" not in str(page.records[0]).lower()  # reviewer names are never read


def test_explicit_api_refusal_is_blocked_and_not_retryable() -> None:
    page = FakePage(
        cards=[],
        graphql_body='{"data":{"reviewListFrontend":{"message":"Request not supported"}}}',
    )
    with pytest.raises(CollectionError) as info:
        source_for(page).open()
    assert info.value.outcome == Outcome.BLOCKED
    assert not info.value.retryable
    assert "refused" in str(info.value)


def test_captcha_page_is_reported_as_captcha() -> None:
    page = FakePage(cards=[], trigger_timeout=True, body="Please verify you are human (CAPTCHA)")
    with pytest.raises(CollectionError) as info:
        source_for(page).open()
    assert info.value.outcome == Outcome.CAPTCHA


def test_http_429_is_rate_limited_with_retry_after() -> None:
    page = FakePage(cards=[], trigger_timeout=True, status=429, headers={"Retry-After": "30"})
    with pytest.raises(CollectionError) as info:
        source_for(page).open()
    assert info.value.outcome == Outcome.RATE_LIMITED
    assert info.value.retry_after == 30
    assert info.value.retryable


def test_slow_or_broken_page_is_transient() -> None:
    page = FakePage(cards=[], trigger_timeout=True, status=503, body="")
    with pytest.raises(CollectionError) as info:
        source_for(page).open()
    assert info.value.outcome == Outcome.TRANSIENT


def test_changed_layout_is_structure_changed_not_retried() -> None:
    page = FakePage(cards=[], trigger_timeout=True, status=200, body="page text " * 100)
    with pytest.raises(CollectionError) as info:
        source_for(page).open()
    assert info.value.outcome == Outcome.STRUCTURE_CHANGED
    assert not info.value.retryable


# --------------------------------------------------------------------------- sessions
class FakePlaywright:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        ctx = SimpleNamespace(
            set_default_navigation_timeout=lambda ms: None,
            set_default_timeout=lambda ms: None,
            close=lambda: self.calls.append(("closed", {})),
            new_page=lambda: None,
        )
        browser = SimpleNamespace(new_context=lambda **kw: ctx, close=lambda: None)
        self.chromium = SimpleNamespace(
            launch_persistent_context=lambda path, **kw: (
                self.calls.append(("persistent", {"path": path, **kw})) or ctx
            ),
            launch=lambda **kw: self.calls.append(("ephemeral", kw)) or browser,
        )

    def __enter__(self) -> FakePlaywright:
        return self

    def __exit__(self, *_: object) -> None:
        pass


@pytest.fixture
def fake_pw(monkeypatch: pytest.MonkeyPatch) -> FakePlaywright:
    fake = FakePlaywright()
    import playwright.sync_api as sync_api

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: fake)
    return fake


def test_persistent_profile_is_used_and_identity_is_not_modified(
    fake_pw: FakePlaywright, tmp_path: Path
) -> None:
    profile = tmp_path / "profile" / "w0"
    with browser_session(headless=True, profile_dir=profile, nav_timeout_s=5, action_timeout_s=5):
        pass
    kind, kw = fake_pw.calls[0]
    assert kind == "persistent" and kw["path"] == str(profile) and kw["headless"] is True
    assert profile.is_dir()
    # A stock browser: no user agent, extra headers, init scripts, args or webdriver overrides.
    for forbidden in ("user_agent", "args", "extra_http_headers", "ignore_default_args"):
        assert forbidden not in kw


def test_ephemeral_session_without_a_profile(fake_pw: FakePlaywright) -> None:
    with browser_session(headless=True, profile_dir=None, nav_timeout_s=5, action_timeout_s=5):
        pass
    assert fake_pw.calls[0][0] == "ephemeral"


def test_headed_without_a_display_on_linux_fails_clearly(
    fake_pw: FakePlaywright, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with (
        pytest.raises(BrowserUnavailable, match="needs a display"),
        browser_session(headless=False, profile_dir=None, nav_timeout_s=5, action_timeout_s=5),
    ):
        pass
    assert fake_pw.calls == []  # nothing was launched


def test_browser_that_cannot_start_names_the_fix(
    fake_pw: FakePlaywright, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**_: Any) -> None:
        raise RuntimeError("Executable doesn't exist at /x/chrome")

    monkeypatch.setattr(fake_pw.chromium, "launch", boom)
    with (
        pytest.raises(BrowserUnavailable, match="playwright install"),
        browser_session(headless=True, profile_dir=None, nav_timeout_s=5, action_timeout_s=5),
    ):
        pass


def test_module_exposes_no_stealth_helpers() -> None:
    text = Path(br.__file__).read_text(encoding="utf-8").lower()
    for banned in ("add_init_script", "user_agent", "webdriver", "--disable-blink", "stealth"):
        assert banned not in text.replace("no identity changes", "")
