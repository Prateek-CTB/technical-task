# Architecture Decision Records

Format: Status · Context · Decision · Consequences. An accepted ADR is not edited; a change is a new ADR that supersedes it.

| # | Decision | Status |
|---|---|---|
| [0001](0001-modular-monolith.md) | Modular monolith | Accepted |
| [0002](0002-no-access-control-circumvention.md) | No circumvention of access controls; owner-side sources first | Accepted (the data source itself is open, D1) |
| [0003](0003-source-adapters.md) | Source adapters and one validation pipeline | Accepted |
| [0004](0004-database-and-migrations.md) | SQLAlchemy + Alembic; PostgreSQL and SQLite, both tested | Accepted |
| [0005](0005-hybrid-absa.md) | Hybrid aspect-based sentiment analysis | Accepted |
| [0006](0006-provider-independent-analyzer.md) | Provider-independent analyzer interface | Superseded by 0018 |
| [0007](0007-analytics-on-demand.md) | Analytics computed on demand | Accepted |
| [0008](0008-scheduling.md) | CLI + OS scheduler, no job queue | Accepted |
| [0009](0009-data-minimisation.md) | Data minimisation | Accepted |
| [0010](0010-openapi-types.md) | OpenAPI-generated TypeScript types | Accepted |
| [0011](0011-analysis-idempotency.md) | Analysis idempotency key has no nullable columns | Accepted |
| [0012](0012-concurrency-scope.md) | Single-flight scope per run kind | Accepted |
| [0013](0013-synthetic-demo-data.md) | Deterministic, labelled synthetic demo data | Accepted |
| [0014](0014-evaluation-integrity.md) | Held-out test set is never tuned against | Accepted |
| [0015](0015-delivery-sequencing.md) | Vertical slice before frontend; estimates are not deadlines | Accepted |
| [0016](0016-migration-policy.md) | Migration policy and structural schema verification | Accepted |
| [0017](0017-dataset-isolation.md) | Production / demo / experimental data isolation | Accepted |
| [0018](0018-ai-provider-strategy.md) | AI providers: mock for tests, OpenAI-compatible (free/local) for inference, cloud optional | Accepted (provider choice open, D4/D7) |
| [0019](0019-run-liveness-and-recovery.md) | Run heartbeat and stale-run recovery | Accepted |
| [0020](0020-property-timezones.md) | Local dates derive from each property's timezone | Accepted |
| [0021](0021-openrouter-only.md) | OpenRouter free models only (real) + mock (tests) | Accepted |
| [0022](0022-collection-architecture.md) | Collection: headless-first, honest about refusal, authorised fallbacks | Accepted |
| [0023](0023-rule-based-analyzer.md) | Offline rule-based analyzer; precedence LLM > rule-based > mock | Accepted |
| [0024](0024-http-structured-strategy.md) | HTTP + structured-data strategy; AWS WAF challenge detection; published score from JSON-LD | Accepted |

## Open decisions (proposal §12)

| # | Question | Working default until answered |
|---|---|---|
| D1 | Real data source (Extranet / channel manager) or synthetic demo | 973 real reviews collected from the public pages with a visible browser; CSV/JSON import, inbox and feed are ready for an owner export |
| D2 | Keep the quarantined experimental dataset? | Not loaded anywhere; stays outside the repository |
| D4 | Free model for real inference | **Closed**: OpenRouter `baidu/ernie-5.5-flash-preview:free` (configurable), ADR 0021 |
| D7 | May real guest-review text be sent to the hosted AI provider? | Off by default. Operator authorised one capped run on 2026-11-26 (ADR 0023) |
| D5 | "Negative review" = guest rating ≤ 6 | Used for KPIs; defined in one place |
| D6 | Who labels the evaluation set | Blocks P7 only |

D3 (database default) is closed by ADR 0004.
