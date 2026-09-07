"""Kill switch: emergency stop-all with audit trail.

The kill switch (addendum 118) cancels every live task, persists the
reason, and journals a KILLSWITCH_TRIGGERED audit event. It never
deletes state: durable records remain for post-incident review
(master prompt 15.4 posture — preserve state, escalate cleanly).
"""

from __future__ import annotations

from dataclasses import dataclass

from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskRecord, TaskStore
from xenopus.runtime.events import Event, EventType


@dataclass(frozen=True, slots=True)
class KillSwitchReport:
    """Auditable outcome of one kill-switch trigger."""

    correlation_id: str
    cancelled: tuple[TaskRecord, ...]
    reason: str


class KillSwitch:
    """Global emergency cancel for all live tasks.

    Contract:
        trigger(): cancels every live task via the TaskStore, journals
            KILLSWITCH_TRIGGERED with the reason, and returns the
            report. Idempotent: a second trigger cancels nothing.

    Failure modes: TaskStore errors propagate — the kill switch must
    never swallow a failed cancel (fail loudly on emergency paths).
    """

    def __init__(self, *, store: TaskStore, journal: EventJournal | None = None) -> None:
        self._store = store
        self._journal = journal

    def trigger(self, reason: str = "emergency stop") -> KillSwitchReport:
        """Cancel all live tasks and record the audit trail."""
        correlation_id = new_correlation_id()
        cancelled = self._store.cancel_all(reason=reason)
        if self._journal is not None:
            self._journal.append(
                Event(
                    type=EventType.KILLSWITCH_TRIGGERED,
                    correlation_id=correlation_id,
                    payload={
                        "reason": reason,
                        "cancelled_task_ids": [record.task_id for record in cancelled],
                    },
                )
            )
        return KillSwitchReport(
            correlation_id=correlation_id,
            cancelled=tuple(cancelled),
            reason=reason,
        )
