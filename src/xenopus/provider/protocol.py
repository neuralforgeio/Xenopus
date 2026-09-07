"""The ModelProvider protocol: the single seam between runtime and models.

Any provider integration (OpenAI-compatible, future native SDKs) implements
this async protocol. The runtime depends only on this protocol — never on
a provider module (ADR-005).
"""

from typing import Protocol

from xenopus.provider.types import CompletionRequest, CompletionResponse


class ModelProvider(Protocol):
    """Async completion interface for one provider.

    Contract:
        name: stable provider identifier (registry key part).
        complete(): performs one completion; raises ProviderError
            subclasses on failure — never returns partial junk.

    Failure modes (callers must handle):
        AuthError (permanent), RateLimitError (cooldown then retry),
        ProviderUnavailableError (fallback/retry), SchemaError (permanent).
    """

    @property
    def name(self) -> str:
        """Stable provider name used in registry keys and logs."""
        ...

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Serve one completion request or raise a ProviderError."""
        ...
