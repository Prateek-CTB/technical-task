"""Build the configured provider and analysis config from settings."""

from __future__ import annotations

from reviewhub.core.settings import Settings

from .lexicon import LexiconProvider
from .providers import ConfigError, MockProvider, OpenRouterProvider, ReviewAnalyzer
from .service import AnalysisConfig


def _name(settings: Settings, override: str | None) -> str:
    return override or settings.ai_provider


def build_provider(settings: Settings, override: str | None = None) -> ReviewAnalyzer:
    """``override`` selects a provider for one command (e.g. ``ai analyze --provider builtin``)."""
    name = _name(settings, override)
    if name == "builtin":
        return LexiconProvider()
    if name == "mock":
        return MockProvider()
    if name == "openrouter":
        key = (
            settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None
        )
        return OpenRouterProvider(
            api_key=key,
            model=settings.openrouter_model,
            base_url=settings.openrouter_base_url,
            timeout=settings.ai_timeout_seconds,
        )
    raise ConfigError(
        "no AI provider configured: set AI_PROVIDER=openrouter (with OPENROUTER_API_KEY and "
        "OPENROUTER_MODEL), AI_PROVIDER=builtin (offline rule-based) or AI_PROVIDER=mock"
    )


def build_config(settings: Settings, override: str | None = None) -> AnalysisConfig:
    name = _name(settings, override)
    if name == "builtin":
        # The lexicon has no prompt; its version is the model id, its taxonomy the shared one.
        return AnalysisConfig(
            "builtin", LexiconProvider.model, "rules-v1", settings.ai_taxonomy_version
        )
    if name == "mock":
        provider, model = "mock", MockProvider.model
    elif name == "openrouter":
        provider, model = "openrouter", settings.openrouter_model
    else:
        provider, model = "none", ""
    return AnalysisConfig(provider, model, settings.ai_prompt_version, settings.ai_taxonomy_version)
