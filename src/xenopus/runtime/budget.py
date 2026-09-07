"""Runtime budget primitives: bounded resources for goals, plans, and tasks.

Every budgeted object must declare its limits up front so execution can
enforce them (backpressure and cost control, master prompt 51/103).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BudgetExceededError(Exception):
    """Raised when an operation would exceed a declared budget limit.

    The message must name the exhausted dimension (token, time, cost, tool
    calls, or child agents) and the current usage so the caller can decide
    whether to simplify, stop, or escalate.
    """


class BudgetDimension(StrEnum):
    """The five bounded dimensions every budgeted actor must declare."""

    TOKENS = "tokens"
    TIME = "time"
    COST = "cost"
    TOOL_CALLS = "tool_calls"
    CHILD_AGENTS = "child_agents"


@dataclass(frozen=True, slots=True)
class Budget:
    """Immutable resource budget for a goal, plan, or agent task.

    Contract:
        limits: maximum allowed value per dimension; negative or missing
            entries are treated as unlimited (None) except where a caller
            requires a mandatory dimension.
        Remaining budget is computed by the consumer against a usage ledger;
        this type carries policy, not mutable accounting state.

    Raises:
        ValueError: if any limit is negative.
    """

    limits: dict[BudgetDimension, int | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for dimension, value in self.limits.items():
            if value is not None and value < 0:
                msg = f"Budget limit for {dimension.value} must be >= 0, got {value}"
                raise ValueError(msg)

    def limit_for(self, dimension: BudgetDimension) -> int | None:
        """Return the limit for ``dimension`` (None = unlimited)."""
        return self.limits.get(dimension)

    @classmethod
    def unlimited(cls) -> Budget:
        """A budget with no limits; useful for tests and local tools."""
        return cls(limits={})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry policy for a task or tool invocation.

    Contract:
        max_attempts: total attempts including the first (>= 1).
        backoff_base_seconds: exponential backoff base (>= 0).
        Requires idempotency from the retried operation (master prompt 109).

    Raises:
        ValueError: if max_attempts < 1 or backoff_base_seconds < 0.
    """

    max_attempts: int = 3
    backoff_base_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = f"max_attempts must be >= 1, got {self.max_attempts}"
            raise ValueError(msg)
        if self.backoff_base_seconds < 0:
            msg = f"backoff_base_seconds must be >= 0, got {self.backoff_base_seconds}"
            raise ValueError(msg)

    def backoff_seconds(self, attempt: int) -> float:
        """Backoff delay before retry ``attempt`` (1-indexed after a failure)."""
        delay: float = self.backoff_base_seconds * (2 ** max(0, attempt - 1))
        return delay
