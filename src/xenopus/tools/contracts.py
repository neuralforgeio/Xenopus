"""Tool contracts: the schema every tool must declare before registration.

Every tool registers a contract describing its inputs, outputs, side
effects, permissions, risk, timeout, and failure modes (master prompt 41).
The gateway refuses unregistered or under-specified tools — no ad-hoc
callable ever executes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class ToolError(Exception):
    """Base class for tool execution failures."""


class ToolTimeoutError(ToolError):
    """The tool exceeded its declared timeout."""


class ToolValidationError(ToolError):
    """Arguments failed contract validation — permanent, do not retry."""


class ToolInputError(ToolError):
    """The tool rejected the input (bad path, missing file) — permanent."""


class RiskLevel(IntEnum):
    """Ordered risk scale used by the risk engine (higher = riskier)."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Full declaration of one tool.

    Contract:
        name: unique registry key.
        description: what the tool does, in user vocabulary.
        input_schema: JSON-schema-shaped dict; validated before dispatch.
        side_effects: 'none' for pure reads; anything else declares mutation.
        required_permissions: permission dimensions the caller must hold.
        risk: static risk level; the risk engine may raise it dynamically.
        timeout_seconds: hard bound per invocation.
        idempotent: True when retried execution is safe (master prompt 109).
        failure_modes: human-readable enumeration the planner can consult.

    Invariants:
        name is non-empty and unique per registry; timeout_seconds > 0.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    side_effects: str
    required_permissions: frozenset[str]
    risk: RiskLevel
    timeout_seconds: float
    idempotent: bool
    failure_modes: tuple[str, ...] = ()
    output_notes: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "tool name must be non-empty"
            raise ValueError(msg)
        if self.timeout_seconds <= 0:
            msg = f"timeout_seconds must be > 0 for tool {self.name!r}"
            raise ValueError(msg)


ToolHandler = Callable[[dict[str, Any]], Awaitable["ToolResult"]]


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Structured outcome of one tool invocation.

    Contract:
        ok: success flag — a tool NEVER throws to report ordinary failure;
            it returns ok=False with an error code (the gateway converts
            exceptions to this shape).
        data: JSON-serializable payload on success.
        error_code: stable machine-readable failure id on failure.
        error_message: human-readable explanation (no secrets, no raw
            untrusted payloads reflected back).
        duration_seconds: wall time consumed by the invocation.
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    duration_seconds: float = 0.0

    @classmethod
    def success(cls, data: dict[str, Any], duration_seconds: float = 0.0) -> ToolResult:
        """Build a successful result."""
        return cls(ok=True, data=data, duration_seconds=duration_seconds)

    @classmethod
    def failure(
        cls,
        error_code: str,
        error_message: str,
        duration_seconds: float = 0.0,
    ) -> ToolResult:
        """Build a failed result with a stable error code."""
        return cls(
            ok=False,
            error_code=error_code,
            error_message=error_message,
            duration_seconds=duration_seconds,
        )
