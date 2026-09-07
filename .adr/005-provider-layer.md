# ADR-005: Provider Protocol, httpx, and Deterministic Routing

## Status
Accepted

## Context
Phase 3 introduces the model provider layer. The runtime must stay
provider-agnostic (master prompt 61), needs async HTTP with mandatory
timeouts (18.4), and routing must be deterministic (63). This is also the
first runtime dependency in the project — it must pass the Section 9
dependency governance matrix (Protocol v9).

## Decision
1. `ModelProvider` is a typing.Protocol (structural): providers register
   against the protocol; the runtime imports `xenopus.provider.protocol`
   only — never a provider module.
2. **First runtime dependency: httpx 0.28.1 (BSD-3-Clause)** — governed
   approval: maintained, permissive license, minimal transitive tree
   (anyio, httpcore, certifi, idna, sniffio, h11 — all permissive),
   mandatory `httpx.Timeout` on every request, cancellation-safe async.
   Rejected alternatives: `requests` (blocking), raw `aiohttp` (larger
   tree), stdlib `urllib` (no timeouts ergonomics, no async).
3. `OpenAICompatProvider` covers every OpenAI-compatible endpoint
   (OpenAI, OpenRouter, z.ai, LM Studio, llama.cpp, Ollama compat, vLLM).
   API keys are constructor-injected ONLY — the module never reads
   `os.environ` (secret discipline, master prompt 101). 401/403 ->
   AuthError (permanent), 429 -> RateLimitError (honors Retry-After),
   5xx/network -> ProviderUnavailableError, 200-with-junk -> SchemaError
   (permanent): a valid status code is not success.
4. `EchoProvider` is a first-class offline provider (local-first default):
   deterministic, zero-cost, network-free — the runtime works out of the
   box with no keys.
5. `ProviderRegistry` is an explicit catalog (no ambient discovery);
   `ProviderHealthTracker` is a deterministic circuit breaker with an
   injected monotonic clock (no real sleeps in tests); `ModelRouter`
   returns a health-filtered, policy-ordered chain (quality/cost/latency/
   local/privacy-first). Same inputs -> same order, always.
6. All provider HTTP tests use `httpx.MockTransport`: the test suite
   never touches the network.

## Reversal Criteria
If a provider requires a non-OpenAI wire protocol, add a new adapter
implementing the same protocol — never bend the protocol. If httpx
blocks a needed transport feature (HTTP/3, connection pooling limits),
re-evaluate with evidence.

## Sunset Review
Phase 5 (learning) when empirical model-performance data can refine
routing; Phase 18 (performance benchmarks).

## Consequences
### Positive
- Runtime works offline end-to-end via EchoProvider.
- One adapter serves many endpoints; keys never leak into logs by design.
- Deterministic routing is testable without flakes.
### Negative
- First runtime dependency added (accepted, governed).
- Protocol stability discipline required from Phase 3 onward.
### Neutral
- Tool-calling arrives in Phase 4+; capability flags already declare it.

## Alternatives Considered
- Provider-specific SDKs (openai, anthropic) — rejected: N large trees,
  version churn, and credential handling varies per SDK.
- stdlib urllib — rejected: no async, timeout handling is error-prone.

## References
- Master prompt 61-65, 80; Protocol v9 Section 9, 18.4.
