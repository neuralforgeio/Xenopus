"""Agent finite state machine: 20 states, legal transitions, guard rails.

Data-driven transition table (ADR-003). Illegal transitions are rejected,
never silently coerced. Every transition returns a structured record with
trigger, guard outcome, and observable side effect so callers can journal
it (Protocol v9 Section 12).
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from xenopus.runtime.budget import Budget

Guard = Callable[[], bool]


class AgentState(StrEnum):
    """The 20 canonical agent states (master prompt 14 + Rev.2 additions)."""

    IDLE = "IDLE"
    UNDERSTANDING = "UNDERSTANDING"
    GOAL_FORMING = "GOAL_FORMING"
    PLANNING = "PLANNING"
    READY = "READY"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    VERIFYING = "VERIFYING"
    REFLECTING = "REFLECTING"
    COMPLETED = "COMPLETED"
    RECOVERING = "RECOVERING"
    RETRYING = "RETRYING"
    REPLANNING = "REPLANNING"
    FAILED = "FAILED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PAUSED = "PAUSED"
    WAITING = "WAITING"
    RESUMABLE = "RESUMABLE"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"


# Legal transitions: (source state) -> {allowed target states}.
# Any transition not present here is illegal by definition.
LEGAL_TRANSITIONS: dict[AgentState, frozenset[AgentState]] = {
    AgentState.IDLE: frozenset({AgentState.UNDERSTANDING}),
    AgentState.UNDERSTANDING: frozenset({AgentState.GOAL_FORMING, AgentState.CANCELLING}),
    AgentState.GOAL_FORMING: frozenset(
        {AgentState.PLANNING, AgentState.CANCELLING, AgentState.WAITING_APPROVAL}
    ),
    AgentState.PLANNING: frozenset(
        {
            AgentState.READY,
            AgentState.REPLANNING,
            AgentState.WAITING_APPROVAL,
            AgentState.CANCELLING,
            AgentState.FAILED,
        }
    ),
    AgentState.READY: frozenset(
        {AgentState.EXECUTING, AgentState.WAITING_APPROVAL, AgentState.CANCELLING}
    ),
    AgentState.EXECUTING: frozenset(
        {
            AgentState.OBSERVING,
            AgentState.PAUSED,
            AgentState.WAITING_APPROVAL,
            AgentState.RECOVERING,
            AgentState.CANCELLING,
        }
    ),
    AgentState.OBSERVING: frozenset(
        {AgentState.VERIFYING, AgentState.EXECUTING, AgentState.RECOVERING}
    ),
    AgentState.VERIFYING: frozenset(
        {AgentState.REFLECTING, AgentState.RETRYING, AgentState.REPLANNING}
    ),
    AgentState.REFLECTING: frozenset({AgentState.COMPLETED, AgentState.REPLANNING}),
    AgentState.COMPLETED: frozenset(),
    AgentState.RECOVERING: frozenset(
        {AgentState.RETRYING, AgentState.REPLANNING, AgentState.FAILED}
    ),
    AgentState.RETRYING: frozenset(
        {AgentState.EXECUTING, AgentState.REPLANNING, AgentState.FAILED}
    ),
    AgentState.REPLANNING: frozenset(
        {AgentState.PLANNING, AgentState.FAILED, AgentState.CANCELLING}
    ),
    AgentState.FAILED: frozenset(),
    AgentState.WAITING_APPROVAL: frozenset({AgentState.READY, AgentState.CANCELLING}),
    AgentState.PAUSED: frozenset({AgentState.RESUMABLE, AgentState.CANCELLING}),
    AgentState.WAITING: frozenset({AgentState.READY, AgentState.CANCELLING}),
    AgentState.RESUMABLE: frozenset({AgentState.EXECUTING, AgentState.CANCELLING}),
    AgentState.CANCELLING: frozenset({AgentState.CANCELLED}),
    AgentState.CANCELLED: frozenset(),
}


class IllegalTransitionError(Exception):
    """Raised when a transition is not in the legal transition table.

    The message names source and target states so callers can log the exact
    FSM violation without inspecting internal tables.
    """

    def __init__(self, source: AgentState, target: AgentState) -> None:
        self.source = source
        self.target = target
        super().__init__(f"Illegal FSM transition: {source.value} -> {target.value}")


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of an attempted (successful) FSM transition.

    Contract:
        allowed: True — a rejected transition raises instead.
        guard_passed: outcome of the optional guard predicate.
        side_effect: description of the observable action to journal.
    """

    source: AgentState
    target: AgentState
    allowed: bool
    guard_passed: bool
    side_effect: str


class AgentFSM:
    """Mutable FSM instance tracking one agent's lifecycle.

    Contract:
        state: the current state; changes only through :meth:`transition`.
        budget: execution budget attached at construction (policy only).

    Failure modes:
        IllegalTransitionError on any transition outside the legal table,
        including terminal -> anything and self-transitions.
    """

    def __init__(self, budget: Budget | None = None) -> None:
        self._state: AgentState = AgentState.IDLE
        self.budget = budget if budget is not None else Budget.unlimited()

    @property
    def state(self) -> AgentState:
        """The current FSM state."""
        return self._state

    def can_transition(self, target: AgentState) -> bool:
        """Check legality without mutating state (pure query)."""
        return target in LEGAL_TRANSITIONS[self._state]

    def transition(self, target: AgentState, guard: Guard | None = None) -> TransitionResult:
        """Move to ``target`` if legal and the optional guard passes.

        Raises:
            IllegalTransitionError: when the transition is not legal.
            PermissionError: when a guard explicitly denies the move
                (guards encode approval requirements, master prompt 68).

        Side effects: none directly; the returned record describes the
        side effect the caller must journal (event emission is Phase 6).
        """
        if not self.can_transition(target):
            raise IllegalTransitionError(self._state, target)
        if guard is not None and not guard():
            msg = f"Guard denied transition {self._state.value} -> {target.value}"
            raise PermissionError(msg)
        source, self._state = self._state, target
        return TransitionResult(
            source=source,
            target=target,
            allowed=True,
            guard_passed=guard is None or guard(),
            side_effect=f"fsm:{source.value}->{target.value}",
        )
