# 0011 Analysis idempotency key has no nullable columns

**Status:** Accepted

**Context:** The v2 proposal's unique key on `review_analysis` included `model_id`, nullable for lexicon analyses. In both PostgreSQL and SQLite, NULLs are distinct in unique indexes, so duplicate lexicon rows would have been accepted silently.

**Decision:**
* Every column in the key is NOT NULL: `(review_id, analyzer, provider, model_id, prompt_version, taxonomy_version, input_hash)`.
* Lexicon analyses use `provider='builtin'`, `model_id='lexicon-<version>'`, `prompt_version='n/a'`. A CHECK requires `provider='builtin'` for lexicon rows.
* A partial unique index allows at most one `is_current` analysis per review. "Current" means "the analysis used for reporting": an LLM analysis supersedes a lexicon one.
* `review_aspect` carries `review_id` for analytics joins. A composite foreign key `(analysis_id, review_id) → review_analysis(id, review_id)` makes a mismatch with the parent analysis impossible; `review_analysis` has `UNIQUE(id, review_id)` as its target.

**Consequences:** Idempotency is enforced by the database on both engines and tested for both analyzers. The redundant unique constraint on `(id, review_id)` costs a little storage.
