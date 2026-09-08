"""Orchestrator: bounded DAG execution with failure isolation.

Executes a validated Plan (addendum 2-5, 13-14): layers of independent
TaskNodes run in parallel under the agent pool; a pre-parallelization
cost gate refuses gratuitous parallelism (addendum 103, 110); one
agent's failure never cancels independent agents; partial failures
produce a partial report with lowered confidence and explicit
incomplete-evidence markers.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from xenopus.runtime.agent import (
    AgentContract,
    AgentProfile,
    AgentResult,
)
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.plan import Plan


class OrchestrationError(Exception):
    """Raised for orchestration misuse: invalid plans, bad wiring."""


class ParallelizationRefused(OrchestrationError):
    """The cost gate refused parallel execution (addendum 103/110).

    The message carries the estimate so the caller can decide to run
    sequentially or ask the user.
    """


PARALLEL_OVERHEAD_PER_AGENT = 1.0  # abstract cost units per spawned agent
PARALLEL_MAX_BENEFIT_RATIO = 10.0  # refuse when overhead/benefit exceeds this


def estimate_parallel_cost(layer_sizes: list[int]) -> dict[str, float]:
    """Estimate coordination overhead vs sequential benefit.

    Deterministic model: spawning N agents in a layer costs N units of
    coordination overhead; sequential benefit is the layer fanout (N-1
    saved serialization slots). The gate compares cumulative overhead
    against cumulative benefit.
    """
    overhead = sum(PARALLEL_OVERHEAD_PER_AGENT * n for n in layer_sizes)
    benefit = sum(max(0, n - 1) for n in layer_sizes)
    return {
        "overhead": overhead,
        "benefit": benefit,
        "ratio": overhead / benefit if benefit else float((overhead and 1e9) or 0.0),
    }


@dataclass(frozen=True, slots=True)
class CostGateDecision:
    """Auditable cost-gate outcome."""

    parallel_allowed: bool
    estimate: dict[str, float]
    reason: str

    def __post_init__(self) -> None:
        if self.parallel_allowed and self.estimate.get("ratio", 0.0) > PARALLEL_MAX_BENEFIT_RATIO:
            msg = "cost gate internal inconsistency: allowed above threshold"
            raise ValueError(msg)


class ParallelizationCostGate:
    """Refuses parallelism whose overhead dominates its benefit.

    Rule (fixed, deterministic): parallel execution is allowed only
    when overhead/benefit <= PARALLEL_MAX_BENEFIT_RATIO and every layer
    has >= 1 node. Single-node layers contribute no benefit; a plan of
    only single-node layers must run sequentially.
    """

    def evaluate(self, layer_sizes: list[int]) -> CostGateDecision:
        """Decide parallel legality for the given layer fanouts."""
        if not layer_sizes:
            return CostGateDecision(
                True, {"overhead": 0.0, "benefit": 0.0, "ratio": 0.0}, "empty plan"
            )
        estimate = estimate_parallel_cost(layer_sizes)
        if all(n <= 1 for n in layer_sizes):
            return CostGateDecision(
                False, estimate, "no layer benefits from parallelism (all fanouts 1)"
            )
        if estimate["ratio"] > PARALLEL_MAX_BENEFIT_RATIO:
            return CostGateDecision(
                False,
                estimate,
                f"overhead/benefit ratio {estimate['ratio']:.1f} exceeds "
                f"{PARALLEL_MAX_BENEFIT_RATIO}",
            )
        return CostGateDecision(True, estimate, "parallel benefit dominates overhead")


@dataclass(frozen=True, slots=True)
class LayerReport:
    """Outcome of one execution layer."""

    layer: int
    results: tuple[AgentResult, ...]

    @property
    def all_completed(self) -> bool:
        """True when every node in the layer completed."""
        return bool(self.results) and all(r.status == "COMPLETED" for r in self.results)

    @property
    def any_completed(self) -> bool:
        """True when at least one node completed (partial progress)."""
        return any(r.status == "COMPLETED" for r in self.results)


@dataclass(frozen=True, slots=True)
class OrchestrationReport:
    """Final report of one orchestrated plan run (addendum 12-13).

    Invariants:
        overall is COMPLETED only when every node completed; PARTIAL
        when some completed and some failed/timed out; FAILED when
        nothing completed. confidence reflects coverage and shrinks
        with incomplete evidence — never silently 1.0 on partial runs.
    """

    plan_id: str
    correlation_id: str
    overall: str
    layers: tuple[LayerReport, ...]
    confidence: float

    @property
    def completed_count(self) -> int:
        """Total completed agent results across layers."""
        return sum(1 for layer in self.layers for r in layer.results if r.status == "COMPLETED")

    @property
    def incomplete_task_ids(self) -> list[str]:
        """Tasks whose evidence is missing (addendum 13 requirement)."""
        return [
            r.task_id for layer in self.layers for r in layer.results if r.status != "COMPLETED"
        ]


AgentRunner = Any  # callable(contract, profile) -> Awaitable[AgentResult]


class Orchestrator:
    """Executes a validated plan DAG under the agent pool.

    Contract:
        execute(): schedules each dependency layer; within a layer,
        independent nodes run in parallel (when the cost gate allows) —
        one node's failure NEVER cancels its siblings (addendum 14);
        downstream nodes whose dependencies failed are marked FAILED
        with a dependency-loss error instead of running on missing
        evidence. A stuck/timeout result is treated as failure with
        incomplete-evidence marking.

    Failure modes:
        ParallelizationRefused when the cost gate blocks the plan's
        parallel shape (callers may re-run in sequential mode);
        OrchestrationError for wiring/plan errors.
    """

    def __init__(
        self,
        *,
        pool: AgentPool,
        runner: AgentRunner,
        profile_for: Any | None = None,
        default_profile: AgentProfile | None = None,
        cost_gate: ParallelizationCostGate | None = None,
        reliability: Any | None = None,
    ) -> None:
        self._pool = pool
        self._runner = runner
        self._profile_for = profile_for
        self._default_profile = default_profile or AgentProfile(
            role="worker",
            allowed_tools=frozenset(),
            forbidden_tools=frozenset(),
            permissions_subject="agent",
        )
        self._cost_gate = cost_gate or ParallelizationCostGate()
        self._reliability = reliability

    async def execute(
        self,
        plan: Plan,
        *,
        correlation_id: str,
        force_sequential: bool = False,
    ) -> OrchestrationReport:
        """Run the plan; returns the layered report."""
        layers = self._dependency_layers(plan)
        if not force_sequential:
            decision = self._cost_gate.evaluate([len(layer) for layer in layers])
            if not decision.parallel_allowed:
                msg = f"parallelization refused: {decision.reason}"
                raise ParallelizationRefused(msg)

        completed_tasks: set[str] = set()
        failed_tasks: set[str] = set()
        layer_reports: list[LayerReport] = []

        for index, layer in enumerate(layers):
            runnable: list[str] = []
            blocked_now: list[AgentResult] = []
            for node_id in layer:
                node = plan.nodes[node_id]
                if node.dependencies and not node.dependencies <= completed_tasks:
                    failed_tasks.add(node_id)
                    blocked_now.append(
                        AgentResult.failed(
                            task_id=node_id,
                            role=self._profile_for_node(node_id).role,
                            error="dependency failed or incomplete: upstream evidence missing",
                            confidence=0.0,
                        )
                    )
                else:
                    runnable.append(node_id)

            if blocked_now and not runnable:
                layer_reports.append(LayerReport(index, tuple(blocked_now)))
                continue  # everything blocked: layer ends, downstream cascades

            gather_results = [self._run_one(plan, node_id, correlation_id) for node_id in runnable]
            results = await asyncio.gather(*gather_results)
            combined: list[AgentResult] = []
            for node_id, result in zip(runnable, results, strict=True):
                combined.append(result)
                if result.status == "COMPLETED":
                    completed_tasks.add(node_id)
                else:
                    failed_tasks.add(node_id)
            combined.extend(blocked_now)
            layer_reports.append(LayerReport(index, tuple(combined)))

        total_nodes = len(plan.nodes)
        completed_total = len(completed_tasks)
        if completed_total == total_nodes:
            overall, confidence = "COMPLETED", 1.0
        elif completed_total > 0:
            overall = "PARTIAL"
            confidence = completed_total / total_nodes
        else:
            overall, confidence = "FAILED", 0.0
        return OrchestrationReport(
            plan_id=plan.plan_id,
            correlation_id=correlation_id,
            overall=overall,
            layers=tuple(layer_reports),
            confidence=confidence,
        )

    # -- internals -------------------------------------------------------

    def _profile_for_node(self, node_id: str) -> AgentProfile:
        """Resolve the profile for a node (planner hook or default)."""
        if self._profile_for is not None:
            profile = self._profile_for(node_id)
            if isinstance(profile, AgentProfile):
                return profile
        return self._default_profile

    async def _run_one(self, plan: Plan, node_id: str, correlation_id: str) -> AgentResult:
        """Run one node through the pool with heartbeats and release."""
        node = plan.nodes[node_id]
        profile = self._profile_for_node(node_id)
        contract = AgentContract(
            mission=node.summary,
            task_id=node_id,
            inputs={"plan_id": plan.plan_id, "correlation_id": correlation_id},
            success_criteria=tuple(node.output_schema or {}),
        )
        try:
            handle = await self._pool.acquire(contract, profile)
        except Exception as err:  # admission limits are plan failures, not crashes
            return AgentResult.failed(
                task_id=node_id, role=profile.role, error=f"admission refused: {err}"
            )
        result: AgentResult
        started_wall = time.monotonic()
        try:
            result = await self._runner(contract, profile)
            self._pool.heartbeat(handle.run_id)
        except TimeoutError:
            result = AgentResult.timeout(
                task_id=node_id, role=profile.role, detail="runner timeout"
            )
        except Exception as err:
            result = AgentResult.failed(
                task_id=node_id, role=profile.role, error=f"runner crash: {err}"
            )
        finally:
            if self._reliability is not None:
                self._reliability.record(
                    kind="agent",
                    subject=profile.role,
                    success=(result.status == "COMPLETED"),
                    duration_seconds=time.monotonic() - started_wall,
                )
        # Pool health tracks liveness, not success semantics; the
        # observed AgentResult carries the outcome to the caller.
        self._pool.release(handle.run_id, failed=False)
        return result

    @staticmethod
    def _dependency_layers(plan: Plan) -> list[list[str]]:
        """Group node ids into dependency layers (Kahn, deterministic)."""
        order = plan.topological_order()
        placed: dict[str, int] = {}
        layers: list[list[str]] = []
        for node_id in order:
            node = plan.nodes[node_id]
            layer_index = 0
            if node.dependencies:
                layer_index = 1 + max(placed[dep] for dep in node.dependencies)
            placed[node_id] = layer_index
            while len(layers) <= layer_index:
                layers.append([])
            layers[layer_index].append(node_id)
        return layers
