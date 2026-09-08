"""Agent model + pool tests: limits, admission, watchdog, concurrency."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from xenopus.runtime.agent import (
    AgentContract,
    AgentHealth,
    AgentProfile,
    AgentResult,
)
from xenopus.runtime.agent_pool import (
    AdmissionError,
    AgentPool,
    PoolLimits,
)


def profile(role: str = "worker") -> AgentProfile:
    return AgentProfile(
        role=role,
        allowed_tools=frozenset({"file_read"}),
        forbidden_tools=frozenset({"file_delete"}),
        permissions_subject=f"agent-{role}",
    )


def contract(task: str = "t1") -> AgentContract:
    return AgentContract(mission="do the thing", task_id=task)


class TestAgentModel:
    def test_profile_rejects_tool_overlap(self) -> None:
        with pytest.raises(ValueError, match="both allowed and forbidden"):
            AgentProfile(
                role="x",
                allowed_tools=frozenset({"a"}),
                forbidden_tools=frozenset({"a"}),
                permissions_subject="s",
            )

    def test_contract_requires_task(self) -> None:
        with pytest.raises(ValueError, match="task id"):
            AgentContract(mission="m", task_id=" ")

    def test_result_confidence_bounds(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            AgentResult.completed(task_id="t", role="r", summary="s", confidence=1.5)

    def test_result_factories(self) -> None:
        ok = AgentResult.completed(task_id="t", role="r", summary="done")
        bad = AgentResult.failed(task_id="t", role="r", error="boom")
        slow = AgentResult.timeout(task_id="t", role="r", detail="late")
        assert ok.status == "COMPLETED"
        assert bad.status == "FAILED" and bad.errors
        assert slow.status == "TIMEOUT" and slow.confidence == 0.0


class TestPoolLimits:
    def test_limits_validation(self) -> None:
        with pytest.raises(ValueError, match="max_concurrent"):
            PoolLimits(max_concurrent=0)
        with pytest.raises(ValueError, match="heartbeat"):
            PoolLimits(heartbeat_timeout_seconds=0)

    @pytest.mark.asyncio
    async def test_depth_limit_enforced(self) -> None:
        pool = AgentPool(limits=PoolLimits(max_depth=2))
        with pytest.raises(AdmissionError, match="depth"):
            await pool.acquire(contract(), profile(), depth=3)

    @pytest.mark.asyncio
    async def test_child_budget_enforced(self) -> None:
        pool = AgentPool(limits=PoolLimits(max_children=1))
        parent = await pool.acquire(contract(), profile())
        await pool.acquire(contract(), profile(), parent_run_id=parent.run_id)
        with pytest.raises(AdmissionError, match="child budget"):
            await pool.acquire(contract(), profile(), parent_run_id=parent.run_id)

    @pytest.mark.asyncio
    async def test_total_budget_enforced(self) -> None:
        pool = AgentPool(limits=PoolLimits(max_total=2, max_concurrent=2))
        await pool.acquire(contract("a"), profile())
        await pool.acquire(contract("b"), profile())
        with pytest.raises(AdmissionError, match="total"):
            await pool.acquire(contract("c"), profile())

    @pytest.mark.asyncio
    async def test_concurrency_semaphore_bounds_live_count(self) -> None:
        pool = AgentPool(limits=PoolLimits(max_concurrent=2, max_total=10))

        holders = [asyncio.create_task(_hold_slot(pool, f"task-{i}")) for i in range(2)]
        await asyncio.sleep(0)  # let both acquire
        assert pool.live_count == 2
        try:
            await asyncio.wait_for(pool.acquire(contract("c"), profile()), timeout=0.05)
            pytest.fail("third acquire should have blocked on the semaphore")
        except TimeoutError:
            pass  # expected: bounded by max_concurrent
        finally:
            for holder in holders:
                holder.cancel()
            await asyncio.gather(*holders, return_exceptions=True)


async def _hold_slot(pool: AgentPool, task_id: str) -> None:
    """Acquire a slot and hold it until cancelled."""
    handle = await pool.acquire(contract(task_id), profile())
    try:
        await asyncio.Event().wait()  # park forever until cancelled
    finally:
        pool.release(handle.run_id, failed=False)


class TestWatchdog:
    @pytest.mark.asyncio
    async def test_stuck_detection_and_recovery(self) -> None:
        now = {"t": datetime.now(UTC)}
        pool = AgentPool(
            limits=PoolLimits(heartbeat_timeout_seconds=10),
            clock=lambda: now["t"],
        )
        handle = await pool.acquire(contract(), profile())
        now["t"] = now["t"] + timedelta(seconds=11)
        stuck = pool.detect_stuck()
        assert [h.run_id for h in stuck] == [handle.run_id]
        assert pool.health_of(handle.run_id) is AgentHealth.STUCK
        pool.heartbeat(handle.run_id)  # recovery: stuck -> recovering
        assert pool.health_of(handle.run_id) is AgentHealth.RECOVERING

    @pytest.mark.asyncio
    async def test_fresh_heartbeat_not_flagged(self) -> None:
        now = {"t": datetime.now(UTC)}
        pool = AgentPool(
            limits=PoolLimits(heartbeat_timeout_seconds=60),
            clock=lambda: now["t"],
        )
        handle = await pool.acquire(contract(), profile())
        now["t"] = now["t"] + timedelta(seconds=5)
        assert pool.detect_stuck() == []
        assert pool.health_of(handle.run_id) is AgentHealth.HEALTHY

    @pytest.mark.asyncio
    async def test_release_frees_slot(self) -> None:
        pool = AgentPool(limits=PoolLimits(max_concurrent=1))
        handle = await pool.acquire(contract(), profile())
        pool.release(handle.run_id, failed=False)
        second = await pool.acquire(contract("b"), profile())  # no deadlock
        assert second.run_id != handle.run_id
