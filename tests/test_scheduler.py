"""Scheduler tests: due math, cooldown, budgets, jobs, TaskStore engine."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pytest

from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskStore
from xenopus.runtime.scheduler import (
    FireResult,
    ScheduleEntry,
    ScheduleKind,
    Scheduler,
    SchedulerError,
    daily_entry,
    interval_entry,
    next_due_time,
    once_entry,
    weekly_entry,
)


class _Clock(TypedDict):
    now: datetime


class _Stack(TypedDict):
    scheduler: Scheduler
    store: TaskStore
    journal: EventJournal
    clock: _Clock


@pytest.fixture
def stack(tmp_path: Path) -> Generator[_Stack]:
    journal = EventJournal(tmp_path / "j.sqlite")
    store = TaskStore(tmp_path / "t.sqlite", journal=journal)
    clock: _Clock = {"now": datetime(2026, 9, 8, 12, 0, tzinfo=UTC)}
    scheduler = Scheduler(store=store, journal=journal, clock=lambda: clock["now"])
    yield {"scheduler": scheduler, "store": store, "journal": journal, "clock": clock}
    store.close()
    journal.close()


class TestDueTimeMath:
    def test_once_past_time_is_overdue(self) -> None:
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        entry = once_entry("past", at=now - timedelta(hours=1))
        assert next_due_time(entry, now=now) <= now

    def test_once_future_time_waits(self) -> None:
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        entry = once_entry("future", at=now + timedelta(hours=2))
        assert next_due_time(entry, now=now) == now + timedelta(hours=2)

    def test_interval_latest_elapsed_slot_is_due(self) -> None:
        created = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
        now = created + timedelta(seconds=25)
        entry = interval_entry("every-10s", interval_seconds=10, created_at=created)
        due = next_due_time(entry, now=now)
        # Slot 20s fully elapsed -> latest elapsed slot is due (overdue).
        assert due == created + timedelta(seconds=20)
        assert due <= now

    def test_interval_not_yet_due_waits_for_first_slot(self) -> None:
        created = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
        now = created + timedelta(seconds=3)
        entry = interval_entry("every-10s", interval_seconds=10, created_at=created)
        due = next_due_time(entry, now=now)
        assert due == created + timedelta(seconds=10)
        assert due > now

    def test_daily_next_occurrence(self) -> None:
        now = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)  # Tuesday
        entry = daily_entry("morning", time_of_day="08:00", created_at=now)
        due = next_due_time(entry, now=now)
        assert (due.day, due.hour, due.minute) == (9, 8, 0)

    def test_daily_before_target_today(self) -> None:
        now = datetime(2026, 9, 8, 6, 0, tzinfo=UTC)
        entry = daily_entry("morning", time_of_day="08:00", created_at=now)
        due = next_due_time(entry, now=now)
        assert (due.day, due.hour) == (8, 8)

    def test_weekday_arithmetic(self) -> None:
        now = datetime(2026, 9, 8, 15, 0, tzinfo=UTC)  # Tuesday
        entry = weekly_entry(
            "monday-standup", weekday="MONDAY", time_of_day="09:00", created_at=now
        )
        due = next_due_time(entry, now=now)
        assert due.weekday() == 0  # Monday
        assert due > now

    def test_naive_now_rejected(self) -> None:
        entry = interval_entry("x", interval_seconds=5, created_at=datetime(2026, 9, 8, tzinfo=UTC))
        with pytest.raises(ValueError, match="timezone-aware"):
            next_due_time(entry, now=datetime(2026, 9, 8, 12, 0))


class TestEntryValidation:
    def test_once_requires_at_time(self) -> None:
        with pytest.raises(ValueError, match="at_time"):
            ScheduleEntry(
                schedule_id="s",
                name="n",
                kind=ScheduleKind.ONCE,
                created_at=datetime.now(UTC),
            )

    def test_interval_requires_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            ScheduleEntry(
                schedule_id="s",
                name="n",
                kind=ScheduleKind.INTERVAL,
                created_at=datetime.now(UTC),
                interval_seconds=0,
            )

    def test_weekly_rejects_bad_weekday(self) -> None:
        with pytest.raises(ValueError, match="weekday"):
            ScheduleEntry(
                schedule_id="s",
                name="n",
                kind=ScheduleKind.WEEKLY,
                created_at=datetime.now(UTC),
                weekday="FUNDAY",
                time_of_day="09:00",
            )

    def test_max_executions_floor(self) -> None:
        with pytest.raises(ValueError, match="max_executions"):
            ScheduleEntry(
                schedule_id="s",
                name="n",
                kind=ScheduleKind.ONCE,
                created_at=datetime.now(UTC),
                at_time=datetime.now(UTC).isoformat(),
                max_executions=0,
            )


class TestSchedulerTick:
    def test_due_once_fires_and_enqueues_task(self, stack: _Stack) -> None:
        scheduler = stack["scheduler"]
        store = stack["store"]
        journal = stack["journal"]
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        entry = once_entry("one-shot", at=now - timedelta(minutes=5))
        scheduler.add(entry)
        fired = scheduler.tick()
        assert len(fired) == 1
        assert fired[0].schedule_id == entry.schedule_id
        tasks = store.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "scheduled: one-shot"
        types = [e.type.value for _, e in journal.all_events()]
        assert "SCHEDULE_FIRED" in types

    def test_future_once_does_not_fire(self, stack: _Stack) -> None:
        scheduler = stack["scheduler"]
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        scheduler.add(once_entry("later", at=now + timedelta(hours=3)))
        assert scheduler.tick() == []

    def test_interval_cooldown_prevents_refire(self, stack: _Stack) -> None:
        scheduler = stack["scheduler"]
        clock = stack["clock"]
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        created = now - timedelta(seconds=30)
        scheduler.add(interval_entry("fast", interval_seconds=5, created_at=created))
        assert len(scheduler.tick()) == 1
        # Same instant: inside cooldown -> held.
        assert scheduler.tick() == []
        # After the cooldown elapses: fires again (next overdue slot).
        clock["now"] = now + timedelta(seconds=10)
        assert len(scheduler.tick()) == 1

    def test_max_executions_retires_entry(self, stack: _Stack) -> None:
        scheduler = stack["scheduler"]
        clock = stack["clock"]
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        created = now - timedelta(seconds=60)
        scheduler.add(
            ScheduleEntry(
                schedule_id="capped",
                name="capped",
                kind=ScheduleKind.INTERVAL,
                created_at=created,
                interval_seconds=1,
                max_executions=2,
            )
        )
        assert len(scheduler.tick()) == 1
        clock["now"] = now + timedelta(seconds=2)  # past cooldown
        assert len(scheduler.tick()) == 1  # second (final) execution
        clock["now"] = now + timedelta(seconds=5)
        assert scheduler.tick() == []  # retired after max_executions

    def test_per_tick_budget_bounds_fires(self, tmp_path: Path) -> None:
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        journal = EventJournal(tmp_path / "j.sqlite")
        store = TaskStore(tmp_path / "t.sqlite", journal=journal)
        try:
            bounded = Scheduler(
                store=store, journal=journal, clock=lambda: now, max_executions_per_tick=5
            )
            for i in range(30):
                bounded.add(once_entry(f"burst-{i}", at=now - timedelta(minutes=1)))
            assert len(bounded.tick()) == 5
        finally:
            store.close()
            journal.close()

    def test_jobs_run_after_fires(self, stack: _Stack) -> None:
        scheduler = stack["scheduler"]
        ran: list[str] = []
        scheduler.register_job("sweep", lambda: ran.append("swept"))
        scheduler.tick()
        assert ran == ["swept"]

    def test_failing_job_does_not_break_tick(self, stack: _Stack) -> None:
        scheduler = stack["scheduler"]
        journal = stack["journal"]
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        scheduler.add(once_entry("fire-me", at=now - timedelta(minutes=1)))

        def bad_job() -> object:
            raise RuntimeError("maintenance explosion")

        scheduler.register_job("bad", bad_job)
        fired = scheduler.tick()
        assert len(fired) == 1  # fire succeeded despite job failure
        payloads = [
            event.payload.get("job_errors")
            for _, event in journal.all_events()
            if event.type.value == "SCHEDULE_FIRED"
        ]
        assert any(p for p in payloads if p)

    def test_duplicate_add_refused(self, stack: _Stack) -> None:
        scheduler = stack["scheduler"]
        now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        entry = once_entry("dup", at=now)
        scheduler.add(entry)
        with pytest.raises(SchedulerError, match="already registered"):
            scheduler.add(entry)

    @pytest.mark.asyncio
    async def test_run_loop_ticks_until_cancelled(self, stack: _Stack) -> None:
        scheduler = stack["scheduler"]
        ticks = {"count": 0}
        original_tick = scheduler.tick

        def counting_tick() -> list[FireResult]:
            ticks["count"] += 1
            return original_tick()

        scheduler.tick = counting_tick  # type: ignore[method-assign]
        task = asyncio.create_task(scheduler.run_loop(interval_seconds=0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ticks["count"] >= 3
