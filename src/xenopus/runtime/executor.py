"""Executor: bounded-retry tool execution with journaling.

Runs approved invocations through the gateway, applies the declared
retry policy (never retrying permanent failures — master prompt 109),
journals every attempt, and records observations for the verifier.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from xenopus.persistence.journal import EventJournal
from xenopus.runtime.budget import RetryPolicy
from xenopus.runtime.events import Event, EventType
from xenopus.runtime.observer import ObservationLog
from xenopus.tools.contracts import ToolResult
from xenopus.tools.gateway import ToolGateway

PERMANENT_ERROR_CODES = frozenset(
    {
        "unknown_tool",
        "tool_quarantined",
        "permission_denied",
        "risk_denied",
        "approval_required",
        "approval_invalid",
        "not_found",
        "tool_validation_error",
    }
)


class Executor:
    """Executes tool calls with bounded retries and full journaling.

    Contract:
        execute(): runs one invocation, retrying transient failures up
        to the policy bound. Every attempt journals TOOL_STARTED /
        TOOL_COMPLETED / TOOL_FAILED; every result is observed.
    """

    def __init__(
        self,
        *,
        gateway: ToolGateway,
        journal: EventJournal,
        observations: ObservationLog,
    ) -> None:
        self._gateway = gateway
        self._journal = journal
        self._observations = observations

    async def execute(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        correlation_id: str,
        retry_policy: RetryPolicy | None = None,
        approval_id: str | None = None,
        path_policy: object | None = None,
    ) -> ToolResult:
        """Invoke ``tool`` with bounded retries; returns the final result."""
        policy = retry_policy or RetryPolicy(max_attempts=1)
        result: ToolResult | None = None
        for attempt in range(1, policy.max_attempts + 1):
            self._journal.append(
                Event(
                    type=EventType.TOOL_STARTED,
                    correlation_id=correlation_id,
                    payload={"tool": tool, "attempt": attempt},
                )
            )
            # Gateway dispatch is synchronous; run it off the event loop
            # so async callers stay responsive (no blocking on the loop).
            result = await asyncio.to_thread(
                self._gateway.invoke,
                tool,
                arguments,
                correlation_id=correlation_id,
                approval_id=approval_id,
                path_policy=path_policy,
            )
            self._observations.record(
                kind="tool_result",
                content={
                    "tool": tool,
                    "attempt": attempt,
                    "ok": result.ok,
                    "error_code": result.error_code,
                    "data": result.data,
                },
                correlation_id=correlation_id,
                created_at=datetime.now(UTC).isoformat(),
            )
            event_type = EventType.TOOL_COMPLETED if result.ok else EventType.TOOL_FAILED
            self._journal.append(
                Event(
                    type=event_type,
                    correlation_id=correlation_id,
                    payload={
                        "tool": tool,
                        "attempt": attempt,
                        "error_code": result.error_code,
                    },
                )
            )
            if result.ok:
                return result
            if result.error_code in PERMANENT_ERROR_CODES:
                return result  # permanent failures: never retried
            if attempt < policy.max_attempts:
                await asyncio.sleep(policy.backoff_seconds(attempt))
        return result  # type: ignore[return-value]  # loop ran >= once
