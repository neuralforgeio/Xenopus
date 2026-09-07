"""Skills layer: skill registry + 7-stage lifecycle, with no auto-trust.

Shipped in Phase 5 (ADR-012): skill schemas (trigger, procedure,
constraints, failure modes, success criteria), the 7-stage lifecycle
gates (CANDIDATE -> STAGED -> EVALUATED -> EXPERIMENTAL -> TRUSTED, with
DEGRADED/SUPERSEDED), deterministic promotion thresholds (>=3 evals at
>=0.75 success for trust; <=0.40 triggers regression), evaluation
metrics, and a SQLite registry (skill-poisoning defense — no stage
reaches TRUSTED without evidence).
"""
