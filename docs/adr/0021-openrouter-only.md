# 0021 OpenRouter free models are the only real AI provider (narrows ADR 0018)

**Status:** Accepted

**Context:** An OpenRouter key is available; no other cloud or local inference is wanted. Free-tier access is limited (about 50 `:free` requests a day on this account) and free endpoints are sometimes rate-limited upstream or withdrawn.

**Decision**
* Providers: `openrouter` (real) and `mock` (deterministic, tests only, stored as `provider='mock'` and labelled in every API response). `none` disables analysis. Ollama, local models, OpenAI and Anthropic adapters are not built.
* **Free only, enforced in code:** the model id must end in `:free` (or be `openrouter/free`); any other model is refused at construction. A response reporting a non-zero cost stops the run. There is no fallback to another model or provider.
* Configuration uses `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_BASE_URL`, `AI_PROVIDER`, `AI_MAX_CONCURRENCY`, `AI_BATCH_SIZE`, `AI_TIMEOUT_SECONDS`, `AI_MAX_RETRIES`, `AI_PROMPT_VERSION`, `AI_TAXONOMY_VERSION`. The key is read only from the backend environment, never logged, stored or returned; stored error text is redacted.
* **Failure policy:** 401/403, 402, 404/model-unavailable, exhausted daily quota and a paid response are fatal: the run stops, finished work is kept, the remainder is reported and resumable. 429 (short), 408, 5xx, timeouts and empty or malformed answers are retried with bounded exponential backoff and jitter (provider `Retry-After` honoured, capped). Invalid output gets one re-ask. After 3 failed attempts a review is not retried automatically, and 5 consecutive failures stop the run, to protect the free allowance.
* `mock` never displaces a real analysis as the current one.
* Model confidence is model-reported, not calibrated; the API says so.

**Observed (2026-11-26):** the account's Zero-Data-Retention setting blocked every free endpoint (HTTP 404 "ZDR violation"); the user disabled it. `google/gemma-4-31b-it:free` was rate-limited upstream, `baidu/ernie-5.5-flash-preview:free` answered and is configured. Free "preview" models can disappear; the model is configuration, and `reviewhub ai status --check` verifies it is listed and zero-priced.

**Consequences:** Real analysis of all reviews takes several days at ~50 requests a day, which the resumable design supports. Guest text goes to the upstream free provider, so only synthetic data is sent (D7 stays "no" for real guest text).
