"""View-model rows for the TUI panels.

Pure data shaping: each function turns engine records into the row
tuples the DataTables display. No Textual imports, no policy — the
engines own semantics, the TUI owns presentation only (ADR-021).
"""

from __future__ import annotations

from xenopus.persistence.approvals_store import ApprovalRow
from xenopus.persistence.tasks import TaskRecord, TaskState
from xenopus.runtime.agent import AgentHealth
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.scheduler import ScheduleEntry

TASK_COLUMNS = ("task_id", "state", "attempt", "title")
APPROVAL_COLUMNS = ("request_id", "tool", "subject", "expires_at")
SCHEDULE_COLUMNS = ("schedule_id", "kind", "name", "enabled")
AGENT_COLUMNS = ("run_id", "health", "task_id", "heartbeat")


def state_color(state: TaskState) -> str:
    """Presentation color for a task state (matches the CLI palette)."""
    return {
        TaskState.COMPLETED: "green",
        TaskState.FAILED: "red",
        TaskState.RUNNING: "cyan",
        TaskState.CANCELLED: "yellow",
    }.get(state, "white")


def task_row(record: TaskRecord) -> tuple[str, str, str, str]:
    """One task-list row: id, state, attempt budget, title."""
    return (
        record.task_id,
        record.state.value,
        f"{record.attempt}/{record.max_attempts}",
        record.title,
    )


def approval_row(row: ApprovalRow) -> tuple[str, str, str, str]:
    """One approvals row: id, tool, subject, expiry."""
    return (row.request_id, row.tool, row.subject, row.expires_at)


def schedule_row(entry: ScheduleEntry) -> tuple[str, str, str, str]:
    """One schedules row: id, kind, name, enabled."""
    return (entry.schedule_id, entry.kind.value, entry.name, str(entry.enabled))


def agent_rows(pool: AgentPool) -> list[tuple[str, str, str, str]]:
    """Agent health rows from the pool's live runs (watchdog view).

    Uses the pool's read-only ``live_handles`` snapshot (the same data
    the watchdog reads) plus per-run ``health_of``; rows list in
    run_id order for determinism.
    """
    return [
        (
            handle.run_id,
            (pool.health_of(handle.run_id) or AgentHealth.HEALTHY).value,
            handle.task_id,
            handle.last_heartbeat,
        )
        for handle in pool.live_handles
    ]
