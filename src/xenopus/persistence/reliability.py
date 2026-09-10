"""Reliability records: empirical tool/agent performance (addendum 88, 107).

Every tool invocation and agent run records success/failure/latency.
Selection reads the aggregates; nothing is inferred. This is the
evidence base Phase 12's learning consumes — promotion stays gated.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

RELIABILITY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS reliability_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    success INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    recorded_at TEXT NOT NULL
)
"""

RELIABILITY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_reliability_subject
    ON reliability_events (kind, subject)
"""


@dataclass(frozen=True, slots=True)
class ReliabilityStats:
    """Aggregated reliability for one kind+subject (e.g. tool/file_read)."""

    kind: str
    subject: str
    invocations: int
    successes: int
    failures: int
    success_rate: float
    avg_duration_seconds: float

    @property
    def rank(self) -> float:
        """Deterministic selection score: success rate minus slowness.

        Higher is better; a 0.99-rate tool at 10s loses to a 0.95-rate
        tool at 0.5s. Range roughly [0, 1]; used for ORDER BY only.
        """
        return self.success_rate - min(0.5, self.avg_duration_seconds * 0.05)


class ReliabilityStore:
    """SQLite-backed invocation statistics.

    Contract:
        record(): appends one observation (idempotent at the caller —
            each real invocation records exactly once).
        stats_for(): aggregates for one subject.
        best_for(): ranks candidates for a kind deterministically.

    Invariants:
        success_rate in [0,1]; avg over all recorded durations;
        rankings are pure functions of recorded data.
    """

    def __init__(self, path: str | None = None) -> None:
        self._conn = sqlite3.connect(path or ":memory:")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(RELIABILITY_TABLE_DDL)
        self._conn.execute(RELIABILITY_INDEX_DDL)
        self._conn.commit()

    def record(
        self,
        *,
        kind: str,
        subject: str,
        success: bool,
        duration_seconds: float,
    ) -> None:
        """Append one observation."""
        if duration_seconds < 0:
            msg = "duration_seconds must be >= 0"
            raise ValueError(msg)
        if not subject.strip():
            msg = "subject must be non-empty"
            raise ValueError(msg)
        from datetime import UTC, datetime

        with self._conn:
            self._conn.execute(
                "INSERT INTO reliability_events "
                "(kind, subject, success, duration_seconds, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    kind,
                    subject,
                    int(success),
                    duration_seconds,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def stats_for(self, kind: str, subject: str) -> ReliabilityStats:
        """Aggregates for one subject; zero-invocation default."""
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(success), 0), "
            "       COALESCE(AVG(duration_seconds), 0.0) "
            "FROM reliability_events WHERE kind = ? AND subject = ?",
            (kind, subject),
        ).fetchone()
        invocations, successes, avg = row[0], row[1], float(row[2])
        return ReliabilityStats(
            kind=kind,
            subject=subject,
            invocations=invocations,
            successes=successes,
            failures=invocations - successes,
            success_rate=(successes / invocations) if invocations else 0.0,
            avg_duration_seconds=avg,
        )

    def subjects_for(self, kind: str) -> list[str]:
        """Distinct recorded subjects of one kind, name-sorted.

        Read-only query surface for consolidation loops (ADR-018:
        selection reads the aggregates; nothing is inferred).
        """
        return [
            row[0]
            for row in self._conn.execute(
                "SELECT DISTINCT subject FROM reliability_events WHERE kind = ? ORDER BY subject",
                (kind,),
            ).fetchall()
        ]

    def best_for(self, kind: str) -> ReliabilityStats | None:
        """Top-ranked subject for a kind (ties: subject name asc)."""
        subjects = [
            row[0]
            for row in self._conn.execute(
                "SELECT DISTINCT subject FROM reliability_events WHERE kind = ?",
                (kind,),
            ).fetchall()
        ]
        if not subjects:
            return None
        ranked = sorted(
            (self.stats_for(kind, subject) for subject in subjects),
            key=lambda stats: (-stats.rank, stats.subject),
        )
        return ranked[0]

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()
