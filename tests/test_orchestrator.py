"""Orchestrator tests: layers, isolation, partial failure, cost gate."""

import asyncio

import pytest

from xenopus.runtime.agent import AgentContract, AgentProfile, AgentResult
from xenopus.runtime.agent_pool import AgentPool, PoolLimits
from xenopus.runtime.goal import GoalDraft, GoalManager
from xenopus.runtime.orchestrator import (
    Orchestrator,
    ParallelizationCostGate,
    ParallelizationRefused,
)
from xenopus.runtime.plan import Plan, PlanEngine, TaskNode


def node(nid: str, deps: frozenset[str] | set[str] | None = None) -> TaskNode:
    return TaskNode(id=nid, summary=f"task {nid}", dependencies=frozenset(deps or ()))


def make_plan(
    node_specs: list[tuple[str, frozenset[str] | set[str]]],
) -> Plan:
    goals = GoalManager()
    goal = goals.create(GoalDraft(objective="O", success_criteria=["c"]))
    goals.activate(goal.id)
    nodes = [node(nid, deps) for nid, deps in node_specs]
    return PlanEngine(goals).create(goal.id, nodes)


def make_orchestrator(
    runner: object,
    *,
    pool: AgentPool | None = None,
) -> Orchestrator:
    return Orchestrator(
        pool=pool or AgentPool(limits=PoolLimits(max_concurrent=4, max_total=12)),
        runner=runner,
    )


class TestCostGate:
    def test_single_node_layers_refuse_parallel(self) -> None:
        gate = ParallelizationCostGate()
        decision = gate.evaluate([1, 1, 1])
        assert decision.parallel_allowed is False
        assert "fanouts 1" in decision.reason

    def test_wide_layer_allows_parallel(self) -> None:
        gate = ParallelizationCostGate()
        decision = gate.evaluate([4])
        assert decision.parallel_allowed is True

    def test_empty_plan_allowed(self) -> None:
        gate = ParallelizationCostGate()
        assert gate.evaluate([]).parallel_allowed is True

    def test_estimate_is_deterministic(self) -> None:
        gate = ParallelizationCostGate()
        d1 = gate.evaluate([2, 3])
        d2 = gate.evaluate([2, 3])
        assert d1.estimate == d2.estimate


class TestOrchestratorLayers:
    @pytest.mark.asyncio
    async def test_sequential_chain_completes(self) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            return AgentResult.completed(task_id=contract.task_id, role=profile.role, summary="ok")

        plan = make_plan([("a", frozenset()), ("b", {"a"}), ("c", {"b"})])
        report = await make_orchestrator(runner).execute(
            plan, correlation_id="c1", force_sequential=True
        )
        assert report.overall == "COMPLETED"
        assert report.confidence == 1.0
        assert report.completed_count == 3

    @pytest.mark.asyncio
    async def test_parallel_layer_runs_independently(self) -> None:
        started = asyncio.Event()
        running = {"count": 0, "peak": 0}

        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            running["count"] += 1
            running["peak"] = max(running["peak"], running["count"])
            await asyncio.sleep(0.02)  # overlap in time
            running["count"] -= 1
            return AgentResult.completed(task_id=contract.task_id, role=profile.role, summary="ok")

        started.set()
        plan = make_plan([("a", frozenset()), ("b", frozenset()), ("c", frozenset())])
        report = await make_orchestrator(runner).execute(plan, correlation_id="c1")
        assert report.overall == "COMPLETED"
        assert running["peak"] >= 2  # genuinely concurrent

    @pytest.mark.asyncio
    async def test_cost_gate_refuses_pure_chain(self) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            return AgentResult.completed(task_id=contract.task_id, role=profile.role, summary="ok")

        plan = make_plan([("a", frozenset()), ("b", {"a"}), ("c", {"b"})])
        with pytest.raises(ParallelizationRefused, match="fanouts 1"):
            await make_orchestrator(runner).execute(plan, correlation_id="c1")


class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_sibling_failure_does_not_kill_layer(self) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            if contract.task_id == "bad":
                raise RuntimeError("agent explosion")
            return AgentResult.completed(task_id=contract.task_id, role=profile.role, summary="ok")

        plan = make_plan([("good", frozenset()), ("bad", frozenset()), ("also-good", frozenset())])
        report = await make_orchestrator(runner).execute(plan, correlation_id="c1")
        assert report.overall == "PARTIAL"
        assert report.confidence == pytest.approx(2 / 3)
        assert set(report.incomplete_task_ids) == {"bad"}

    @pytest.mark.asyncio
    async def test_dependency_failure_blocks_downstream_with_marker(self) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            if contract.task_id == "upstream":
                return AgentResult.failed(
                    task_id="upstream", role=profile.role, error="no evidence"
                )
            return AgentResult.completed(task_id=contract.task_id, role=profile.role, summary="ok")

        plan = make_plan([("upstream", frozenset()), ("downstream", {"upstream"})])
        report = await make_orchestrator(runner).execute(
            plan, correlation_id="c1", force_sequential=True
        )
        # Nothing completed (upstream failed, downstream blocked): FAILED.
        assert report.overall == "FAILED"
        assert report.confidence == 0.0
        assert "upstream" in report.incomplete_task_ids
        assert "downstream" in report.incomplete_task_ids
        downstream_result = next(
            r for layer in report.layers for r in layer.results if r.task_id == "downstream"
        )
        assert "dependency failed" in downstream_result.errors[0]

    @pytest.mark.asyncio
    async def test_timeout_produces_timeout_result(self) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            if contract.task_id == "slow":
                raise TimeoutError
            return AgentResult.completed(task_id=contract.task_id, role=profile.role, summary="ok")

        plan = make_plan([("slow", frozenset()), ("fast", frozenset())])
        report = await make_orchestrator(runner).execute(plan, correlation_id="c1")
        assert report.overall == "PARTIAL"
        slow = next(r for layer in report.layers for r in layer.results if r.task_id == "slow")
        assert slow.status == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_admission_limit_failure_is_reported_not_raised(self) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            return AgentResult.completed(task_id=contract.task_id, role=profile.role, summary="ok")

        # total=1: the second sibling cannot be admitted — reported as
        # a per-node failure, never an orchestration crash.
        tiny_pool = AgentPool(limits=PoolLimits(max_concurrent=2, max_total=1))
        plan = make_plan([("a", frozenset()), ("b", frozenset())])
        report = await make_orchestrator(runner, pool=tiny_pool).execute(plan, correlation_id="c1")
        assert report.overall == "PARTIAL"
        assert "b" in report.incomplete_task_ids


class TestLayerReport:
    @pytest.mark.asyncio
    async def test_layer_grouping_matches_dependencies(self) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            return AgentResult.completed(task_id=contract.task_id, role=profile.role, summary="ok")

        plan = make_plan([("a", frozenset()), ("b", frozenset()), ("c", {"a", "b"})])
        report = await make_orchestrator(runner).execute(plan, correlation_id="c1")
        assert len(report.layers) == 2
        assert {r.task_id for r in report.layers[0].results} == {"a", "b"}
        assert {r.task_id for r in report.layers[1].results} == {"c"}
