"""FSM tests: table legality, guards, terminal states, and property invariants."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xenopus.runtime.budget import Budget
from xenopus.runtime.fsm import (
    LEGAL_TRANSITIONS,
    AgentFSM,
    AgentState,
    IllegalTransitionError,
)

ALL_STATES = list(AgentState)


class TestUnitFSM:
    def test_initial_state_is_idle(self) -> None:
        assert AgentFSM().state is AgentState.IDLE

    def test_legal_happy_path_walks_to_completed(self) -> None:
        fsm = AgentFSM()
        for target in [
            AgentState.UNDERSTANDING,
            AgentState.GOAL_FORMING,
            AgentState.PLANNING,
            AgentState.READY,
            AgentState.EXECUTING,
            AgentState.OBSERVING,
            AgentState.VERIFYING,
            AgentState.REFLECTING,
            AgentState.COMPLETED,
        ]:
            result = fsm.transition(target)
            assert result.allowed is True
            assert result.side_effect.endswith(f"->{target.value}")

    def test_illegal_transition_raises_with_names(self) -> None:
        fsm = AgentFSM()
        with pytest.raises(IllegalTransitionError, match="IDLE -> COMPLETED"):
            fsm.transition(AgentState.COMPLETED)

    def test_terminal_states_allow_nothing(self) -> None:
        for terminal in [AgentState.COMPLETED, AgentState.FAILED, AgentState.CANCELLED]:
            fsm = AgentFSM()
            fsm._state = terminal
            for target in ALL_STATES:
                assert fsm.can_transition(target) is False

    def test_self_transition_is_illegal(self) -> None:
        fsm = AgentFSM()
        with pytest.raises(IllegalTransitionError, match="IDLE -> IDLE"):
            fsm.transition(AgentState.IDLE)

    def test_guard_denial_raises_permission_error(self) -> None:
        fsm = AgentFSM()
        with pytest.raises(PermissionError, match="Guard denied"):
            fsm.transition(AgentState.UNDERSTANDING, guard=lambda: False)

    def test_guard_pass_allows_transition(self) -> None:
        fsm = AgentFSM()
        result = fsm.transition(AgentState.UNDERSTANDING, guard=lambda: True)
        assert result.guard_passed is True
        assert fsm.state is AgentState.UNDERSTANDING

    def test_transition_result_carries_source_and_target(self) -> None:
        fsm = AgentFSM()
        result = fsm.transition(AgentState.UNDERSTANDING)
        assert result.source is AgentState.IDLE
        assert result.target is AgentState.UNDERSTANDING

    def test_cancel_path_from_executing(self) -> None:
        fsm = AgentFSM()
        fsm.transition(AgentState.UNDERSTANDING)
        fsm.transition(AgentState.GOAL_FORMING)
        fsm.transition(AgentState.PLANNING)
        fsm.transition(AgentState.READY)
        fsm.transition(AgentState.EXECUTING)
        fsm.transition(AgentState.CANCELLING)
        fsm.transition(AgentState.CANCELLED)
        assert fsm.state is AgentState.CANCELLED

    def test_approval_waiting_round_trip(self) -> None:
        fsm = AgentFSM()
        fsm.transition(AgentState.UNDERSTANDING)
        fsm.transition(AgentState.GOAL_FORMING)
        fsm.transition(AgentState.PLANNING)
        fsm.transition(AgentState.READY)
        fsm.transition(AgentState.WAITING_APPROVAL)
        fsm.transition(AgentState.READY)
        assert fsm.state is AgentState.READY

    def test_budget_attached_at_construction(self) -> None:
        budget = Budget.unlimited()
        assert AgentFSM(budget=budget).budget is budget


class TestPropertyFSM:
    @given(st.sampled_from(ALL_STATES), st.sampled_from(ALL_STATES))
    def test_transition_legality_matches_table_exactly(
        self, source: AgentState, target: AgentState
    ) -> None:
        """No illegal state transition — the table is the single source."""
        fsm = AgentFSM()
        fsm._state = source
        expected = target in LEGAL_TRANSITIONS[source]
        assert fsm.can_transition(target) is expected
        if expected:
            fsm.transition(target)
            assert fsm.state is target
        else:
            with pytest.raises(IllegalTransitionError):
                fsm.transition(target)

    @given(st.lists(st.sampled_from(ALL_STATES), max_size=25))
    def test_random_walk_never_reaches_illegal_state(self, walk: list[AgentState]) -> None:
        """A random walk applies only legal moves and never corrupts the FSM."""
        fsm = AgentFSM()
        for target in walk:
            if fsm.can_transition(target):
                fsm.transition(target)
            else:
                with pytest.raises(IllegalTransitionError):
                    fsm.transition(target)

    def test_every_state_has_a_table_entry(self) -> None:
        """The transition table is exhaustive over the 20 states."""
        assert set(LEGAL_TRANSITIONS) == set(AgentState)
