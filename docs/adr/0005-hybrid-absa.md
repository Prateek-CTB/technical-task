# 0005 Hybrid aspect-based sentiment analysis

**Status:** Accepted

**Context:** Keyword matching cannot handle negation, mixed sentiment or multilingual text (46% of the experimental reviews are not tagged English).

**Decision:** An LLM produces multi-label, per-topic sentiment with verbatim evidence, validated against a strict schema. Evidence is verified as an exact substring of the review. A keyword lexicon is the fallback, a baseline and a disagreement signal for confidence. No accuracy is claimed without a measured, human-labelled evaluation (ADR 0014).

**Consequences:** Per-review cost and an evaluation burden, in exchange for context-aware, explainable output with a measured baseline.
