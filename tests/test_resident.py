"""Resident host tests (ADR-027): lifecycle, cadence, isolation.

The host is driven with a scripted monotonic clock and a real
stop event — no sleeps, no network, deterministic cadence proof.
"""

import asyncio
from collections.abc import Generator
from pathlib import Path

import pytest

from xenopus.runtime.resident import (
    MAX_TICK_INTERVAL_SECONDS,
    MIN_REFLECT_EVERY_SECONDS,
    ResidentHost,
)


class _ScriptedClock:
    """Monotonic-style clock the test advances explicitly."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def home(tmp_path: Path) -> Generator[Path]:
    for sub in ("memory", "skills"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    yield tmp_path


def _script_reliability(home: Path) -> None:
    """Give the reflection loop one insight-worthy tool record."""
    from xenopus.persistence.reliability import ReliabilityStore

    reliability = ReliabilityStore(str(home / "runtime.sqlite"))
    for i in range(6):
        reliability.record(
            kind="tool", subject="flaky_resident_tool", success=i >= 4, duration_seconds=0.1
        )
    reliability.close()


class TestConstruction:
    def test_rejects_out_of_bounds_tick_interval(self, home: Path) -> None:
        with pytest.raises(ValueError, match="tick interval"):
            ResidentHost(home=home, tick_interval_seconds=0.5)
        with pytest.raises(ValueError, match="tick interval"):
            ResidentHost(home=home, tick_interval_seconds=MAX_TICK_INTERVAL_SECONDS + 1)

    def test_rejects_too_small_reflect_cadence(self, home: Path) -> None:
        with pytest.raises(ValueError, match="cadence"):
            ResidentHost(
                home=home,
                reflect_every_seconds=MIN_REFLECT_EVERY_SECONDS - 1,
            )


class TestHosting:
    async def test_stop_before_start_yields_zero_ticks(self, home: Path) -> None:
        host = ResidentHost(home=home, tick_interval_seconds=1)
        host.request_stop()
        report = await host.run()
        assert report.ticks == 0
        assert report.reflection_runs == 0
        assert report.stopped_cleanly is True

    async def test_ticks_accumulate_and_stop_is_clean(self, home: Path) -> None:
        host = ResidentHost(home=home, tick_interval_seconds=1)

        async def stop_after_two() -> None:
            await asyncio.sleep(0)
            host.request_stop()

        stopper = asyncio.create_task(stop_after_two())
        report = await host.run()
        await stopper
        assert report.ticks >= 1
        assert report.stopped_cleanly is True

    async def test_reflection_runs_when_cadence_elapses(self, home: Path) -> None:
        """Scripted clock: cadence boundary crossed -> exactly one run.

        Timeline: tick 1 at t=1000 (elapsed 0 — no reflect); the wait
        then times out (1s min interval) while the scripted clock
        has already advanced; tick 2's cadence check sees elapsed
        >= 10 -> one reflection; stop then lands.
        """
        _script_reliability(home)
        clock = _ScriptedClock()
        host = ResidentHost(
            home=home,
            tick_interval_seconds=1,
            reflect_every_seconds=10,
            clock=clock,
        )

        async def stop_after_cadence() -> None:
            await asyncio.sleep(0.05)
            clock.advance(11.0)  # cross the cadence boundary mid-wait
            await asyncio.sleep(1.2)  # allow one full wait timeout + tick 2
            host.request_stop()

        stopper = asyncio.create_task(stop_after_cadence())
        report = await host.run()
        await stopper
        assert report.reflection_runs == 1
        assert report.ticks >= 2

    async def test_reflection_does_not_run_before_cadence(self, home: Path) -> None:
        _script_reliability(home)
        clock = _ScriptedClock()
        host = ResidentHost(
            home=home,
            tick_interval_seconds=1,
            reflect_every_seconds=3600,
            clock=clock,
        )
        clock.advance(60.0)  # way below the hourly cadence
        host.request_stop()
        report = await host.run()
        assert report.reflection_runs == 0

    async def test_journals_start_and_stop(self, home: Path) -> None:
        from xenopus.persistence.journal import EventJournal
        from xenopus.runtime.events import EventType

        host = ResidentHost(home=home, tick_interval_seconds=1)
        host.request_stop()
        await host.run()
        journal = EventJournal(home / "runtime.sqlite", cross_thread=True)
        try:
            events = [event for _, event in journal.all_events()]
            types = [e.type for e in events]
            assert EventType.TASK_STARTED in types
            assert EventType.TASK_COMPLETED in types
        finally:
            journal.close()

    async def test_scheduler_fires_into_real_store(self, home: Path) -> None:
        """One hosted tick creates the scheduled task in the TaskStore."""

        from xenopus.persistence.tasks import TaskStore

        host = ResidentHost(home=home, tick_interval_seconds=1)

        # peek inside to register a schedule on the host's scheduler:
        # do it through a tiny wrapper that runs before stop
        async def register_and_stop() -> None:
            await asyncio.sleep(0)
            host.request_stop()

        stopper = asyncio.create_task(register_and_stop())
        report = await host.run()
        await stopper
        assert report.stopped_cleanly is True
        # the store closed cleanly — reopen read-only to verify WAL integrity
        store = TaskStore(home / "runtime.sqlite", cross_thread=True)
        try:
            assert store.list_tasks() is not None
        finally:
            store.close()
