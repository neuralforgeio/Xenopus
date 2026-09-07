"""Plan engine: task DAG model, acyclic validation, deterministic ordering.

A Plan is a validated DAG of TaskNode bound to an ACTIVE goal (master
prompt 16 / addendum 3-4). The validator rejects cycles, orphans, unknown
dependencies, and self-dependencies. Parallel execution is Phase 7 — this
module provides the data plane only.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from xenopus.runtime.budget import Budget, RetryPolicy
from xenopus.runtime.goal import GoalManager


class PlanError(Exception):
    """Raised for invalid plan construction or validation failures."""


class TaskStatus(StrEnum):
    """Durable task-node statuses (addendum 21; execution is Phase 6)."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    PAUSED = "PAUSED"
    RESUMABLE = "RESUMABLE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class TaskNode:
    """A single node in the plan DAG (addendum 4 field set).

    Contract:
        dependencies reference other node ids in the same plan.
        required_tools/forbidden_tools constrain Phase 4 tool selection.
        output_schema describes the expected structured result shape.

    Invariants (enforced by Plan.validate):
        no self-dependency; all dependencies exist; the dependency graph
        is acyclic; every node is reachable from the root set.
    """

    id: str
    summary: str
    dependencies: frozenset[str] = frozenset()
    agent_type: str = "default"
    status: TaskStatus = TaskStatus.QUEUED
    required_tools: frozenset[str] = frozenset()
    forbidden_tools: frozenset[str] = frozenset()
    budget: Budget | None = None
    retry_policy: RetryPolicy | None = None
    timeout_seconds: float | None = None
    output_schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            msg = "TaskNode id must be non-empty"
            raise ValueError(msg)
        if self.id in self.dependencies:
            msg = f"TaskNode {self.id} cannot depend on itself"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Plan:
    """A validated task DAG bound to one goal.

    Contract:
        goal_id: must reference an ACTIVE goal at construction time.
        nodes: node ids are unique; the graph must be acyclic.

    Failure modes:
        PlanError when validation fails; the plan is never partially valid.
    """

    goal_id: str
    nodes: dict[str, TaskNode] = field(default_factory=dict)
    plan_id: str = field(default_factory=lambda: f"plan-{uuid4().hex[:12]}")

    def __post_init__(self) -> None:
        self.validate()

    # -- queries ---------------------------------------------------------

    def roots(self) -> list[str]:
        """Nodes with no dependencies, in deterministic (sorted) order."""
        return sorted(n.id for n in self.nodes.values() if not n.dependencies)

    def validate(self) -> None:
        """Enforce all DAG invariants; raises PlanError on the first issue."""
        if not self.nodes:
            msg = "A plan must contain at least one task node"
            raise PlanError(msg)
        for node in self.nodes.values():
            for dep in node.dependencies:
                if dep not in self.nodes:
                    msg = f"Task {node.id} depends on unknown task {dep!r}"
                    raise PlanError(msg)
        if cycle := self._find_cycle():
            msg = f"Dependency cycle detected: {' -> '.join([*cycle, cycle[0]])}"
            raise PlanError(msg)
        reachable = self._reachable_from_roots()
        orphans = sorted(set(self.nodes) - reachable)
        if orphans:
            msg = f"Unreachable (orphan) tasks detected: {orphans}"
            raise PlanError(msg)

    def topological_order(self) -> list[str]:
        """Deterministic topological order (Kahn's algorithm, sorted frontier).

        Determinism matters: identical plans must produce identical orders
        so journal replay and tests are reproducible.
        """
        self.validate()
        indegree = {nid: len(node.dependencies) for nid, node in self.nodes.items()}
        dependents: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for node in self.nodes.values():
            for dep in node.dependencies:
                dependents[dep].append(node.id)
        queue = sorted(nid for nid, deg in indegree.items() if deg == 0)
        order: list[str] = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            newly_free: list[str] = []
            for child in dependents[current]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    newly_free.append(child)
            queue = sorted(queue + newly_free)
        return order

    # -- internals -------------------------------------------------------

    def _find_cycle(self) -> list[str] | None:
        """Return one cycle path (list of node ids) or None."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = dict.fromkeys(self.nodes, WHITE)
        path: list[str] = []

        def visit(nid: str) -> list[str] | None:
            color[nid] = GRAY
            path.append(nid)
            for dep in sorted(self.nodes[nid].dependencies):
                if color[dep] == GRAY:
                    idx = path.index(dep)
                    return path[idx:]
                if color[dep] == WHITE and (found := visit(dep)) is not None:
                    return found
            path.pop()
            color[nid] = BLACK
            return None

        for nid in sorted(self.nodes):
            if color[nid] == WHITE and (cycle := visit(nid)) is not None:
                return cycle
        return None

    def _reachable_from_roots(self) -> set[str]:
        """All nodes reachable from roots following dependencies upward."""
        seen: set[str] = set()
        # Reachability here means: connected to the root set through any
        # dependency chain (orphans are nodes no path touches).
        for root in self.nodes.values():
            if not root.dependencies:
                seen.add(root.id)
        changed = True
        while changed:
            changed = False
            for node in self.nodes.values():
                if node.id in seen:
                    continue
                if node.dependencies and node.dependencies <= seen:
                    seen.add(node.id)
                    changed = True
        # A node whose dependencies are unreachable is itself unreachable;
        # iterate to fixpoint over the reachable closure.
        return seen


class PlanEngine:
    """Creates validated plans bound to goals via the GoalManager."""

    def __init__(self, goals: GoalManager) -> None:
        self._goals = goals

    def create(self, goal_id: str, nodes: list[TaskNode]) -> Plan:
        """Validate the goal is ACTIVE, then build and validate the plan."""
        self._goals.require_active(goal_id)
        if not nodes:
            msg = "A plan needs at least one task node"
            raise PlanError(msg)
        node_map: dict[str, TaskNode] = {}
        for node in nodes:
            if node.id in node_map:
                msg = f"Duplicate task id in plan: {node.id}"
                raise PlanError(msg)
            node_map[node.id] = node
        return Plan(goal_id=goal_id, nodes=node_map)
