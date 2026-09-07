"""Crash recovery: detect interrupted tasks on startup.

If the process died without a graceful shutdown, in-flight tasks are
left RUNNING/WAITING. The recovery pass (master prompt 127) flags them
RESUMABLE (never silently restarts), so the operator decides per task
via the remote-control surface.
"""

from __future__ import annotations

from dataclasses import dataclass

from xenopus.persistence.tasks import TaskRecord, TaskStore


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Outcome of one startup recovery pass."""

    interrupted: tuple[TaskRecord, ...]

    @property
    def has_work(self) -> bool:
        """True when operator decision is required."""
        return bool(self.interrupted)


class CrashRecovery:
    """Startup recovery over the durable task store.

    Contract:
        run(): flags RUNNING/WAITING tasks as RESUMABLE (idempotent —
        a clean restart finds nothing to flag). Recovery never restarts
        tasks by itself: it surfaces, the operator resumes.

    Failure modes: TaskStore errors propagate (recovery must not hide
    persistence corruption behind a green report).
    """

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    def run(self) -> RecoveryReport:
        """Flag in-flight tasks resumable; return the report."""
        interrupted = self._store.mark_interrupted()
        return RecoveryReport(interrupted=tuple(interrupted))
