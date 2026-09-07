"""Provider health tracking: circuit-breaker states with cooldown.

Health states (master prompt 64): healthy, rate-limited, unavailable,
cooldown. The tracker records failures; the router consults it before
selection. Deterministic time via injected clock (testable, no sleeps).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from xenopus.provider.types import ProviderError, RateLimitError

DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 30.0
DEFAULT_UNAVAILABLE_COOLDOWN_SECONDS = 60.0
DEFAULT_FAILURE_THRESHOLD = 3


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Point-in-time health of one provider.

    Contract: state is one of 'healthy', 'rate-limited', 'unavailable',
    'cooldown'. cooldown_until is a monotonic-clock seconds value or None.
    """

    provider: str
    state: str
    cooldown_until: float | None
    consecutive_failures: int


class ProviderHealthError(Exception):
    """Raised for tracker misuse (unknown provider)."""


class ProviderHealthTracker:
    """Circuit-breaker bookkeeping per provider name.

    Rules (deterministic, no heuristics):
        - A RateLimitError moves the provider to 'rate-limited' with the
          advertised Retry-After (or the default cooldown).
        - Consecutive transient failures >= threshold move the provider
          to 'cooldown' for the unavailable-cooldown window.
        - A success resets counters and state to 'healthy'.
        - Permanent errors (auth/schema) are NOT tracked here — they are
          configuration bugs the router must exclude permanently.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        rate_limit_cooldown: float = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS,
        unavailable_cooldown: float = DEFAULT_UNAVAILABLE_COOLDOWN_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    ) -> None:
        if failure_threshold < 1:
            msg = "failure_threshold must be >= 1"
            raise ValueError(msg)
        if rate_limit_cooldown < 0 or unavailable_cooldown < 0:
            msg = "cooldowns must be non-negative"
            raise ValueError(msg)
        self._clock = clock
        self._rate_limit_cooldown = rate_limit_cooldown
        self._unavailable_cooldown = unavailable_cooldown
        self._threshold = failure_threshold
        self._states: dict[str, HealthSnapshot] = {}

    def _snapshot(self, provider: str) -> HealthSnapshot:
        return self._states.get(
            provider,
            HealthSnapshot(
                provider=provider,
                state="healthy",
                cooldown_until=None,
                consecutive_failures=0,
            ),
        )

    def record_failure(self, provider: str, error: ProviderError) -> HealthSnapshot:
        """Record one failure and update the provider's health state."""
        current = self._snapshot(provider)
        if isinstance(error, RateLimitError):
            cooldown = (
                error.retry_after_seconds
                if error.retry_after_seconds is not None
                else self._rate_limit_cooldown
            )
            updated = HealthSnapshot(
                provider=provider,
                state="rate-limited",
                cooldown_until=self._clock() + cooldown,
                consecutive_failures=current.consecutive_failures + 1,
            )
        elif error.permanent:
            # Permanent errors leave state unchanged; the router excludes
            # permanently broken providers at registration/lookup time.
            return current
        else:
            failures = current.consecutive_failures + 1
            if failures >= self._threshold:
                updated = HealthSnapshot(
                    provider=provider,
                    state="cooldown",
                    cooldown_until=self._clock() + self._unavailable_cooldown,
                    consecutive_failures=failures,
                )
            else:
                updated = HealthSnapshot(
                    provider=provider,
                    state="unavailable",
                    cooldown_until=None,
                    consecutive_failures=failures,
                )
        self._states[provider] = updated
        return updated

    def record_success(self, provider: str) -> HealthSnapshot:
        """Record one success; resets counters and returns to healthy."""
        updated = HealthSnapshot(
            provider=provider,
            state="healthy",
            cooldown_until=None,
            consecutive_failures=0,
        )
        self._states[provider] = updated
        return updated

    def is_available(self, provider: str) -> bool:
        """True when the provider may accept traffic right now.

        A provider in cooldown/rate-limit becomes available again once
        the monotonic clock passes its cooldown deadline (self-healing,
        no external reset call needed).
        """
        snap = self._snapshot(provider)
        if snap.state == "healthy":
            return True
        if snap.state in ("rate-limited", "cooldown") and snap.cooldown_until is not None:
            if self._clock() >= snap.cooldown_until:
                # Cooldown elapsed: restore availability lazily.
                self._states[provider] = HealthSnapshot(
                    provider=provider,
                    state="healthy",
                    cooldown_until=None,
                    consecutive_failures=0,
                )
                return True
            return False
        # 'unavailable' with failures below threshold: still routable.
        return True

    def snapshot(self, provider: str) -> HealthSnapshot:
        """Current snapshot without availability side effects."""
        return self._snapshot(provider)
