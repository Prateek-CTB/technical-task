# 0006 Provider-independent analyzer

**Status:** Superseded by [ADR 0018](0018-ai-provider-strategy.md) (no paid API key is available; the interface stays, the provider strategy changed).

**Decision:** `ReviewAnalyzer` is a protocol. The default adapter uses the official Anthropic SDK with structured outputs. Provider, model and effort come from settings. API keys come from the environment only and are never logged or committed. No paid call runs without explicit approval.

**Consequences:** Models can be compared fairly on the same evaluation set. The interface is the lowest common denominator across providers.
