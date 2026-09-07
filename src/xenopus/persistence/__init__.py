"""Persistence layer: durable, schema-versioned local state for Xenopus.

Shipped: the append-only event journal (Phase 2) and the session store
(Phase 3, ADR-006). Later phases add checkpoints and task-state tables.
Migrations must always be reversible (master prompt 103).
"""
