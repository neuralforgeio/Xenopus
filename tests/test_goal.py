"""Goal manager tests: validation, lifecycle rules, and plan-binding gate."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xenopus.runtime.goal import (
    GoalDraft,
    GoalError,
    GoalManager,
    GoalPriority,
    GoalStatus,
)


class TestUnitGoal:
    def test_create_valid_draft(self) -> None:
        goal = GoalManager().create(
            GoalDraft(objective="Fix the build", success_criteria=["pytest passes"])
        )
        assert goal.status is GoalStatus.DRAFT
        assert goal.id.startswith("goal-")
        assert goal.priority is GoalPriority.NORMAL

    def test_empty_objective_rejected(self) -> None:
        with pytest.raises(GoalError, match="non-empty"):
            GoalManager().create(GoalDraft(objective="   ", success_criteria=["x"]))

    def test_missing_success_criteria_rejected(self) -> None:
        with pytest.raises(GoalError, match="success criterion"):
            GoalManager().create(GoalDraft(objective="Do a thing"))

    def test_activate_then_require_active(self) -> None:
        manager = GoalManager()
        goal = manager.create(GoalDraft(objective="O", success_criteria=["c1"]))
        assert manager.activate(goal.id).status is GoalStatus.ACTIVE
        assert manager.require_active(goal.id).id == goal.id

    def test_cannot_activate_twice(self) -> None:
        manager = GoalManager()
        goal = manager.create(GoalDraft(objective="O", success_criteria=["c1"]))
        manager.activate(goal.id)
        with pytest.raises(GoalError, match="Only DRAFT"):
            manager.activate(goal.id)

    def test_require_active_rejects_draft(self) -> None:
        manager = GoalManager()
        goal = manager.create(GoalDraft(objective="O", success_criteria=["c1"]))
        with pytest.raises(GoalError, match="ACTIVE"):
            manager.require_active(goal.id)

    def test_achieve_requires_active(self) -> None:
        manager = GoalManager()
        goal = manager.create(GoalDraft(objective="O", success_criteria=["c1"]))
        with pytest.raises(GoalError, match="Only ACTIVE"):
            manager.achieve(goal.id)

    def test_achieved_is_terminal(self) -> None:
        manager = GoalManager()
        goal = manager.create(GoalDraft(objective="O", success_criteria=["c1"]))
        manager.activate(goal.id)
        manager.achieve(goal.id)
        with pytest.raises(GoalError, match="cannot be abandoned"):
            manager.abandon(goal.id)

    def test_unknown_goal_raises_goal_error(self) -> None:
        with pytest.raises(GoalError, match="Unknown goal"):
            GoalManager().get("goal-nonexistent")

    def test_to_dict_exposes_objective_not_budget_internals(self) -> None:
        manager = GoalManager()
        goal = manager.create(GoalDraft(objective="O", success_criteria=["c1"]))
        as_dict = goal.to_dict()
        assert as_dict["objective"] == "O"
        assert "budget" not in as_dict


class TestPropertyGoal:
    @given(
        st.text(min_size=1, max_size=80).filter(lambda s: s.strip()),
        st.lists(st.text(min_size=1, max_size=40), min_size=1, max_size=5),
        st.sampled_from(GoalPriority),
    )
    def test_any_valid_draft_creates_deterministically(
        self, objective: str, criteria: list[str], priority: GoalPriority
    ) -> None:
        manager = GoalManager()
        goal = manager.create(
            GoalDraft(objective=objective, success_criteria=criteria, priority=priority)
        )
        assert goal.objective == objective.strip()
        assert goal.priority is priority
        assert goal.status is GoalStatus.DRAFT
