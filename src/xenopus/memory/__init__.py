"""Memory layer: working/episodic/semantic/procedural memory with provenance.

Shipped in Phase 5 (ADR-011): memory types with a 7-status lifecycle,
scope isolation (GLOBAL/USER/WORKSPACE/PROJECT/TASK/TEMPORARY),
evidence-gated promotion (memory-poisoning defense — candidates never
auto-trust), supersession, TTL expiry, and quality scoring; persisted
in a scoped SQLite store.
"""
