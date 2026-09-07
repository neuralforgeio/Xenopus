"""Checkpoint manager: SQLite snapshots for rewind and recovery.

Checkpoints capture runtime state references (master prompt 55):
fsm state, active task/plan ids, tool journal anchor, and memory/skill
counts at snapshot time. Checkpoints are append-only rows with sequence
numbers; rewind semantics (fork-from-checkpoint) arrive with the durable
task runtime (Phase 6) — this manager establishes the durable substrate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHECKPOINTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    label TEXT NOT NULL,
    fsm_state TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

CHECKPOINTS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_checkpoints_correlation
    ON checkpoints (correlation_id, created_at)
"""


class CheckpointError(Exception):
    """Raised for checkpoint misuse: unknown ids, invalid snapshots."""


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """One durable snapshot row.

    Contract:
        snapshot: JSON object of state references (fsm_state, task ids,
        journal anchor, counters). Must round-trip through json exactly.
    """

    checkpoint_id: str
    correlation_id: str
    label: str
    fsm_state: str
    snapshot: dict[str, Any]
    created_at: str


class CheckpointManager:
    """Append-only checkpoint store per correlation id.

    Contract:
        create(): stores one snapshot (JSON-validated).
        latest_for(): newest checkpoint of a trace.
        list_for(): chronological checkpoints of a trace.

    Failure modes:
        CheckpointError when the snapshot is not JSON-serializable or a
        checkpoint id is unknown.
    """

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(CHECKPOINTS_TABLE_DDL)
        self._conn.execute(CHECKPOINTS_INDEX_DDL)
        self._conn.commit()

    def create(
        self,
        *,
        correlation_id: str,
        label: str,
        fsm_state: str,
        snapshot: dict[str, Any],
    ) -> Checkpoint:
        """Persist one snapshot; returns the stored row."""
        if not correlation_id.strip():
            msg = "correlation_id must be non-empty"
            raise CheckpointError(msg)
        if not label.strip():
            msg = "checkpoint label must be non-empty"
            raise CheckpointError(msg)
        try:
            payload = json.dumps(snapshot)
        except (TypeError, ValueError) as err:
            msg = f"checkpoint snapshot is not JSON-serializable: {err}"
            raise CheckpointError(msg) from err
        from uuid import uuid4

        from xenopus.persistence.journal import EventJournal  # noqa: F401 — doc anchor

        checkpoint_id = f"ckpt-{uuid4().hex[:14]}"
        from datetime import UTC, datetime

        created_at = datetime.now(UTC).isoformat()
        with self._conn:
            self._conn.execute(
                "INSERT INTO checkpoints (checkpoint_id, correlation_id, label, "
                "fsm_state, snapshot, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    checkpoint_id,
                    correlation_id,
                    label,
                    fsm_state,
                    payload,
                    created_at,
                ),
            )
        return self.get(checkpoint_id)

    def get(self, checkpoint_id: str) -> Checkpoint:
        """Fetch one checkpoint; CheckpointError when unknown."""
        row = self._conn.execute(
            "SELECT checkpoint_id, correlation_id, label, fsm_state, snapshot, "
            "created_at FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            msg = f"unknown checkpoint: {checkpoint_id!r}"
            raise CheckpointError(msg)
        return Checkpoint(
            checkpoint_id=row[0],
            correlation_id=row[1],
            label=row[2],
            fsm_state=row[3],
            snapshot=json.loads(row[4]),
            created_at=row[5],
        )

    def latest_for(self, correlation_id: str) -> Checkpoint | None:
        """Newest checkpoint for a trace, or None."""
        row = self._conn.execute(
            "SELECT checkpoint_id FROM checkpoints WHERE correlation_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (correlation_id,),
        ).fetchone()
        return self.get(row[0]) if row else None

    def list_for(self, correlation_id: str) -> list[Checkpoint]:
        """All checkpoints of a trace, chronological."""
        rows = self._conn.execute(
            "SELECT checkpoint_id FROM checkpoints WHERE correlation_id = ? "
            "ORDER BY created_at, rowid",
            (correlation_id,),
        ).fetchall()
        return [self.get(row[0]) for row in rows]

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
