"""Agreement between the rule-based analyzer and the LLM on reviews analysed by both.

This is a *consistency* check between two automated methods, not accuracy: neither is ground
truth, and no human-labelled evaluation set exists yet (ADR 0014). It answers "how often would
the dashboard change if a review were analysed by the other method?", which is what an operator
needs to know when most reviews carry only the rule-based analysis.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from reviewhub.persistence.models import ReviewAnalysis, ReviewAspect

USABLE = ("ok", "needs_review")
NEGATIVE_SIDE = {"negative", "mixed"}


@dataclass
class Agreement:
    reviews: int = 0
    topic_tp: int = 0  # topic found by both
    topic_fp: int = 0  # rule-based only
    topic_fn: int = 0  # LLM only
    polarity_agree: int = 0  # shared topics whose polarity matches (negative ~ mixed)
    overall_agree: int = 0

    @property
    def precision(self) -> float | None:
        d = self.topic_tp + self.topic_fp
        return self.topic_tp / d if d else None

    @property
    def recall(self) -> float | None:
        d = self.topic_tp + self.topic_fn
        return self.topic_tp / d if d else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r else None

    @property
    def polarity_rate(self) -> float | None:
        return self.polarity_agree / self.topic_tp if self.topic_tp else None

    @property
    def overall_rate(self) -> float | None:
        return self.overall_agree / self.reviews if self.reviews else None


def _aspects(s: Session, analysis_id: int) -> dict[str, str]:
    return {
        t: sent
        for t, sent in s.execute(
            select(ReviewAspect.topic, ReviewAspect.sentiment).where(
                ReviewAspect.analysis_id == analysis_id
            )
        )
    }


def rule_based_vs_llm(s: Session) -> Agreement:
    """Compare, per review, the newest usable LLM analysis with the rule-based one."""
    llm: dict[int, ReviewAnalysis] = {}
    lex: dict[int, ReviewAnalysis] = {}
    for a in s.scalars(
        select(ReviewAnalysis)
        .where(ReviewAnalysis.status.in_(USABLE))
        .order_by(ReviewAnalysis.created_at)
    ):
        if a.provider == "builtin":
            lex[a.review_id] = a
        elif a.analyzer == "llm" and a.provider != "mock":
            llm[a.review_id] = a
    out = Agreement()
    for rid in sorted(llm.keys() & lex.keys()):
        ref, cand = _aspects(s, llm[rid].id), _aspects(s, lex[rid].id)
        out.reviews += 1
        shared = ref.keys() & cand.keys()
        out.topic_tp += len(shared)
        out.topic_fp += len(cand.keys() - ref.keys())
        out.topic_fn += len(ref.keys() - cand.keys())
        out.polarity_agree += sum(
            ref[t] == cand[t] or {ref[t], cand[t]} <= NEGATIVE_SIDE for t in shared
        )
        out.overall_agree += llm[rid].overall_sentiment == lex[rid].overall_sentiment
    return out
