"""Context engine tests: budget enforcement, protected segments, boundaries."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xenopus.provider.types import Message, Role
from xenopus.runtime.context import (
    ContextBudgetError,
    ContextEngine,
    ContextSegment,
    wrap_untrusted,
)


def seg(content: str, protected: bool = False) -> ContextSegment:
    return ContextSegment.from_message(
        Message(role=Role.USER, content=content), protected=protected
    )


class TestContextEngine:
    def test_within_budget_keeps_everything_in_order(self) -> None:
        segments = [seg("a" * 400), seg("b" * 400), seg("c" * 400)]
        messages = ContextEngine().assemble(segments, token_budget=10_000)
        assert [m.content[0] for m in messages] == ["a", "b", "c"]

    def test_over_budget_drops_oldest_droppable_first(self) -> None:
        segments = [seg("a" * 400), seg("b" * 400), seg("c" * 400)]
        # Budget for roughly two segments: 'a' (oldest) must be dropped.
        messages = ContextEngine().assemble(segments, token_budget=210)
        assert [m.content[0] for m in messages] == ["b", "c"]

    def test_protected_segments_survive(self) -> None:
        segments = [seg("system", protected=True), seg("old history"), seg("recent")]
        # Budget 2: protected 'system' (1) + newest 'recent' (1) fit;
        # 'old history' (2 est) is dropped oldest-first.
        messages = ContextEngine().assemble(segments, token_budget=2)
        contents = [m.content for m in messages]
        assert "system" in contents
        assert "recent" in contents
        assert "old history" not in contents  # droppable, oldest first out

    def test_protected_over_budget_raises(self) -> None:
        segments = [seg("s" * 1000, protected=True)]
        with pytest.raises(ContextBudgetError, match="refusing to drop"):
            ContextEngine().assemble(segments, token_budget=10)

    def test_zero_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="token_budget"):
            ContextEngine().assemble([], token_budget=0)

    def test_untrusted_wrap_adds_boundaries(self) -> None:
        wrapped = wrap_untrusted("tool output here")
        assert wrapped.startswith("<untrusted-content>")
        assert wrapped.endswith("</untrusted-content>")
        assert "tool output here" in wrapped


class TestContextProperty:
    @given(
        st.lists(
            st.tuples(st.integers(min_value=1, max_value=100), st.booleans()),
            min_size=0,
            max_size=15,
        ),
        st.integers(min_value=1, max_value=2_000),
    )
    def test_assembly_never_exceeds_budget_or_drops_protected(
        self, spec: list[tuple[int, bool]], budget: int
    ) -> None:
        """For any segments and budget: protected always kept, total <= budget
        (unless protected alone overflow, which must raise instead)."""
        engine = ContextEngine()
        segments = [
            ContextSegment.from_message(
                Message(role=Role.USER, content="x" * size), protected=protected
            )
            for size, protected in spec
        ]
        protected_total = sum(s.est_tokens for s in segments if s.protected)
        try:
            messages = engine.assemble(segments, token_budget=budget)
        except ContextBudgetError:
            assert protected_total > budget
            return
        kept = {m.content for m in messages}
        for s in segments:
            if s.protected:
                assert s.message.content in kept
        total = sum(len(m.content) // 4 for m in messages)
        assert total <= budget
