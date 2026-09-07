"""Registry, health tracker, and router tests (deterministic, offline)."""

import pytest

from xenopus.provider.echo import EchoProvider
from xenopus.provider.health import ProviderHealthTracker
from xenopus.provider.registry import ProviderRegistry, ProviderRegistryError
from xenopus.provider.router import (
    HIGH_QUALITY_TAG,
    LOCAL_TAG,
    ModelRouter,
    RoutingError,
    RoutingPolicy,
)
from xenopus.provider.types import (
    ModelCapability,
    ProviderUnavailableError,
    RateLimitError,
)


def cap(
    provider: str,
    model: str,
    *,
    input_cost_per_mtok: float = 1.0,
    output_cost_per_mtok: float = 2.0,
    context_window: int = 8000,
    tags: frozenset[str] = frozenset(),
) -> ModelCapability:
    return ModelCapability(
        provider=provider,
        model=model,
        input_cost_per_mtok=input_cost_per_mtok,
        output_cost_per_mtok=output_cost_per_mtok,
        context_window=context_window,
        tags=tags,
    )


class NamedEcho(EchoProvider):
    """EchoProvider variant with an overridable registry name (test seam)."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    @property
    def name(self) -> str:
        """Registry name supplied at construction."""
        return self._name


def make_stack() -> tuple[ProviderRegistry, ProviderHealthTracker, ModelRouter]:
    registry = ProviderRegistry()
    health = ProviderHealthTracker(clock=lambda: 0.0)
    registry.register(
        NamedEcho("echo"),
        [cap("echo", "echo-1", tags=frozenset({LOCAL_TAG}))],
    )
    registry.register(
        NamedEcho("cloud"),
        [cap("cloud", "big-1", tags=frozenset({HIGH_QUALITY_TAG}))],
    )
    return registry, health, ModelRouter(registry, health)


class TestRegistry:
    def test_register_and_lookup(self) -> None:
        registry, _, _ = make_stack()
        assert registry.has("echo/echo-1")
        assert registry.capability("cloud/big-1").context_window == 8000

    def test_duplicate_provider_rejected(self) -> None:
        registry = ProviderRegistry()
        registry.register(NamedEcho("echo"), [cap("echo", "m")])
        with pytest.raises(ProviderRegistryError, match="already registered"):
            registry.register(NamedEcho("echo"), [cap("echo", "other")])

    def test_capability_name_mismatch_rejected(self) -> None:
        registry = ProviderRegistry()
        with pytest.raises(ProviderRegistryError, match="does not match"):
            registry.register(NamedEcho("echo"), [cap("other", "m")])

    def test_unknown_capability_raises(self) -> None:
        registry, _, _ = make_stack()
        with pytest.raises(ProviderRegistryError, match="unknown capability"):
            registry.capability("ghost/m")

    def test_capabilities_sorted(self) -> None:
        registry, _, _ = make_stack()
        keys = [c.key() for c in registry.capabilities()]
        assert keys == sorted(keys)


class TestHealth:
    def test_starts_healthy(self) -> None:
        _, health, _ = make_stack()
        assert health.is_available("echo") is True
        assert health.snapshot("echo").state == "healthy"

    def test_rate_limit_moves_to_cooldown(self) -> None:
        _, health, _ = make_stack()
        health.record_failure("echo", RateLimitError("rl", retry_after_seconds=10))
        assert health.is_available("echo") is False
        assert health.snapshot("echo").state == "rate-limited"

    def test_cooldown_self_heals_after_deadline(self) -> None:
        clock = {"now": 0.0}
        health = ProviderHealthTracker(clock=lambda: clock["now"])
        health.record_failure("echo", RateLimitError("rl", retry_after_seconds=10))
        assert health.is_available("echo") is False
        clock["now"] = 10.0
        assert health.is_available("echo") is True
        assert health.snapshot("echo").state == "healthy"

    def test_transient_failures_below_threshold_still_routable(self) -> None:
        _, health, _ = make_stack()
        health.record_failure("echo", ProviderUnavailableError("x"))
        health.record_failure("echo", ProviderUnavailableError("x"))
        assert health.is_available("echo") is True  # threshold is 3

    def test_threshold_trips_cooldown(self) -> None:
        _, health, _ = make_stack()
        for _ in range(3):
            health.record_failure("echo", ProviderUnavailableError("x"))
        assert health.is_available("echo") is False
        assert health.snapshot("echo").state == "cooldown"

    def test_success_resets_counters(self) -> None:
        _, health, _ = make_stack()
        health.record_failure("echo", ProviderUnavailableError("x"))
        health.record_success("echo")
        assert health.snapshot("echo").consecutive_failures == 0
        assert health.is_available("echo") is True


class TestRouter:
    def test_local_first_prefers_local_tag(self) -> None:
        _, _, router = make_stack()
        (best, _) = router.select(RoutingPolicy.LOCAL_FIRST)
        assert best.provider == "echo"

    def test_quality_first_prefers_quality_tag(self) -> None:
        _, _, router = make_stack()
        (best, _) = router.select(RoutingPolicy.QUALITY_FIRST)
        assert best.provider == "cloud"

    def test_cost_first_orders_by_price(self) -> None:
        registry = ProviderRegistry()
        health = ProviderHealthTracker(clock=lambda: 0.0)
        registry.register(
            NamedEcho("cheap"),
            [cap("cheap", "c1", input_cost_per_mtok=0.1, output_cost_per_mtok=0.1)],
        )
        registry.register(
            NamedEcho("pricey"),
            [cap("pricey", "p1", input_cost_per_mtok=10, output_cost_per_mtok=10)],
        )
        router = ModelRouter(registry, health)
        assert router.select(RoutingPolicy.COST_FIRST)[0].provider == "cheap"

    def test_unhealthy_provider_excluded(self) -> None:
        _, health, router = make_stack()
        health.record_failure("cloud", RateLimitError("rl"))
        candidates = router.select(RoutingPolicy.QUALITY_FIRST)
        assert all(c.provider != "cloud" for c in candidates)
        assert candidates[0].provider == "echo"

    def test_all_unavailable_raises_routing_error(self) -> None:
        _, health, router = make_stack()
        health.record_failure("echo", RateLimitError("rl"))
        health.record_failure("cloud", RateLimitError("rl"))
        with pytest.raises(RoutingError, match="no available provider"):
            router.select(RoutingPolicy.LOCAL_FIRST)

    def test_exclude_skips_specific_key(self) -> None:
        _, _, router = make_stack()
        candidates = router.select(RoutingPolicy.LOCAL_FIRST, exclude=frozenset({"echo/echo-1"}))
        assert all(c.key() != "echo/echo-1" for c in candidates)

    def test_selection_is_deterministic(self) -> None:
        _, _, router = make_stack()
        a = router.select(RoutingPolicy.QUALITY_FIRST)
        b = router.select(RoutingPolicy.QUALITY_FIRST)
        assert [c.key() for c in a] == [c.key() for c in b]
