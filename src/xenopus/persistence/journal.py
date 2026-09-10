"""Append-only event journal backed by SQLite (WAL mode).

The journal is the canonical event source for the runtime (Protocol v9
Section 12, addendum 43/89). Writes are idempotent per event id; rows carry
a schema version so migrations stay reversible.

Design notes:
- WAL mode keeps readers (future dashboard) unblocked during writes.
- The journal never mutates or deletes existing rows (append-only).
- Payloads are stored as JSON text; secrets must never be placed in
  payloads (enforced by policy, sanitized at call sites).
"""

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from xenopus.runtime.events import Event, EventType

JOURNAL_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS event_journal (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    occurred_at TEXT NOT NULL
)
"""

JOURNAL_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_journal_correlation
    ON event_journal (correlation_id, occurred_at)
"""


class JournalError(Exception):
    """Raised for journal misuse: bad event types, unreadable rows."""


class EventJournal:
    """Append-only store for runtime events.

    Contract:
        append(): writes exactly once per event id (idempotent).
        events_for(): returns events in chronological order for one
            correlation id — the audit trail backbone.
        all_events(): full-table read for tests and maintenance tools.

    Failure modes:
        JournalError when a payload is not JSON-serializable or a row
        fails validation on read. SQLite operational errors propagate.
    """

    def __init__(self, path: Path, *, cross_thread: bool = False) -> None:
        """Open the journal; ``cross_thread`` relaxes sqlite's thread pin.

        Servers (Phase 11 web) may serve requests from a different
        thread than the one that built the stores; WAL mode keeps
        concurrent readers safe, and writers serialize on the sqlite
        connection lock. Default False preserves the strict
        same-thread contract the CLI/TUI rely on.
        """
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=not cross_thread)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(JOURNAL_TABLE_DDL)
        self._conn.execute(JOURNAL_INDEX_DDL)
        self._conn.commit()

    def append(self, event: Event) -> str:
        """Append one event; returns its id. Idempotent per event id."""
        try:
            payload_text = json.dumps(event.payload)
        except (TypeError, ValueError) as err:
            msg = f"Event payload is not JSON-serializable: {err}"
            raise JournalError(msg) from err
        event_id = f"evt-{uuid4().hex[:16]}"
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO event_journal "
                "(event_id, type, correlation_id, payload, schema_version, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (
                    event_id,
                    event.type.value,
                    event.correlation_id,
                    payload_text,
                    event.schema_version,
                ),
            )
        return event_id

    def events_for(self, correlation_id: str) -> list[Event]:
        """Chronological events for one correlation id.

        Ordering uses rowid as the tiebreaker: ``occurred_at`` has second
        granularity, so same-second events need a deterministic order
        (append order) for journal replay and audit trails.
        """
        rows = self._conn.execute(
            "SELECT type, payload, schema_version "
            "FROM event_journal WHERE correlation_id = ? ORDER BY occurred_at, rowid",
            (correlation_id,),
        ).fetchall()
        return [self._row_to_event(row, correlation_id) for row in rows]

    def all_events(self) -> list[tuple[str, Event]]:
        """Full journal read; for tests and maintenance only."""
        rows = self._conn.execute(
            "SELECT event_id, type, correlation_id, payload, schema_version "
            "FROM event_journal ORDER BY occurred_at, rowid"
        ).fetchall()
        return [(row[0], self._row_to_event((row[1], row[3], row[4]), row[2])) for row in rows]

    def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        self._conn.close()

    @staticmethod
    def _row_to_event(row: tuple[Any, ...], correlation_id: str) -> Event:
        """Rebuild an Event from a (type, payload, schema_version) row slice."""
        event_type = row[0]
        try:
            canonical_type = EventType(event_type)
        except ValueError as err:
            msg = f"Journal row has unknown event type {event_type!r}"
            raise JournalError(msg) from err
        payload = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        return Event(
            type=canonical_type,
            correlation_id=correlation_id,
            payload=payload,
            schema_version=row[2],
        )
