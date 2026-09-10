"""Durable task store: persisted lifecycle with legal transitions.

Task records survive process restarts (addendum 21): QUEUED, RUNNING,
WAITING, PAUSED, RESUMABLE, COMPLETED, FAILED, CANCELLED. Every
transition passes a legal-transition table (mirroring ADR-003's
approach); illegal moves raise. `cancel_all` implements the kill
switch data path; `mark_interrupted` is the crash-detection entry
point used by recovery.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from xenopus.persistence.journal import EventJournal
from xenopus.runtime.events import Event, EventType


class TaskError(Exception):
    """Raised for task misuse: unknown ids, illegal transitions."""


class TaskState(StrEnum):
    """Durable task states (addendum 21)."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    RESUMABLE = "RESUMABLE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


LEGAL_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.QUEUED: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.RUNNING: frozenset(
        {
            TaskState.PAUSED,
            TaskState.WAITING,
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.RESUMABLE,
        }
    ),
    TaskState.WAITING: frozenset(
        {TaskState.RUNNING, TaskState.PAUSED, TaskState.CANCELLED, TaskState.RESUMABLE}
    ),
    TaskState.PAUSED: frozenset({TaskState.RUNNING, TaskState.RESUMABLE, TaskState.CANCELLED}),
    TaskState.RESUMABLE: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.FAILED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset({TaskState.QUEUED}),  # retry re-queues
    TaskState.CANCELLED: frozenset(),
}

TASKS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal_ref TEXT,
    plan_ref TEXT,
    state TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

