# 0018 AI provider strategy (supersedes ADR 0006)

**Status:** Accepted. Choice of free provider and model: open (D4, D7).

**Context:** No paid API key is available. P4 must not depend on one, yet the AI analysis has to run on real inference and be testable offline.

**Decision:** `ReviewAnalyzer` stays a protocol. Provider adapters, selected by `REVIEWHUB_AI_PROVIDER`:

| Provider | Purpose | Needs |
|---|---|---|
| `mock` | **All automated tests and CI.** Deterministic: output is a pure function of the input (and of scripted fault injection: malformed JSON, fabricated quotes, timeouts, refusals). | nothing |
| `openai_compatible` | **Real inference without a paid key.** One adapter for any endpoint that speaks the OpenAI chat-completions format: free-tier hosted APIs, or a local server such as Ollama / llama.cpp. | base URL + model; a key only if the host wants one |
| `anthropic` | Optional cloud integration | `ANTHROPIC_API_KEY` |
| `none` | Lexicon analyzer only (always available as fallback) | nothing |

**Honest constraints**
* "Free hosted API" almost always means a **free-tier account key** (sign-up, no payment). A truly key-less option is only a **local** model. I have not verified any provider's current free-tier availability, limits or terms; they change, so the project hard-codes none.
* **Guest-review text leaves the machine** when a hosted provider is used, and free tiers may log or train on inputs. That is a data-protection decision for Azzurro (D7), not a technical default. A local model sends nothing out. Until D7 is answered, live inference through a hosted provider is limited to synthetic data; real guest text is only analysed by the mock, the lexicon, or a local model.
* Small or free models follow structured-output instructions less reliably and have rate limits. The design already assumes this: strict schema validation with bounded retries, verbatim-evidence verification, a request-rate limiter (`REVIEWHUB_AI_REQUESTS_PER_MINUTE`), resumable `analyse` runs (cached per review), and the lexicon fallback. Quality is **measured on the human-labelled set** (ADR 0014), never assumed; a weak model will show weak numbers in the report.
* Provider-specific features (native JSON-schema mode, prompt caching) are optional optimisations; correctness never relies on them.

**Consequences:** P4 can be built and fully tested with no external service. Switching providers is configuration. The evaluation report can compare providers on the same test set. Cost figures in the proposal apply only to the optional Anthropic adapter.
