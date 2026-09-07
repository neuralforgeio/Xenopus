"""Provider layer: model provider abstraction (OpenAI-compatible, etc.) + router.

Shipped in Phase 3: core types, the ModelProvider protocol, the offline
EchoProvider (local-first default), an OpenAI-compatible HTTP adapter
with mandatory timeouts and schema validation, a capability registry, a
circuit-breaker health tracker, and a deterministic policy router
(ADR-005). The core stays provider-agnostic; no provider-specific import
ever enters the core runtime modules.
"""
