"""Goal model and manager: every plan must bind to an approved goal.

A Goal carries objective, constraints, non-goals, priority, success
criteria, risk tolerance, and budget (master prompt 15). The manager
validates goals and tracks their lifecycle: draft -> active -> achieved/
abandoned. Plans may only be created for active goals.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from xenopus.runtime.budget import Budget


class GoalError(Exception):
    """Raised for invalid goal construction or illegal lifecycle moves."""


class GoalPriority(StrEnum):
    """Task priority ladder used by the future scheduler (addendum 8)."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"
    BACKGROUND = "BACKGROUND"


class GoalStatus(StrEnum):
    """Goal lifecycle statuses."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ACHIEVED = "ACHIEVED"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True, slots=True)
class Goal:
    """A validated goal. Immutable once accepted by the manager.

    Contract:
        objective: one sentence definition of done (non-empty).
        constraints: hard requirements the plan must respect.
        non_goals: what explicitly must NOT change (verified at the end).
        success_criteria: machine-checkable or reviewable acceptance tests.
        risk_tolerance: how much irreversible action is permitted.
        budget: resource envelope for any plan bound to this goal.

    Invariants:
        ``created_at`` is UTC and never empty; ids are unique.
    """

    id: str
    objective: str
    constraints: list[str]
    non_goals: list[str]
    priority: GoalPriority
    success_criteria: list[str]
    risk_tolerance: str
    budget: Budget
    created_at: datetime
    status: GoalStatus

    def to_dict(self) -> dict[str, Any]:
        """Serializable representation (no sensitive data)."""
        return {
            "id": self.id,
            "objective": self.objective,
            "constraints": self.constraints,
            "non_goals": self.non_goals,
            "priority": self.priority.value,
            "success_criteria": self.success_criteria,
            "risk_tolerance": self.risk_tolerance,
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class GoalDraft:
    """User-facing input for constructing a Goal (pre-validation)."""

    objective: str
    constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    priority: GoalPriority = GoalPriority.NORMAL
    success_criteria: list[str] = field(default_factory=list)
    risk_tolerance: str = "low"
    budget: Budget = field(default_factory=Budget.unlimited)


class GoalManager:
    """Validates drafts, stores goals, and enforces lifecycle rules.

    Failure modes:
        GoalError on empty objective, missing success criteria, or illegal
        status transitions (e.g., reactivating an achieved goal).
    """

    def __init__(self) -> None:
        self._goals: dict[str, Goal] = {}

    def create(self, draft: GoalDraft) -> Goal:
        """Validate and register a goal in DRAFT status."""
        if not draft.objective.strip():
            msg = "Goal objective must be a non-empty sentence"
            raise GoalError(msg)
        if not draft.success_criteria:
            msg = "Goal must declare at least one success criterion"
            raise GoalError(msg)
        goal = Goal(
            id=f"goal-{uuid4().hex[:12]}",
            objective=draft.objective.strip(),
            constraints=list(draft.constraints),
            non_goals=list(draft.non_goals),
            priority=draft.priority,
            success_criteria=list(draft.success_criteria),
            risk_tolerance=draft.risk_tolerance,
            budget=draft.budget,
            created_at=datetime.now(UTC),
            status=GoalStatus.DRAFT,
        )
        self._goals[goal.id] = goal
        return goal

    def activate(self, goal_id: str) -> Goal:
        """Move a DRAFT goal to ACTIVE; only active goals may hold plans."""
        goal = self._require(goal_id)
        if goal.status is not GoalStatus.DRAFT:
            msg = f"Only DRAFT goals can be activated; {goal_id} is {goal.status.value}"
            raise GoalError(msg)
        activated = self._replace_status(goal, GoalStatus.ACTIVE)
        self._goals[goal_id] = activated
        return activated

    def achieve(self, goal_id: str) -> Goal:
        """Mark an ACTIVE goal ACHIEVED (terminal)."""
        goal = self._require(goal_id)
        if goal.status is not GoalStatus.ACTIVE:
            msg = f"Only ACTIVE goals can be achieved; {goal_id} is {goal.status.value}"
            raise GoalError(msg)
        achieved = self._replace_status(goal, GoalStatus.ACHIEVED)
        self._goals[goal_id] = achieved
        return achieved

    def abandon(self, goal_id: str) -> Goal:
        """Mark a DRAFT or ACTIVE goal ABANDONED (terminal)."""
        goal = self._require(goal_id)
        if goal.status is GoalStatus.ACHIEVED:
            msg = f"Achieved goals cannot be abandoned; {goal_id}"
            raise GoalError(msg)
        abandoned = self._replace_status(goal, GoalStatus.ABANDONED)
        self._goals[goal_id] = abandoned
        return abandoned

    def get(self, goal_id: str) -> Goal:
        """Return the stored goal; KeyError (as GoalError) when absent."""
        return self._require(goal_id)

    def require_active(self, goal_id: str) -> Goal:
        """Return the goal only when ACTIVE — the plan-binding precondition."""
        goal = self._require(goal_id)
        if goal.status is not GoalStatus.ACTIVE:
            msg = f"Plan binding requires an ACTIVE goal; {goal_id} is {goal.status.value}"
            raise GoalError(msg)
        return goal

    def _require(self, goal_id: str) -> Goal:
        try:
            return self._goals[goal_id]
        except KeyError as err:
            msg = f"Unknown goal: {goal_id}"
            raise GoalError(msg) from err

    @staticmethod
    def _replace_status(goal: Goal, status: GoalStatus) -> Goal:
        return Goal(
            id=goal.id,
            objective=goal.objective,
            constraints=goal.constraints,
            non_goals=goal.non_goals,
            priority=goal.priority,
            success_criteria=goal.success_criteria,
            risk_tolerance=goal.risk_tolerance,
            budget=goal.budget,
            created_at=goal.created_at,
            status=status,
        )
