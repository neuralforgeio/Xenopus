"""Provider registry: capability catalog for registered providers/models.

The registry is the searchable catalog the router consults (master prompt
62: capability registry). Registration is explicit; nothing is discovered
implicitly from the environment.
"""

from __future__ import annotations

from xenopus.provider.protocol import ModelProvider
from xenopus.provider.types import ModelCapability


class ProviderRegistryError(Exception):
    """Raised for registry misuse: duplicates, unknown lookups."""


class ProviderRegistry:
    """Catalog of provider instances and their model capabilities.

    Contract:
        register(): adds one provider instance + one or more capabilities;
            duplicate provider names or capability keys are rejected.
        provider_for(): returns the registered instance for a capability key.

    Invariants:
        every capability key 'provider/model' maps to a registered
        provider instance with the same name.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._capabilities: dict[str, ModelCapability] = {}

    def register(
        self,
        provider: ModelProvider,
        capabilities: list[ModelCapability],
    ) -> None:
        """Register a provider with at least one capability entry."""
        if provider.name in self._providers:
            msg = f"provider already registered: {provider.name!r}"
            raise ProviderRegistryError(msg)
        if not capabilities:
            msg = "registration requires at least one capability"
            raise ProviderRegistryError(msg)
        for cap in capabilities:
            if cap.provider != provider.name:
                msg = (
                    f"capability provider {cap.provider!r} does not match "
                    f"registered name {provider.name!r}"
                )
                raise ProviderRegistryError(msg)
            if cap.key() in self._capabilities:
                msg = f"capability already registered: {cap.key()!r}"
                raise ProviderRegistryError(msg)
        self._providers[provider.name] = provider
        for cap in capabilities:
            self._capabilities[cap.key()] = cap

    def capability(self, key: str) -> ModelCapability:
        """Look up one capability by 'provider/model'; KeyError -> typed."""
        try:
            return self._capabilities[key]
        except KeyError as err:
            msg = f"unknown capability: {key!r}"
            raise ProviderRegistryError(msg) from err

    def provider_for(self, key: str) -> ModelProvider:
        """Return the provider instance backing a capability key."""
        cap = self.capability(key)
        return self._providers[cap.provider]

    def capabilities(self) -> list[ModelCapability]:
        """All capabilities in deterministic (sorted) order."""
        return [self._capabilities[k] for k in sorted(self._capabilities)]

    def has(self, key: str) -> bool:
        """Membership check without exceptions."""
        return key in self._capabilities
