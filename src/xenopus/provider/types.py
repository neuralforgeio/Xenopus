"""Provider-layer core types: requests, responses, usage, capabilities.

These types form the stable contract between the runtime and any model
provider. Providers are data + behavior behind the ModelProvider protocol;
the runtime never imports provider-specific modules (ADR-005).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProviderError(Exception):
    """Base class for provider failures.

    Every concrete error carries a ``permanent`` flag: permanent failures
    must not be retried with the same request (e.g., auth, bad schema);
    transient failures may be retried under the retry policy.
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class AuthError(ProviderError):
    """Authentication/authorization failure — permanent."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class RateLimitError(ProviderError):
    """Rate limit hit — transient; cooldown applies before retry."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message, permanent=False)
        self.retry_after_seconds = retry_after_seconds


class ProviderUnavailableError(ProviderError):
    """Provider unreachable or returned a server error — transient."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=False)


class SchemaError(ProviderError):
    """The provider response violated the expected schema — permanent."""

    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class Role(StrEnum):
    """Chat message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class Message:
    """One conversation message.

    Contract:
        role: SYSTEM messages come only from trusted runtime assembly;
            untrusted content must never be elevated to SYSTEM (master
            prompt 131 — content boundary).
        content: message text; may be empty for tool-result scaffolding.
    """

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """A single completion request against one provider/model.

    Invariants:
        messages is never empty; temperature and max_output_tokens are
        bounded by the provider adapter.
    """

    model: str
    messages: tuple[Message, ...]
    temperature: float = 1.0
    max_output_tokens: int | None = None
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not self.messages:
            msg = "CompletionRequest requires at least one message"
            raise ValueError(msg)
        if self.temperature < 0:
            msg = f"temperature must be >= 0, got {self.temperature}"
            raise ValueError(msg)
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            msg = "max_output_tokens must be >= 1 when provided"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting for one completion.

    Contract: counts are non-negative; total_tokens == prompt + completion
    when the provider reports all three (otherwise the adapter sums).
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if self.prompt_tokens < 0 or self.completion_tokens < 0:
            msg = "token counts must be non-negative"
            raise ValueError(msg)
        if self.total_tokens == 0:
            object.__setattr__(
                self,
                "total_tokens",
                self.prompt_tokens + self.completion_tokens,
            )


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    """A successful completion result.

    Contract:
        model: the model that actually served the request (may differ
            from the requested one after fallback routing).
        content: assistant text; never None (errors raise instead).
    """

    content: str
    model: str
    usage: Usage
    provider: str
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelCapability:
    """Static capability descriptor for one provider/model pair.

    Used by the registry and router (master prompt 62): costs are decimal
    USD per 1M tokens; booleans are declared capabilities, verified at
    provider registration time by a smoke request when available.
    """

    provider: str
    model: str
    input_cost_per_mtok: float
    output_cost_per_mtok: float
    context_window: int
    supports_tools: bool = False
    supports_vision: bool = False
    tags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.input_cost_per_mtok < 0 or self.output_cost_per_mtok < 0:
            msg = "costs must be non-negative"
            raise ValueError(msg)
        if self.context_window < 1:
            msg = "context_window must be >= 1"
            raise ValueError(msg)

    def key(self) -> str:
        """Stable registry key: 'provider/model'."""
        return f"{self.provider}/{self.model}"
