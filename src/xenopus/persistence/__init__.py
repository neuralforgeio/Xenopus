"""Persistence layer: durable, schema-versioned local state for Xenopus.

Owns the append-only event journal (SQLite WAL) and, in later phases,
sessions, checkpoints, and task-state tables. Migrations must always be
reversible (master prompt 103).
"""
