"""Tools layer: the Tool Gateway — discovery, permission, risk, execution.

Shipped in Phase 4 (ADR-008/009): tool contracts (schema, side effects,
permissions, risk, timeout, idempotency, failure modes), a lazy-search
registry, the enforcement gateway (permission -> risk -> approval ->
execute -> observe), and the built-in file tools with workspace path
boundaries. Every tool invocation passes through the gateway — nothing
calls handlers directly.
"""
