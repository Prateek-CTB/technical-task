"""Model providers: a deterministic mock (tests) and OpenRouter free models (real inference).

Only these two exist (ADR 0021). The OpenRouter adapter refuses any model that is not
free, never falls back to another model or provider, classifies every failure so the
caller knows whether to retry, wait or stop, and never logs credentials.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


# ----------------------------------------------------------------------------- errors
class ProviderError(Exception):
    retryable = False
    fatal = False  # fatal: stop the whole run (it is not this review's fault)
    code = "provider_error"


class ConfigError(ProviderError):
    fatal, code = True, "config"


class AuthError(ProviderError):
    fatal, code = True, "auth"


class QuotaExhausted(ProviderError):
    fatal, code = True, "quota_exhausted"


class ModelUnavailable(ProviderError):
    fatal, code = True, "model_unavailable"


class PaidRequestDetected(ProviderError):
    fatal, code = True, "paid_request"


class RateLimited(ProviderError):
    retryable, code = True, "rate_limited"

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(ProviderError):
    retryable, code = True, "transient"


class BadResponse(ProviderError):
    retryable, code = True, "bad_response"


# ----------------------------------------------------------------------------- types
@dataclass
class ProviderResponse:
    content: str
    provider: str  # "openrouter" | "mock"
    model: str  # model that answered
    upstream: str | None = None  # who served it (OpenRouter reports this)
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class ReviewAnalyzer(Protocol):
    provider: str
    model: str

    def complete(self, system: str, user: str) -> ProviderResponse: ...


def redact(text: str, secret: str | None = None) -> str:
    """Remove anything that looks like an API key before text is stored or logged."""
    text = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted]", text)
    if secret:
        text = text.replace(secret, "[redacted]")
    return text


# ----------------------------------------------------------------------------- OpenRouter
def assert_free_model(model: str) -> None:
    if not model:
        raise ConfigError("OPENROUTER_MODEL is not set")
    if not (model.endswith(":free") or model == "openrouter/free"):
        raise ConfigError(
            f"refusing non-free model {model!r}: only OpenRouter models ending in ':free' are allowed"
        )


class OpenRouterProvider:
    provider = "openrouter"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: float = 60,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ConfigError("OPENROUTER_API_KEY is not set")
        assert_free_model(model)
        self.model = model
        self._key = api_key
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._client = client or httpx.Client(timeout=timeout)

    def complete(self, system: str, user: str) -> ProviderResponse:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": 1500,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "X-Title": "ReviewHub (Azzurro review intelligence)",
        }
        try:
            resp = self._client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise TransientError("request timed out") from exc
        except httpx.HTTPError as exc:
            raise TransientError(f"network error: {type(exc).__name__}") from exc
        return self._parse(resp)

    # -- response handling -------------------------------------------------------
    def _parse(self, resp: httpx.Response) -> ProviderResponse:
        try:
            data: dict[str, Any] = resp.json()
        except ValueError:
            data = {}
        err = data.get("error") if isinstance(data, dict) else None
        status = resp.status_code
        if isinstance(err, dict) and isinstance(err.get("code"), int) and status < 400:
            status = err["code"]  # OpenRouter can report failures inside a 200 body
        if status >= 400 or err:
            self._raise_for(status, err, resp)
        try:
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise BadResponse("response has no choices[0].message") from exc
        if not content.strip():
            raise BadResponse("empty model output")
        usage = data.get("usage") or {}
        cost = usage.get("cost")
        if isinstance(cost, (int, float)) and cost > 0:
            raise PaidRequestDetected(
                f"provider reported a non-zero cost ({cost}); stopping, free models only"
            )
        return ProviderResponse(
            content=content,
            provider=self.provider,
            model=str(data.get("model") or self.model),
            upstream=data.get("provider") if isinstance(data.get("provider"), str) else None,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        )

    def _raise_for(self, status: int, err: Any, resp: httpx.Response) -> None:
        message = redact(
            str(err.get("message") if isinstance(err, dict) else resp.text[:200]), self._key
        )[:300]
        if status in (401, 403):
            raise AuthError(f"authentication failed ({status}): {message}")
        if status == 402:
            raise QuotaExhausted(
                f"payment/credits required ({status}); no paid requests are allowed: {message}"
            )
        if status == 404 or (status == 400 and "model" in message.lower()):
            raise ModelUnavailable(f"model {self.model!r} unavailable ({status}): {message}")
        if status == 429:
            wait = _retry_after(resp)
            if (
                (wait is not None and wait > 120)
                or "per-day" in message.lower()
                or "per day" in message.lower()
            ):
                raise QuotaExhausted(f"free quota exhausted: {message}")
            raise RateLimited(f"rate limited: {message}", wait)
        if status == 408 or status >= 500:
            raise TransientError(f"provider error {status}: {message}")
        raise BadResponse(f"request rejected ({status}): {message}")


def _retry_after(resp: httpx.Response) -> float | None:
    h = resp.headers
    if (v := h.get("retry-after")) and v.replace(".", "", 1).isdigit():
        return float(v)
    if (v := h.get("x-ratelimit-reset")) and v.isdigit():  # epoch milliseconds
        return max(0.0, int(v) / 1000 - time.time())
    return None


def check_model_listed(
    model: str, base_url: str, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Ask OpenRouter's public model list (no quota used) whether the model exists and is zero-priced."""
    assert_free_model(model)
    http = client or httpx.Client(timeout=30)
    resp = http.get(base_url.rstrip("/") + "/models")
    resp.raise_for_status()
    found = next((m for m in resp.json().get("data", []) if m.get("id") == model), None)
    if found is None:
        raise ModelUnavailable(f"{model!r} is not in OpenRouter's current model list")
    pricing = found.get("pricing") or {}
    free = all(str(pricing.get(k, "1")) in ("0", "0.0") for k in ("prompt", "completion"))
    if not free:
        raise ConfigError(f"{model!r} is listed but not zero-priced ({pricing})")
    return {
        "id": model,
        "pricing": pricing,
        "context_length": found.get("context_length"),
        "supports_response_format": "response_format" in (found.get("supported_parameters") or []),
    }


