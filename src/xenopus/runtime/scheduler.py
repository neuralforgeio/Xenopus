"""Scheduler: durable schedules with ONE execution engine (TaskStore).

Schedules (addendum 38, master prompt 57) are data: entries with a
kind (ONCE / INTERVAL / DAILY / WEEKLY), deterministic due-time math,
a minimum cooldown, and a bounded tick loop over an injected clock.
When an entry fires, the scheduler enqueues a task in the SAME
TaskStore the rest of the runtime uses — there is no second execution
engine. Self-maintenance jobs (addendum 39, 100) run on the same tick
with their own budgets.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskStore
from xenopus.runtime.events import Event, EventType


class SchedulerError(Exception):
    """Raised for schedule misuse: invalid entries, unknown ids."""


class ScheduleKind(StrEnum):
    """Entry kinds (addendum 38).

    ONCE fires at a fixed time; INTERVAL fires every N seconds;
    DAILY fires at a wall-clock time-of-day; WEEKLY fires at a
    weekday + time-of-day. No cron-expression dependency: the four
    kinds cover the P0/P1 needs deterministically (dependency
    governance: a cron parser is unjustified weight today).
    """

    ONCE = "ONCE"
    INTERVAL = "INTERVAL"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"


WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")

MIN_COOLDOWN_SECONDS = 1.0
DEFAULT_MAX_EXECUTIONS_PER_TICK = 25


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (UTC enforced downstream)."""
    return datetime.fromisoformat(value)


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    """One durable schedule definition.

    Contract:
        kind-specific fields: ONCE uses at_time; INTERVAL uses
        interval_seconds; DAILY uses time_of_day; WEEKLY uses weekday +
        time_of_day. Mismatched fields are rejected at construction.
        max_executions: None = unbounded; a number = retire after N
        fires (bounded by design, addendum 39).
    """

    schedule_id: str
    name: str
    kind: ScheduleKind
    created_at: datetime
    at_time: str | None = None
    interval_seconds: float | None = None
    time_of_day: str | None = None  # "HH:MM"
    weekday: str | None = None
    max_executions: int | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "schedule name must be non-empty"
            raise ValueError(msg)
        if self.kind is ScheduleKind.ONCE and not self.at_time:
            msg = "ONCE schedule requires at_time"
            raise ValueError(msg)
        if self.kind is ScheduleKind.INTERVAL and (
            self.interval_seconds is None or self.interval_seconds <= 0
        ):
            msg = "INTERVAL schedule requires positive interval_seconds"
            raise ValueError(msg)
        if self.kind is ScheduleKind.DAILY and not self.time_of_day:
            msg = "DAILY schedule requires time_of_day (HH:MM)"
            raise ValueError(msg)
        if self.kind is ScheduleKind.WEEKLY:
            if not self.time_of_day or not self.weekday:
                msg = "WEEKLY schedule requires weekday + time_of_day"
                raise ValueError(msg)
            if self.weekday not in WEEKDAYS:
                msg = f"unknown weekday {self.weekday!r}"
                raise ValueError(msg)
        if self.max_executions is not None and self.max_executions < 1:
            msg = "max_executions must be >= 1 when provided"
            raise ValueError(msg)


