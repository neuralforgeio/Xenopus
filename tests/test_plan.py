"""Plan engine tests: DAG validation, cycles, orphans, topological order."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xenopus.runtime.goal import GoalDraft, GoalError, GoalManager
from xenopus.runtime.plan import PlanEngine, PlanError, TaskNode, TaskStatus


def make_engine_with_active_goal() -> tuple[GoalManager, str]:
    manager = GoalManager()
    goal = manager.create(GoalDraft(objective="Analyze repo", success_criteria=["report"]))
    manager.activate(goal.id)
    return manager, goal.id


def node(nid: str, deps: frozenset[str] | set[str] | None = None) -> TaskNode:
    return TaskNode(id=nid, summary=f"task {nid}", dependencies=frozenset(deps or ()))


class TestUnitPlan:
    def test_single_node_plan_valid(self) -> None:
        goals, goal_id = make_engine_with_active_goal()
        plan = PlanEngine(goals).create(goal_id, [node("t1")])
        assert plan.roots() == ["t1"]
        assert plan.topological_order() == ["t1"]

    def test_diamond_dag_orders_correctly(self) -> None:
        goals, goal_id = make_engine_with_active_goal()
        plan = PlanEngine(goals).create(
            goal_id,
            [
                node("t1"),
                node("t2", {"t1"}),
                node("t3", {"t1"}),
                node("t4", {"t2", "t3"}),
            ],
        )
        order = plan.topological_order()
        assert order.index("t1") < order.index("t2")
        assert order.index("t1") < order.index("t3")
        assert order.index("t2") < order.index("t4")
        assert order.index("t3") < order.index("t4")

    def test_cycle_rejected(self) -> None:
        goals, goal_id = make_engine_with_active_goal()
        with pytest.raises(PlanError, match="cycle"):
            PlanEngine(goals).create(
                goal_id,
                [node("a", {"b"}), node("b", {"a"})],
            )

    def test_unknown_dependency_rejected(self) -> None:
        goals, goal_id = make_engine_with_active_goal()
        with pytest.raises(PlanError, match="unknown task"):
            PlanEngine(goals).create(goal_id, [node("a", {"ghost"})])

    def test_self_dependency_rejected(self) -> None:
        with pytest.raises(ValueError, match="depend on itself"):
            TaskNode(id="a", summary="s", dependencies=frozenset({"a"}))

    def test_empty_node_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            TaskNode(id=" ", summary="s")

    def test_empty_plan_rejected(self) -> None:
        goals, goal_id = make_engine_with_active_goal()
        with pytest.raises(PlanError, match="at least one"):
            PlanEngine(goals).create(goal_id, [])

    def test_duplicate_node_id_rejected(self) -> None:
        goals, goal_id = make_engine_with_active_goal()
        with pytest.raises(PlanError, match="Duplicate"):
            PlanEngine(goals).create(goal_id, [node("a"), node("a")])

    def test_plan_requires_active_goal(self) -> None:
        goals = GoalManager()
        goal = goals.create(GoalDraft(objective="O", success_criteria=["c"]))
        with pytest.raises(GoalError, match="ACTIVE"):
            PlanEngine(goals).create(goal.id, [node("t1")])

    def test_default_status_is_queued(self) -> None:
        assert TaskNode(id="x", summary="s").status is TaskStatus.QUEUED

    def test_topological_order_is_deterministic(self) -> None:
        goals, goal_id = make_engine_with_active_goal()
        nodes = [node("t1"), node("t2", {"t1"}), node("t3", {"t1"}), node("t4", {"t2", "t3"})]
        plan = PlanEngine(goals).create(goal_id, nodes)
        assert plan.topological_order() == plan.topological_order()


class TestPropertyPlan:
    @given(
        st.lists(
            st.text(min_size=1, max_size=6, alphabet="abc"), min_size=1, max_size=3, unique=True
        )
    )
    def test_linear_chain_is_always_valid_and_ordered(self, ids: list[str]) -> None:
        """Any linear dependency chain forms a valid DAG with one root."""
        goals, goal_id = make_engine_with_active_goal()
        nodes: list[TaskNode] = []
        for i, nid in enumerate(ids):
            if i > 0:
                nodes.append(node(nid, {ids[i - 1]}))
            else:
                nodes.append(node(nid))
        plan = PlanEngine(goals).create(goal_id, nodes)
        order = plan.topological_order()
        assert order == ids

    @given(
        st.lists(
            st.tuples(
                st.text(min_size=1, max_size=4, alphabet="t"),
                st.text(min_size=1, max_size=4, alphabet="t"),
            ),
            min_size=0,
            max_size=12,
        )
    )
    def test_arbitrary_edge_set_either_validates_or_names_a_cycle(
        self, edges: list[tuple[str, str]]
    ) -> None:
        """Any edge set either passes validation or fails with a precise error."""
        ids = {eid for pair in edges for eid in pair}
        if not ids:
            return
        goals, goal_id = make_engine_with_active_goal()
        nodes = []
        for nid in sorted(ids):
            deps = {src for src, dst in edges if dst == nid and src != nid}
            nodes.append(node(nid, deps))
        try:
            plan = PlanEngine(goals).create(goal_id, nodes)
            order = plan.topological_order()
            assert sorted(order) == sorted(ids)
            for src, dst in edges:
                if (
                    src in plan.nodes
                    and dst in plan.nodes
                    and src != dst
                    and plan.nodes[dst].dependencies
                ):
                    assert order.index(src) < order.index(dst) or (
                        src not in plan.nodes[dst].dependencies
                    )
        except PlanError as err:
            assert any(
                token in str(err) for token in ("cycle", "unknown task", "orphan", "at least one")
            )
