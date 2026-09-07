"""Budget and retry-policy tests including property invariants."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xenopus.runtime.budget import (
    Budget,
    BudgetDimension,
    BudgetExceededError,
    RetryPolicy,
)


class TestBudget:
    def test_unlimited_budget_has_no_limits(self) -> None:
        assert Budget.unlimited().limit_for(BudgetDimension.TOKENS) is None

    def test_limit_lookup(self) -> None:
        budget = Budget(limits={BudgetDimension.TOOL_CALLS: 10})
        assert budget.limit_for(BudgetDimension.TOOL_CALLS) == 10
        assert budget.limit_for(BudgetDimension.COST) is None

    @given(
        st.sampled_from(BudgetDimension),
        st.integers(min_value=0, max_value=1_000_000),
    )
    def test_any_nonnegative_limit_is_valid(self, dim: BudgetDimension, value: int) -> None:
        assert Budget(limits={dim: value}).limit_for(dim) == value

    @given(st.sampled_from(BudgetDimension), st.integers(max_value=-1))
    def test_negative_limit_always_rejected(self, dim: BudgetDimension, value: int) -> None:
        with pytest.raises(ValueError, match="must be >= 0"):
            Budget(limits={dim: value})

    def test_budget_exceeded_error_is_importable_contract(self) -> None:
        """The exception type exists for Phase 4+ executors to raise."""
        assert issubclass(BudgetExceededError, Exception)


class TestRetryPolicy:
    def test_defaults(self) -> None:
        policy = RetryPolicy()
        assert policy.max_attempts == 3
        assert policy.backoff_base_seconds == 1.0

    def test_backoff_growth(self) -> None:
        policy = RetryPolicy(max_attempts=5, backoff_base_seconds=0.5)
        assert policy.backoff_seconds(1) == 0.5
        assert policy.backoff_seconds(2) == 1.0
        assert policy.backoff_seconds(3) == 2.0

    @given(st.integers(min_value=1, max_value=50))
    def test_any_positive_attempts_valid(self, attempts: int) -> None:
        assert RetryPolicy(max_attempts=attempts).max_attempts == attempts

    @given(st.integers(max_value=0))
    def test_zero_or_negative_attempts_rejected(self, attempts: int) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            RetryPolicy(max_attempts=attempts)

    @given(st.floats(min_value=0, max_value=100))
    def test_backoff_never_negative(self, base: float) -> None:
        policy = RetryPolicy(backoff_base_seconds=base)
        assert policy.backoff_seconds(1) >= 0
