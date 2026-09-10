"""Agent pool: admission control, session limits, and the watchdog.

Bounded multi-agent substrate (addendum 6-7, 15): a fixed concurrency
semaphore, per-session recursion limits (depth/children/total), live
run tracking with heartbeats, and stuck detection that transitions
health (addendum 19, 59). No unbounded spawn can pass this gate.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from xenopus.runtime.agent import AgentContract, AgentHealth, AgentProfile

DEFAULT_MAX_CONCURRENT_AGENTS = 4
DEFAULT_MAX_AGENT_DEPTH = 2
DEFAULT_MAX_CHILD_AGENTS = 4
DEFAULT_MAX_AGENT_TOTAL = 12
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 30.0


class AdmissionError(Exception):
    """Raised when a spawn would exceed a pool limit.

    The message names the violated limit so callers can decide to
    serialize, substitute, or escalate (addendum 7: the scheduler
    considers limits, never silently overflows them).
    """


@dataclass(frozen=True, slots=True)
class AgentRunHandle:
    """Live registration of one agent run."""

    run_id: str
    task_id: str
    depth: int
    profile: AgentProfile
    started_at: str
    last_heartbeat: str


@dataclass(frozen=True, slots=True)
class PoolLimits:
    """Session-wide bounds (addendum 15)."""

    max_concurrent: int = DEFAULT_MAX_CONCURRENT_AGENTS
    max_depth: int = DEFAULT_MAX_AGENT_DEPTH
    max_children: int = DEFAULT_MAX_CHILD_AGENTS
    max_total: int = DEFAULT_MAX_AGENT_TOTAL
    heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        for name, value in (
            ("max_concurrent", self.max_concurrent),
            ("max_depth", self.max_depth),
            ("max_children", self.max_children),
            ("max_total", self.max_total),
        ):
            if value < 1:
                msg = f"{name} must be >= 1, got {value}"
                raise ValueError(msg)
        if self.heartbeat_timeout_seconds <= 0:
            msg = "heartbeat_timeout_seconds must be > 0"
            raise ValueError(msg)


@dataclass(slots=True)
class _RunEntry:
    handle: AgentRunHandle
    health: AgentHealth
    last_heartbeat_dt: datetime


class AgentPool:
    """Admission-controlled agent registry with a heartbeat watchdog.

    Contract:
        acquire(): blocks (async) until a concurrency slot frees, then
            admits the run — or raises AdmissionError immediately when
            depth/children/total limits would be violated.
        release(): frees the slot and records the final result state.
        heartbeat(): refreshes liveness for a run.
        detect_stuck(): transitions silent runs to STUCK (watchdog).

    Invariants:
        live run count never exceeds limits.max_concurrent; session
        totals never exceed limits.max_total.
    """

    def __init__(
        self,
        *,
        limits: PoolLimits | None = None,
        clock: Any = None,
    ) -> None:
        self._limits = limits or PoolLimits()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._semaphore = asyncio.Semaphore(self._limits.max_concurrent)
        self._live: dict[str, _RunEntry] = {}
        self._total_spawned = 0
        self._children_by_parent: dict[str, int] = {}

    @property
    def limits(self) -> PoolLimits:
        """Active pool bounds."""
        return self._limits

    @property
    def live_count(self) -> int:
        """Currently admitted runs."""
        return len(self._live)

    @property
    def live_handles(self) -> tuple[AgentRunHandle, ...]:
        """Live run handles, run_id-sorted (read-only watchdog view).

        Observation surface for the watchdog UI (Phase 10): the pool
        owns admission and health transitions; consumers read handles
        and ask ``health_of`` per run. Single-loop usage (same-thread
        asyncio, as the orchestrator uses) makes the snapshot atomic.
        """
        return tuple(self._live[run_id].handle for run_id in sorted(self._live))

    async def acquire(
        self,
        contract: AgentContract,
        profile: AgentProfile,
        *,
        depth: int = 0,
        parent_run_id: str | None = None,
    ) -> AgentRunHandle:
        """Admit one agent run under all limits; blocks for a slot.

        Raises:
            AdmissionError: when depth exceeds the profile or session
                bound, when the parent's child budget is exhausted, or
                when the session total is exhausted. Concurrent-slot
                waits are allowed (that is scheduling, not violation).
        """
        if depth > self._limits.max_depth or depth > profile.max_depth:
            msg = f"agent depth {depth} exceeds limit {self._limits.max_depth}"
            raise AdmissionError(msg)
        if parent_run_id is not None:
            used = self._children_by_parent.get(parent_run_id, 0)
            if used >= self._limits.max_children:
                msg = f"parent {parent_run_id} exhausted child budget"
                raise AdmissionError(msg)
        if self._total_spawned >= self._limits.max_total:
            msg = f"session agent total {self._limits.max_total} exhausted"
            raise AdmissionError(msg)

        await self._semaphore.acquire()
        run_id = f"agent-{uuid4().hex[:12]}"
        now = self._clock()
        handle = AgentRunHandle(
            run_id=run_id,
            task_id=contract.task_id,
            depth=depth,
            profile=profile,
            started_at=now.isoformat(),
            last_heartbeat=now.isoformat(),
        )
        self._live[run_id] = _RunEntry(handle, AgentHealth.HEALTHY, now)
        self._total_spawned += 1
        if parent_run_id is not None:
            self._children_by_parent[parent_run_id] = (
                self._children_by_parent.get(parent_run_id, 0) + 1
            )
        return handle

    def release(self, run_id: str, *, failed: bool) -> None:
        """Free a slot and record final health."""
        entry = self._live.pop(run_id, None)
        if entry is None:
            return
        if failed:
            entry.health = AgentHealth.FAILED
        self._semaphore.release()

    def heartbeat(self, run_id: str) -> None:
        """Refresh liveness; unknown runs are ignored (idempotent)."""
        entry = self._live.get(run_id)
        if entry is not None:
            entry.last_heartbeat_dt = self._clock()
            entry.handle = AgentRunHandle(
                run_id=entry.handle.run_id,
                task_id=entry.handle.task_id,
                depth=entry.handle.depth,
                profile=entry.handle.profile,
                started_at=entry.handle.started_at,
                last_heartbeat=entry.last_heartbeat_dt.isoformat(),
            )
            if entry.health is AgentHealth.STUCK:
                entry.health = AgentHealth.RECOVERING

    def detect_stuck(self) -> list[AgentRunHandle]:
        """Flag runs whose heartbeat lapsed; returns them (watchdog).

        A STUCK run keeps its slot (the operator decides: recover,
        cancel, or wait) — the watchdog never kills silently
        (addendum 58: do not kill tasks blindly).
        """
        threshold = self._limits.heartbeat_timeout_seconds
        now = self._clock()
        stuck: list[AgentRunHandle] = []
        for entry in self._live.values():
            elapsed = (now - entry.last_heartbeat_dt).total_seconds()
            if elapsed > threshold and entry.health is AgentHealth.HEALTHY:
                entry.health = AgentHealth.STUCK
                stuck.append(entry.handle)
        return stuck

    def health_of(self, run_id: str) -> AgentHealth | None:
        """Current health of a live run (None when not live)."""
        entry = self._live.get(run_id)
        return entry.health if entry else None