# ----------------------------------------------------------------------------- mock
_CUES: dict[str, tuple[list[str], list[str]]] = {
    "cleanliness": (
        ["clean", "spotless", "tidy", "fresh"],
        ["dirty", "dust", "stain", "smell", "hair", "grubby", "cockroach", "sticky", "musty"],
    ),
    "check_in": (
        ["check-in", "check in", "keys", "arrived"],
        [
            "reception was unattended",
            "no record",
            "key card failed",
            "wasn't ready",
            "code didn't work",
            "deposit",
            "check-in was slow",
        ],
    ),
    "staff": (
        ["friendly", "helpful", "lovely", "polite", "legend"],
        ["rude", "unhelpful", "ignored", "uninterested", "curt", "dismissed"],
    ),
    "noise": (
        ["quiet", "slept", "no noise"],
        ["loud", "noise", "noisy", "woke", "thin walls", "sirens", "slamming"],
    ),
    "facilities": (
        ["kitchen", "wi-fi", "wifi", "rooftop", "laundry", "lift"],
        [
            "lift was out",
            "wi-fi kept",
            "wifi kept",
            "no hot water",
            "broken",
            "out of order",
            "washing machine",
        ],
    ),
    "location": (["location", "station", "walk", "near"], ["too far", "sketchy", "further from"]),
    "room_condition": (
        ["bed", "air-con", "comfy", "comfortable"],
        ["mattress", "peeling", "leaks", "tiny", "tired", "didn't work", "rattled", "window"],
    ),
    "value": (
        ["value", "cheap", "worth", "fair price"],
        ["expensive", "overpriced", "fee", "not worth", "charged"],
    ),
}
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+| - ")


class MockProvider:
    """Deterministic, offline, keyword-based. Its output is NOT AI inference and is labelled
    ``provider='mock'`` everywhere it is stored or reported."""

    provider = "mock"
    model = "mock-keyword-1"

    def complete(self, system: str, user: str) -> ProviderResponse:
        m = re.search(r"<review>\s*(\{.*\})\s*</review>", user, re.S)
        data = json.loads(m.group(1)) if m else {}
        fields = {k: str(data.get(k) or "") for k in ("title", "liked", "disliked")}
        aspects: dict[str, dict[str, Any]] = {}
        for fname, polarity in (("liked", "positive"), ("disliked", "negative")):
            for sentence in (s.strip() for s in _SPLIT.split(fields[fname]) if s.strip()):
                low = sentence.lower()
                for topic, (pos_cues, neg_cues) in _CUES.items():
                    if topic in aspects:
                        continue
                    cues = neg_cues if polarity == "negative" else pos_cues
                    if any(c in low for c in cues):
                        aspects[topic] = {
                            "topic": topic,
                            "sentiment": polarity,
                            "certainty": "medium",
                            "confidence": 0.6,
                            "evidence": [sentence.rstrip(".!")],
                        }
        sentiments = {a["sentiment"] for a in aspects.values()}
        overall = (
            "mixed"
            if len(sentiments) == 2
            else (next(iter(sentiments)) if sentiments else "neutral")
        )
        neg: dict[str, Any] | None = next(
            (asp for asp in aspects.values() if asp["sentiment"] == "negative"), None
        )
        text = " ".join(fields.values()).lower()
        ascii_only = all(ord(c) < 128 for c in text)
        flags = ["possible_prompt_injection"] if "ignore" in text and "instruction" in text else []
        out = {
            "overall_sentiment": overall,
            "summary_en": f"Mock analysis: {overall} review mentioning {', '.join(aspects) or 'no specific topic'}.",
            "language": "en" if ascii_only else "und",
            "aspects": list(aspects.values()),
            "main_complaint": neg["evidence"][0][:150] if neg else None,
            "main_complaint_topic": neg["topic"] if neg else None,
            "suggested_action": f"Review {neg['topic'].replace('_', ' ')} issues raised by guests."
            if neg
            else None,
            "confidence": 0.6,
            "flags": flags,
        }
        return ProviderResponse(
            json.dumps(out),
            "mock",
            self.model,
            None,
            len(user) // 4,
            len(json.dumps(out)) // 4,
            0.0,
        )
