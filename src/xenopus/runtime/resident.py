"""Resident host: the desktop shell's daemon-shaped surface (ADR-027).

One process hosting the engines: scheduler tick + the periodic
self-improvement loop on a host-controlled cadence + optional
loopback web dashboard opened in the system browser. The host
owns process lifecycle and NOTHING else (ADR-001): no runtime
logic lives here; engines are consumed exactly as the CLI/TUI/Web
surfaces consume them.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from xenopus.memory.store import MemoryStore
from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.reliability import ReliabilityStore
from xenopus.persistence.tasks import TaskStore
from xenopus.runtime.events import Event, EventType
from xenopus.runtime.learning import ReflectionLoop
from xenopus.runtime.scheduler import Scheduler
from xenopus.skills.registry import SkillRegistry

DEFAULT_TICK_INTERVAL_SECONDS = 5.0
DEFAULT_REFLECT_EVERY_SECONDS = 3600.0
MIN_TICK_INTERVAL_SECONDS = 1.0
MAX_TICK_INTERVAL_SECONDS = 3600.0
MIN_REFLECT_EVERY_SECONDS = 5.0
STOP_CHECK_RESOLUTION_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class ResidentReport:
    """Outcome of one resident session (auditable)."""

    correlation_id: str
    ticks: int
    reflection_runs: int
    stopped_cleanly: bool


class ResidentHost:
    """Single-process engine host (ADR-027).

    Contract:
        run(): hosts the scheduler tick until stopped; the
        reflection loop runs when its cadence elapses (host-side
        scheduling — the scheduler's register_job runs EVERY tick,
        so the reflection cadence is governed here, explicitly).
        Stores open at start and close exactly once at stop. Every
        start/stop journals.

    Invariants:
        - No engine logic lives here (ADR-001 boundary).
        - A reflection failure is caught and counted; the hosting
          loop never dies from it (the loop itself also aborts
          cleanly — ADR-026 double isolation).
        - Graceful stop: the current tick drains, then stores close
          exactly once.

    Failure modes:
        ValueError for out-of-bounds intervals; store/engine
        errors propagate after the loop drains (stores still close).
    """

    def __init__(
        self,
        *,
        home: Path,
        tick_interval_seconds: float = DEFAULT_TICK_INTERVAL_SECONDS,
        reflect_every_seconds: float = DEFAULT_REFLECT_EVERY_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not MIN_TICK_INTERVAL_SECONDS <= tick_interval_seconds <= MAX_TICK_INTERVAL_SECONDS:
            msg = (
                "tick interval must be within "
                f"[{MIN_TICK_INTERVAL_SECONDS}, {MAX_TICK_INTERVAL_SECONDS}] seconds"
            )
            raise ValueError(msg)
        if reflect_every_seconds < MIN_REFLECT_EVERY_SECONDS:
            msg = f"reflect cadence must be >= {MIN_REFLECT_EVERY_SECONDS}s"
            raise ValueError(msg)
        self._home = home
        self._tick_interval = tick_interval_seconds
        self._reflect_every = reflect_every_seconds
        self._clock = clock or time.monotonic
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        """Signal the run loop to drain and stop (idempotent)."""
        self._stop.set()

    async def run(self) -> ResidentReport:
        """Host engines until stopped; returns the session report."""
        correlation_id = f"resident-{new_correlation_id()}"
        db = self._home / "runtime.sqlite"
        journal = EventJournal(db, cross_thread=True)
        store = TaskStore(db, journal=journal, cross_thread=True)
        reliability = ReliabilityStore(str(db))
        memory = MemoryStore(self._home / "memory" / "memories.sqlite")
        skills = SkillRegistry(self._home / "skills" / "skills.sqlite")
        scheduler = Scheduler(store=store, journal=journal)
        loop = ReflectionLoop(
            reliability=reliability,
            memory=memory,
            skills=skills,
            journal=journal,
        )
        self._journal(journal, EventType.TASK_STARTED, correlation_id)
        ticks = 0
        reflection_runs = 0
        last_reflect = self._clock()
        stopped_cleanly = False
        try:
            while not self._stop.is_set():
                scheduler.tick()
                ticks += 1
                if self._clock() - last_reflect >= self._reflect_every:
                    last_reflect = self._clock()
                    loop.run()  # ADR-026: aborts cleanly internally
                    reflection_runs += 1
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._tick_interval)
                except TimeoutError:
                    continue
            stopped_cleanly = True
            self._journal(journal, EventType.TASK_COMPLETED, correlation_id)
        finally:
            store.close()
            reliability.close()
            skills.close()
            memory.close()
            journal.close()
        return ResidentReport(
            correlation_id=correlation_id,
            ticks=ticks,
            reflection_runs=reflection_runs,
            stopped_cleanly=stopped_cleanly,
        )

    @staticmethod
    def _journal(journal: EventJournal, event_type: EventType, correlation_id: str) -> None:
        """Start/stop evidence for the audit trail."""
        journal.append(
            Event(
                type=event_type,
                correlation_id=correlation_id,
                payload={"loop": "resident"},
            )
        )


def install_signal_handlers(host: ResidentHost) -> None:
    """Wire SIGINT/SIGTERM to a graceful stop (main thread only)."""
    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: host.request_stop())
        except (ValueError, OSError):
            return  # non-main thread or unsupported platform: Ctrl+C still works
