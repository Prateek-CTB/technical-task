"""Helpers for AI/analytics tests: scripted providers and a seeded, analysed database."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from reviewhub.demo.seed import seed_demo
from reviewhub.intelligence.providers import MockProvider, ProviderError, ProviderResponse
from reviewhub.intelligence.service import AnalysisConfig, RunReport, run_analysis
from reviewhub.persistence.session import create_session_factory

END = date(2026, 11, 25)
MOCK_CFG = AnalysisConfig("mock", MockProvider.model, "v1", "v1")


class Fake:
    """The deterministic mock, optionally posing as another provider/model and/or misbehaving."""

    def __init__(
        self,
        provider: str = "mock",
        model: str = MockProvider.model,
        fail: Callable[[int, str], str | ProviderError | None] | None = None,
    ) -> None:
        self.provider, self.model, self.fail, self.calls = provider, model, fail, 0

    def complete(self, system: str, user: str) -> ProviderResponse:
        self.calls += 1
        if self.fail is not None:
            r = self.fail(self.calls, user)
            if isinstance(r, ProviderError):
                raise r
            if isinstance(r, str):
                return ProviderResponse(r, self.provider, self.model, None, 5, 5, 0.0)
        base = MockProvider().complete(system, user)
        return ProviderResponse(
            base.content,
            self.provider,
            self.model,
            None,
            base.input_tokens,
            base.output_tokens,
            0.0,
        )


def cfg_for(provider: Fake, prompt: str = "v1", taxonomy: str = "v1") -> AnalysisConfig:
    return AnalysisConfig(provider.provider, provider.model, prompt, taxonomy)


def go(
    factory: sessionmaker[Any], provider: Fake, cfg: AnalysisConfig | None = None, **kw: Any
) -> RunReport:
    kw.setdefault("requests_per_minute", 60000)
    return run_analysis(factory, provider, cfg or cfg_for(provider), sleep=lambda _: None, **kw)


def seeded(engine: Engine, days: int = 60) -> sessionmaker[Any]:
    factory = create_session_factory(engine)
    seed_demo(factory, end=END, days=days)
    return factory


def seeded_and_analysed(engine: Engine, days: int = 120) -> sessionmaker[Any]:
    factory = seeded(engine, days)
    report = go(factory, Fake())
    assert report.failed == 0 and report.remaining == 0
    return factory
