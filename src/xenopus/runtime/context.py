"""Context engine: budgeted, protected message assembly.

Assembles conversation context for a completion within a token budget
(master prompt 51-52): protected segments are never dropped or truncated;
ordinary segments are dropped oldest-first when over budget. Untrusted
content (tool output, web text) is wrapped in explicit content-boundary
markers so it is never confused with system instruction (master prompt
100/131).
"""

from __future__ import annotations

from dataclasses import dataclass

from xenopus.provider.types import Message

CONTENT_BOUNDARY_OPEN = "<untrusted-content>"
CONTENT_BOUNDARY_CLOSE = "</untrusted-content>"


class ContextBudgetError(Exception):
    """Raised when even the protected segments exceed the token budget."""


def wrap_untrusted(text: str) -> str:
    """Wrap tool/web output in content-boundary markers.

    The boundary markers are a defense-in-depth signal for providers:
    content inside them is data, not instruction.
    """
    return f"{CONTENT_BOUNDARY_OPEN}\n{text}\n{CONTENT_BOUNDARY_CLOSE}"


@dataclass(frozen=True, slots=True)
class ContextSegment:
    """One candidate segment for context assembly.

    Contract:
        protected: True segments are pinned (system prompt, goal,
            constraints, decisions, current state); False segments are
            droppable history when the budget is tight.
        est_tokens: cost estimate; the engine counts with the same
            four-chars-per-token heuristic used across the runtime.
    """

    message: Message
    protected: bool
    est_tokens: int

    @classmethod
    def from_message(cls, message: Message, *, protected: bool = False) -> ContextSegment:
        """Build a segment with an estimated token cost."""
        return cls(
            message=message,
            protected=protected,
            est_tokens=max(1, len(message.content) // 4),
        )


class ContextEngine:
    """Assembles messages under a token budget.

    Deterministic rules:
        1. Protected segments always survive, in their original order.
        2. Unprotected segments fill the remaining budget newest-first
           (recent context is more valuable than old history).
        3. If protected segments alone exceed the budget, raise
           ContextBudgetError — shrinking the system prompt silently is
           forbidden (fail loudly instead, Protocol v9 P5).
    """

    def assemble(self, segments: list[ContextSegment], *, token_budget: int) -> tuple[Message, ...]:
        """Return the messages to send, ordered oldest-first as sent."""
        if token_budget < 1:
            msg = "token_budget must be >= 1"
            raise ValueError(msg)

        protected_total = sum(s.est_tokens for s in segments if s.protected)
        if protected_total > token_budget:
            msg = (
                f"protected context ({protected_total} est. tokens) exceeds "
                f"budget ({token_budget}); refusing to drop protected segments"
            )
            raise ContextBudgetError(msg)

        # Protected segments in original order (oldest-first stability).
        protected = [s for s in segments if s.protected]
        # Droppable segments, newest-first for greedy fill.
        droppable = [s for s in segments if not s.protected]
        remaining = token_budget - protected_total
        kept: list[ContextSegment] = []
        for segment in reversed(droppable):  # newest first
            if remaining <= 0:
                break
            if segment.est_tokens <= remaining:
                kept.append(segment)
                remaining -= segment.est_tokens

        kept_segments = protected + list(reversed(kept))  # restore oldest-first
        kept_segments.sort(key=lambda s: segments.index(s))  # original order
        return tuple(s.message for s in kept_segments)