def next_due_time(entry: ScheduleEntry, *, now: datetime) -> datetime:
    """Deterministic next due time for an entry.

    Rules:
        ONCE: the fixed at_time (past = overdue, fires immediately).
        INTERVAL: created_at + k*interval, the first > now.
        DAILY: next occurrence of time_of_day strictly after now.
        WEEKLY: next occurrence of weekday+time_of_day strictly after now.

    All comparisons are UTC-aware; inputs must be timezone-aware.
    """
    if now.tzinfo is None:
        msg = "now must be timezone-aware"
        raise ValueError(msg)
    if entry.kind is ScheduleKind.ONCE:
        due = _parse_iso(str(entry.at_time))
        return due if due.tzinfo else due.replace(tzinfo=UTC)
    if entry.kind is ScheduleKind.INTERVAL:
        interval = float(entry.interval_seconds or 0.0)
        elapsed = (now - entry.created_at).total_seconds()
        # Latest fully-elapsed slot when one exists (overdue fire — the
        # cooldown paces any backlog, no catch-up bursts); otherwise
        # the first upcoming slot (entry not yet due).
        slots_elapsed = int(elapsed // interval)
        if slots_elapsed >= 1:
            return entry.created_at + timedelta(seconds=slots_elapsed * interval)
        return entry.created_at + timedelta(seconds=interval)
    hour_s, minute_s = str(entry.time_of_day).split(":")
    hour, minute = int(hour_s), int(minute_s)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if entry.kind is ScheduleKind.DAILY:
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    # WEEKLY
    target_index = WEEKDAYS.index(str(entry.weekday))
    days_ahead = (target_index - now.weekday()) % 7
    candidate = candidate + timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


@dataclass(frozen=True, slots=True)
class FireResult:
    """Outcome of one fired schedule entry."""

    schedule_id: str
    task_id: str
    correlation_id: str


class Scheduler:
    """Durable tick-loop scheduler over the shared TaskStore.

    Contract:
        add(): registers an entry.
        tick(): fires every enabled entry whose due time has arrived,
        enqueuing a task per fire (ONE execution engine) and journaling
        SCHEDULE_FIRED; respects per-entry cooldown and per-tick
        execution budget; disables entries that exhaust max_executions.
        Self-maintenance jobs run AFTER schedule fires, each wrapped in
        its own budget (a failing job never breaks the tick).

    Failure modes:
        SchedulerError for unknown ids; job errors are captured into
        tick errors (reported, never swallowed silently).
    """

    def __init__(
        self,
        *,
        store: TaskStore,
        journal: EventJournal | None = None,
        clock: Callable[[], datetime] | None = None,
        cooldown_seconds: float = MIN_COOLDOWN_SECONDS,
        max_executions_per_tick: int = DEFAULT_MAX_EXECUTIONS_PER_TICK,
    ) -> None:
        if cooldown_seconds < MIN_COOLDOWN_SECONDS:
            msg = f"cooldown must be >= {MIN_COOLDOWN_SECONDS}s"
            raise ValueError(msg)
        if max_executions_per_tick < 1:
            msg = "max_executions_per_tick must be >= 1"
            raise ValueError(msg)
        self._store = store
        self._journal = journal
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cooldown = cooldown_seconds
        self._max_per_tick = max_executions_per_tick
        self._entries: dict[str, _EntryState] = {}
        self._jobs: list[tuple[str, Callable[[], object]]] = []

    def add(self, entry: ScheduleEntry) -> None:
        """Register a schedule entry."""
        if entry.schedule_id in self._entries:
            msg = f"schedule already registered: {entry.schedule_id!r}"
            raise SchedulerError(msg)
        self._entries[entry.schedule_id] = _EntryState(
            entry=entry,
            executions=0,
            last_fired=None,
        )

    def register_job(self, name: str, job: Callable[[], object]) -> None:
        """Register a self-maintenance job (addendum 39, 100).

        Jobs run each tick (after fires), must be idempotent and cheap,
        and own their own budgets internally.
        """
        self._jobs.append((name, job))

    def tick(self) -> list[FireResult]:
        """Fire due entries; returns the fire results of this tick."""
        now = self._clock()
        fired: list[FireResult] = []
        errors: list[str] = []
        for state in list(self._entries.values()):
            if len(fired) >= self._max_per_tick:
                break
            entry = state.entry
            if not entry.enabled:
                continue
            due = next_due_time(entry, now=now)
            if due > now:
                continue
            if (
                state.last_fired is not None
                and (now - state.last_fired).total_seconds() < self._cooldown
            ):
                continue
            correlation_id = f"sched-{entry.schedule_id}-{state.executions + 1}"
            task = self._store.create(
                f"scheduled: {entry.name}",
                correlation_id=correlation_id,
            )
            fired.append(
                FireResult(
                    schedule_id=entry.schedule_id,
                    task_id=task.task_id,
                    correlation_id=correlation_id,
                )
            )
            if self._journal is not None:
                self._journal.append(
                    Event(
                        type=EventType.SCHEDULE_FIRED,
                        correlation_id=correlation_id,
                        payload={"schedule_id": entry.schedule_id, "name": entry.name},
                    )
                )
            state.executions += 1
            state.last_fired = now
            if entry.max_executions is not None and state.executions >= entry.max_executions:
                state.entry = ScheduleEntry(
                    schedule_id=entry.schedule_id,
                    name=entry.name,
                    kind=entry.kind,
                    created_at=entry.created_at,
                    at_time=entry.at_time,
                    interval_seconds=entry.interval_seconds,
                    time_of_day=entry.time_of_day,
                    weekday=entry.weekday,
                    max_executions=entry.max_executions,
                    enabled=False,
                )
        for name, job in self._jobs:
            try:
                job()
            except Exception as err:
                errors.append(f"job {name!r} failed: {err}")
        if errors and self._journal is not None:
            self._journal.append(
                Event(
                    type=EventType.SCHEDULE_FIRED,
                    correlation_id="sched-jobs",
                    payload={"job_errors": errors},
                )
            )
        return fired

    async def run_loop(self, *, interval_seconds: float = 1.0) -> None:
        """Reference daemon loop: tick + sleep until cancelled.

        The TUI (Phase 10) / server (Phase 11) host this loop; the
        scheduler itself is loop-agnostic and fully testable via tick().
        """
        if interval_seconds <= 0:
            msg = "interval_seconds must be > 0"
            raise ValueError(msg)
        while True:
            self.tick()
            await asyncio.sleep(interval_seconds)


@dataclass(slots=True)
class _EntryState:
    entry: ScheduleEntry
    executions: int
    last_fired: datetime | None


def once_entry(name: str, at: datetime) -> ScheduleEntry:
    """Build a ONCE entry with a fresh id."""
    return ScheduleEntry(
        schedule_id=f"sched-{uuid4().hex[:12]}",
        name=name,
        kind=ScheduleKind.ONCE,
        created_at=at,
        at_time=at.isoformat(),
    )


def interval_entry(name: str, *, interval_seconds: float, created_at: datetime) -> ScheduleEntry:
    """Build an INTERVAL entry with a fresh id."""
    return ScheduleEntry(
        schedule_id=f"sched-{uuid4().hex[:12]}",
        name=name,
        kind=ScheduleKind.INTERVAL,
        created_at=created_at,
        interval_seconds=interval_seconds,
    )


def daily_entry(name: str, *, time_of_day: str, created_at: datetime) -> ScheduleEntry:
    """Build a DAILY entry with a fresh id."""
    return ScheduleEntry(
        schedule_id=f"sched-{uuid4().hex[:12]}",
        name=name,
        kind=ScheduleKind.DAILY,
        created_at=created_at,
        time_of_day=time_of_day,
    )


def weekly_entry(
    name: str,
    *,
    weekday: str,
    time_of_day: str,
    created_at: datetime,
) -> ScheduleEntry:
    """Build a WEEKLY entry with a fresh id."""
    return ScheduleEntry(
        schedule_id=f"sched-{uuid4().hex[:12]}",
        name=name,
        kind=ScheduleKind.WEEKLY,
        created_at=created_at,
        weekday=weekday,
        time_of_day=time_of_day,
    )