TASKS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks (state, updated_at)
"""


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """One durable task row."""

    task_id: str
    title: str
    goal_ref: str | None
    plan_ref: str | None
    state: TaskState
    correlation_id: str
    attempt: int
    max_attempts: int
    created_at: str
    updated_at: str

    @property
    def retries_left(self) -> int:
        """Remaining retry budget."""
        return max(0, self.max_attempts - self.attempt)


class TaskStore:
    """SQLite-backed durable task lifecycle.

    Contract:
        create(): stores a QUEUED task bound to a correlation id.
        transition(): applies the legal-transition table; every state
            change is journaled (TASK_* events) with the correlation id.
        cancel_all(): kill-switch data path — cancels every live task
            (QUEUED/RUNNING/WAITING/PAUSED/RESUMABLE) and returns the
            affected records.
        mark_interrupted(): crash detection — RUNNING/WAITING tasks
            become RESUMABLE with a TASK_INTERRUPTED journal entry.

    Failure modes:
        TaskError for unknown ids, illegal transitions, and empty titles.
    """

    def __init__(
        self, path: Path, *, journal: EventJournal | None = None, cross_thread: bool = False
    ) -> None:
        """Open the task store; ``cross_thread`` relaxes sqlite's thread pin.

        See EventJournal.cross_thread (Phase 11 web-surface contract).
        """
        self._conn = sqlite3.connect(path, check_same_thread=not cross_thread)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(TASKS_TABLE_DDL)
        self._conn.execute(TASKS_INDEX_DDL)
        self._conn.commit()
        self._journal = journal

    # -- lifecycle ---------------------------------------------------------

    def create(
        self,
        title: str,
        *,
        correlation_id: str,
        goal_ref: str | None = None,
        plan_ref: str | None = None,
        max_attempts: int = 3,
    ) -> TaskRecord:
        """Store a new QUEUED task."""
        if not title.strip():
            msg = "task title must be non-empty"
            raise TaskError(msg)
        if max_attempts < 1:
            msg = "max_attempts must be >= 1"
            raise ValueError(msg)
        now = datetime.now(UTC).isoformat()
        task_id = f"task-{uuid4().hex[:12]}"
        with self._conn:
            self._conn.execute(
                "INSERT INTO tasks (task_id, title, goal_ref, plan_ref, state, "
                "correlation_id, attempt, max_attempts, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    task_id,
                    title.strip(),
                    goal_ref,
                    plan_ref,
                    TaskState.QUEUED.value,
                    correlation_id,
                    max_attempts,
                    now,
                    now,
                ),
            )
        record = self.get(task_id)
        self._emit(EventType.TASK_CREATED, correlation_id, {"task_id": task_id})
        return record

    def transition(
        self,
        task_id: str,
        target: TaskState,
        *,
        bump_attempt: bool = False,
    ) -> TaskRecord:
        """Move a task to ``target`` through the legal table."""
        record = self.get(task_id)
        if target not in LEGAL_TASK_TRANSITIONS[record.state]:
            msg = f"illegal task transition: {record.state.value} -> {target.value} for {task_id}"
            raise TaskError(msg)
        now = datetime.now(UTC).isoformat()
        attempt = record.attempt + 1 if bump_attempt else record.attempt
        with self._conn:
            self._conn.execute(
                "UPDATE tasks SET state = ?, attempt = ?, updated_at = ? WHERE task_id = ?",
                (target.value, attempt, now, task_id),
            )
        self._emit(_state_event(target), record.correlation_id, {"task_id": task_id})
        return self.get(task_id)

    def retry(self, task_id: str) -> TaskRecord:
        """Re-queue a FAILED task while retries remain."""
        record = self.get(task_id)
        if record.state is not TaskState.FAILED:
            msg = f"only FAILED tasks can retry; {task_id} is {record.state.value}"
            raise TaskError(msg)
        if record.retries_left < 1:
            msg = f"retry budget exhausted for {task_id}"
            raise TaskError(msg)
        return self.transition(task_id, TaskState.QUEUED, bump_attempt=True)

    def cancel_all(self, *, reason: str = "kill switch") -> list[TaskRecord]:
        """Cancel every live task (kill-switch data path).

        Returns the tasks actually cancelled, in deterministic order.
        """
        live = (
            TaskState.QUEUED,
            TaskState.RUNNING,
            TaskState.WAITING,
            TaskState.PAUSED,
            TaskState.RESUMABLE,
        )
        cancelled: list[TaskRecord] = []
        for record in self.list_tasks():
            if record.state in live:
                cancelled.append(self.transition(record.task_id, TaskState.CANCELLED))
        return cancelled

    def mark_interrupted(self) -> list[TaskRecord]:
        """Crash detection: in-flight tasks become RESUMABLE.

        Called at startup when no graceful shutdown was recorded —
        RUNNING/WAITING tasks are flagged resumable instead of being
        silently restarted or lost.
        """
        interrupted: list[TaskRecord] = []
        for record in self.list_tasks():
            if record.state in (TaskState.RUNNING, TaskState.WAITING):
                interrupted.append(self.transition(record.task_id, TaskState.RESUMABLE))
                self._emit(
                    EventType.TASK_INTERRUPTED,
                    record.correlation_id,
                    {"task_id": record.task_id},
                )
        return interrupted

    # -- queries -----------------------------------------------------------

    def get(self, task_id: str) -> TaskRecord:
        """Fetch one task; TaskError when unknown."""
        row = self._conn.execute(
            "SELECT task_id, title, goal_ref, plan_ref, state, correlation_id, "
            "attempt, max_attempts, created_at, updated_at FROM tasks "
            "WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            msg = f"unknown task: {task_id!r}"
            raise TaskError(msg)
        return _row_to_record(row)

    def list_tasks(
        self,
        *,
        state: TaskState | None = None,
        limit: int = 100,
    ) -> list[TaskRecord]:
        """Tasks newest-first, optionally filtered by state."""
        if limit < 1:
            msg = "limit must be >= 1"
            raise ValueError(msg)
        if state is None:
            rows = self._conn.execute(
                "SELECT task_id, title, goal_ref, plan_ref, state, correlation_id, "
                "attempt, max_attempts, created_at, updated_at FROM tasks "
                "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT task_id, title, goal_ref, plan_ref, state, correlation_id, "
                "attempt, max_attempts, created_at, updated_at FROM tasks "
                "WHERE state = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (state.value, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    # -- internals ---------------------------------------------------------

    def _emit(self, event_type: EventType, correlation_id: str, payload: dict[str, Any]) -> None:
        """Journal a task event when a journal is attached."""
        if self._journal is None:
            return
        self._journal.append(Event(type=event_type, correlation_id=correlation_id, payload=payload))


def _state_event(state: TaskState) -> EventType:
    """Map a task state to its journal event type."""
    mapping: dict[TaskState, EventType] = {
        TaskState.QUEUED: EventType.TASK_CREATED,
        TaskState.RUNNING: EventType.TASK_STARTED,
        TaskState.WAITING: EventType.TASK_WAITING,
        TaskState.PAUSED: EventType.TASK_PAUSED,
        TaskState.RESUMABLE: EventType.TASK_RESUMED,  # resumable marker
        TaskState.COMPLETED: EventType.TASK_COMPLETED,
        TaskState.FAILED: EventType.TASK_FAILED,
        TaskState.CANCELLED: EventType.TASK_CANCELLED,
    }
    return mapping[state]


def _row_to_record(
    row: tuple[str, str, str | None, str | None, str, str, int, int, str, str],
) -> TaskRecord:
    """Map a tasks-table row to a TaskRecord."""
    return TaskRecord(
        task_id=row[0],
        title=row[1],
        goal_ref=row[2],
        plan_ref=row[3],
        state=TaskState(row[4]),
        correlation_id=row[5],
        attempt=row[6],
        max_attempts=row[7],
        created_at=row[8],
        updated_at=row[9],
    )
