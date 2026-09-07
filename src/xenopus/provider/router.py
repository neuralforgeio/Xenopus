"""Model router: deterministic selection + fallback ordering.

Policies (master prompt 63): quality-first, cost-first, latency-first,
local-first, privacy-first. The router picks candidates by policy,
skips providers the health tracker marks unavailable, and returns a
ranked fallback chain — deterministic: same inputs, same order.
"""

from __future__ import annotations

from enum import StrEnum

from xenopus.provider.health import ProviderHealthTracker
from xenopus.provider.registry import ProviderRegistry
from xenopus.provider.types import ModelCapability


class RoutingError(Exception):
    """Raised when no candidate satisfies the request."""


class RoutingPolicy(StrEnum):
    """Selection policies; each maps to a deterministic sort key."""

    QUALITY_FIRST = "quality-first"
    COST_FIRST = "cost-first"
    LATENCY_FIRST = "latency-first"
    LOCAL_FIRST = "local-first"
    PRIVACY_FIRST = "privacy-first"


# Tag vocabularies consumed by LOCAL_FIRST / PRIVACY_FIRST (declared by
# capability registration; nothing is inferred at runtime).
LOCAL_TAG = "local"
PRIVATE_TAG = "private"
LOW_LATENCY_TAG = "low-latency"
HIGH_QUALITY_TAG = "high-quality"


class ModelRouter:
    """Selects and ranks provider/model candidates.

    Contract:
        select(): returns candidates sorted by policy, health-filtered.
            Raises RoutingError when the filtered set is empty.
        The router never calls providers — pure selection logic.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        health: ProviderHealthTracker,
    ) -> None:
        self._registry = registry
        self._health = health

    def select(
        self,
        policy: RoutingPolicy,
        *,
        task_tags: frozenset[str] = frozenset(),
        exclude: frozenset[str] = frozenset(),
    ) -> list[ModelCapability]:
        """Return health-filtered capabilities ordered by policy.

        ``exclude`` removes specific capability keys (used by callers to
        skip a provider that just failed before retrying).
        """
        candidates = [
            cap
            for cap in self._registry.capabilities()
            if cap.key() not in exclude and self._health.is_available(cap.provider)
        ]
        if not candidates:
            msg = "no available provider satisfies the routing request"
            raise RoutingError(msg)

        def sort_key(cap: ModelCapability) -> tuple[float, str] | tuple[int, float, str]:
            if policy is RoutingPolicy.COST_FIRST:
                return (
                    cap.input_cost_per_mtok + cap.output_cost_per_mtok,
                    cap.key(),
                )
            if policy is RoutingPolicy.LOCAL_FIRST:
                return (
                    0 if LOCAL_TAG in cap.tags else 1,
                    cap.input_cost_per_mtok,
                    cap.key(),
                )
            if policy is RoutingPolicy.PRIVACY_FIRST:
                return (
                    0 if PRIVATE_TAG in cap.tags else 1,
                    cap.input_cost_per_mtok,
                    cap.key(),
                )
            if policy is RoutingPolicy.LATENCY_FIRST:
                return (
                    0 if LOW_LATENCY_TAG in cap.tags else 1,
                    cap.input_cost_per_mtok,
                    cap.key(),
                )
            # QUALITY_FIRST (default)
            return (
                0 if HIGH_QUALITY_TAG in cap.tags else 1,
                -(cap.context_window),
                cap.key(),
            )

        return sorted(candidates, key=sort_key)
