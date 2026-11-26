"""Strict structure of a model's answer, and its verification against the review text.

Model output is untrusted. It is accepted only if it parses, matches this schema, and
every quoted piece of evidence is found *verbatim* in the review it analyses.
Confidence values are model-reported, not calibrated probabilities.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from reviewhub.persistence.base import SENTIMENTS, TOPIC_KEYS

Topic = Literal[
    "cleanliness", "check_in", "staff", "noise", "facilities", "location", "room_condition", "value"
]
Sentiment = Literal["positive", "negative", "neutral", "mixed"]
assert set(TOPIC_KEYS) == set(Topic.__args__)  # type: ignore[attr-defined]
assert set(SENTIMENTS) == set(Sentiment.__args__)  # type: ignore[attr-defined]

_LANG = re.compile(r"^([a-z]{2,3}(-[A-Za-z0-9]{2,8})?|und)$")


class AspectOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topic: Topic
    sentiment: Sentiment
    certainty: Literal["high", "medium", "low"] = "medium"
    confidence: float = Field(ge=0, le=1)
    evidence: list[str] = Field(min_length=1, max_length=3)

    @field_validator("evidence")
    @classmethod
    def _non_empty_quotes(cls, v: list[str]) -> list[str]:
        if any(not q.strip() for q in v):
            raise ValueError("empty evidence quote")
        return v


class AnalysisOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    overall_sentiment: Sentiment
    summary_en: str = Field(max_length=400)
    language: str
    aspects: list[AspectOut] = Field(max_length=8)
    main_complaint: str | None = Field(default=None, max_length=200)
    main_complaint_topic: Topic | None = None
    suggested_action: str | None = Field(default=None, max_length=240)
    confidence: float = Field(ge=0, le=1)
    flags: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        v = v.strip()
        if v.lower() == "und":
            return "und"
        v = v.replace("_", "-")
        base, _, rest = v.partition("-")
        v = base.lower() + (f"-{rest}" if rest else "")
        if not _LANG.match(v):
            raise ValueError(f"invalid language code {v!r}")
        return v

    @model_validator(mode="after")
    def _unique_topics(self) -> AnalysisOut:
        topics = [a.topic for a in self.aspects]
        if len(topics) != len(set(topics)):
            raise ValueError("duplicate topics in aspects")
        return self


def parse_model_json(content: str) -> AnalysisOut:
    """Parse the model's text into the schema. Tolerates markdown fences and surrounding prose."""
    import json

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model output")
    try:
        data: Any = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    try:
        return AnalysisOut.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValueError(
            f"schema violation at {'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
        ) from exc


# ----------------------------------------------------------------------------- evidence
_PUNCT = " \t\n\r\"'`.,;:!?-–—()[]{}…"
_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _normalise(text: str) -> tuple[str, list[int]]:
    """Casefolded text with whitespace collapsed, plus a map from each output char to its original index."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).translate(_MAP).casefold():
            if c.isspace():
                if prev_space:
                    continue
                c, prev_space = " ", True
            else:
                prev_space = False
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def locate(quote: str, fields: dict[str, str]) -> dict[str, Any] | None:
    """Find ``quote`` verbatim (modulo case, quotes, dashes and whitespace) in one of the fields."""
    needle, _ = _normalise(quote)
    needle = needle.strip(_PUNCT)
    if len(needle) < 2 or (" " in needle and len(needle.split()) < 2 and len(needle) < 4):
        return None
    for name, original in fields.items():
        hay, idx = _normalise(original)
        pos = hay.find(needle)
        if pos >= 0:
            start, end = idx[pos], idx[pos + len(needle) - 1] + 1
            return {"field": name, "quote": original[start:end], "start": start, "end": end}
    return None


@dataclass
class Verified:
    analysis: AnalysisOut
    aspects: list[
        dict[str, Any]
    ]  # topic, sentiment, certainty, confidence, evidence=[{field, quote, start, end}]
    status: str  # ok | needs_review | failed
    flags: list[str] = field(default_factory=list)
    dropped_quotes: int = 0
    dropped_aspects: int = 0
    error: str | None = None


def verify(out: AnalysisOut, fields: dict[str, str], rating: float | None = None) -> Verified:
    """Check evidence against the original text and sanitise unsupported content."""
    flags = [f for f in out.flags if f][:8]
    aspects: list[dict[str, Any]] = []
    dropped_q = dropped_a = 0
    for a in out.aspects:
        found = [loc for q in a.evidence if (loc := locate(q, fields)) is not None]
        dropped_q += len(a.evidence) - len(found)
        if not found:
            dropped_a += 1
            continue
        aspects.append(
            {
                "topic": a.topic,
                "sentiment": a.sentiment,
                "certainty": a.certainty,
                "confidence": a.confidence,
                "evidence": found,
            }
        )
    if out.aspects and not aspects:
        return Verified(
            out,
            [],
            "failed",
            flags,
            dropped_q,
            dropped_a,
            "no evidence could be verified in the review text",
        )

    status = "ok"
    if dropped_q or dropped_a:
        flags.append("unverified_evidence_dropped")
        status = "needs_review"
    negative = {a["topic"] for a in aspects if a["sentiment"] in ("negative", "mixed")}
    if (out.main_complaint_topic and out.main_complaint_topic not in negative) or (
        (out.main_complaint or out.suggested_action) and not negative
    ):
        # A complaint or recommendation must be backed by a verified negative aspect.
        out = out.model_copy(
            update={"main_complaint": None, "main_complaint_topic": None, "suggested_action": None}
        )
        flags.append("unsupported_recommendation_dropped")
        status = "needs_review"
    if any(a["certainty"] == "low" for a in aspects if a["sentiment"] in ("negative", "mixed")):
        flags.append("low_certainty_negative")
        status = "needs_review"
    if rating is not None and (
        (rating >= 9 and out.overall_sentiment == "negative")
        or (rating <= 3 and out.overall_sentiment == "positive")
    ):
        flags.append("rating_sentiment_mismatch")
        status = "needs_review"
    if out.language == "und":
        flags.append("language_undetermined")
    return Verified(out, aspects, status, sorted(set(flags)), dropped_q, dropped_a)
