"""Observability layer: structured logs, metrics, traces with correlation IDs.

Shipped: correlation ids (Phase 3), structured logging with mandatory
secret redaction (Phase 5, ADR-013). Metrics and tracing arrive with the
durable task runtime (Phase 6+). All logs must be free of secrets/PII.
"""
