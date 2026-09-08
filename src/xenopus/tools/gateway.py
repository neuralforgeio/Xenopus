"""Tool gateway: the single door every tool invocation passes through.

Pipeline (master prompt 39): permission -> risk -> approval -> execute ->
observe. No component may call a tool handler directly; the gateway is
the enforcement point for contracts, permissions, risk outcomes, and
approval validation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from xenopus.runtime.approval import ApprovalLedger, action_hash
from xenopus.runtime.permission import (
    PermissionDecision,
    PermissionEngine,
    PermissionRequest,
)
from xenopus.runtime.risk import RiskEngine, RiskFactors, RiskOutcome
from xenopus.tools.contracts import RiskLevel, ToolResult
from xenopus.tools.registry import ToolQuarantineError, ToolRegistry


async def _materialize(awaitable: Awaitable[ToolResult]) -> ToolResult:
    """Bridge an Awaitable into asyncio.run (typing shim for mypy strict)."""
    return await awaitable


class GatewayError(Exception):
    """Raised for wiring errors (unknown tool, missing approval callback)."""


@dataclass(frozen=True, slots=True)
class GatewayDecision:
    """Why the gateway allowed, blocked, or escalated an invocation."""

    permission: PermissionDecision
    risk_outcome: RiskOutcome
    approval_request_id: str | None
    approved: bool


class ToolGateway:
    """Enforcement pipeline for every tool call.

    Contract:
        invoke(): runs the full pipeline for one tool call.
            - permission DENY -> ToolResult.failure("permission_denied")
            - permission ASK -> treated as APPROVAL path (a human must
              grant; without a callback the call fails closed)
            - risk AUTO -> execute
            - risk APPROVAL -> resolve a presented approval (hash-bound)
              or fail closed
            - risk DENY -> ToolResult.failure("risk_denied")
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        permissions: PermissionEngine,
        risk: RiskEngine,
        approvals: ApprovalLedger,
        subject: str = "agent",
        recycle_bin: object | None = None,
    ) -> None:
        self._registry = registry
        self._permissions = permissions
        self._risk = risk
        self._approvals = approvals
        self._subject = subject
        self._recycle_bin = recycle_bin

    def invoke(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        correlation_id: str = "",
        approval_id: str | None = None,
        path_policy: object | None = None,
    ) -> ToolResult:
        """Run one invocation through the full pipeline (sync dispatch).

        When a recycle bin is attached, destructive file deletes are
        reversible and route through the APPROVAL path (ADR-008
        reversal); without one they stay DENY (destructive+irreversible).
        """
        try:
            contract, handler = self._registry.get(tool)
        except ToolQuarantineError as err:
            return ToolResult.failure("tool_quarantined", str(err))
        except Exception as err:
            return ToolResult.failure("unknown_tool", str(err))

        request = PermissionRequest(
            subject=self._subject,
            action=next(iter(contract.required_permissions), ""),
            tool=contract.name,
        )
        permission = self._permissions.decide(request)

        if permission is PermissionDecision.DENY:
            return ToolResult.failure(
                "permission_denied",
                f"{self._subject!r} lacks permission for {contract.name!r}",
            )

        # Dynamic risk: destructive deletes are reversible when a recycle
        # bin is attached (ADR-008); irreversible without one.
        destructive = "destructive" in contract.side_effects
        reversible = not destructive or self._recycle_bin is not None
        factors = RiskFactors(
            destructiveness=destructive,
            external_side_effects=False,
            reversible=reversible,
            sensitive_data=False,
            blast_radius=RiskLevel.LOW,
        )
        risk_outcome = self._risk.evaluate(factors, contract.risk)

        if permission is PermissionDecision.ASK or risk_outcome is RiskOutcome.APPROVAL:
            if risk_outcome is RiskOutcome.DENY:
                return ToolResult.failure(
                    "risk_denied",
                    f"{contract.name!r} is denied by the risk engine "
                    "(destructive and irreversible)",
                )
            if approval_id is None:
                request_obj = self._approvals.create(
                    tool=contract.name,
                    subject=self._subject,
                    action_hash_value=action_hash(
                        tool=contract.name,
                        arguments=arguments,
                        subject=self._subject,
                        correlation_id=correlation_id,
                    ),
                    reason=contract.side_effects,
                )
                return ToolResult.failure(
                    "approval_required",
                    f"approval {request_obj.request_id} required for {contract.name!r}",
                )
            try:
                self._approvals.resolve(
                    request_id=approval_id,
                    action_hash_value=action_hash(
                        tool=contract.name,
                        arguments=arguments,
                        subject=self._subject,
                        correlation_id=correlation_id,
                    ),
                    subject=self._subject,
                )
            except Exception as err:
                return ToolResult.failure("approval_invalid", str(err))

        if risk_outcome is RiskOutcome.DENY:
            return ToolResult.failure("risk_denied", f"{contract.name!r} exceeds the risk budget")

        wired_arguments = dict(arguments)
        if path_policy is not None and contract.name.startswith("file_"):
            wired_arguments["__path_policy__"] = path_policy
        if contract.name == "file_delete" and self._recycle_bin is not None:
            wired_arguments["__recycle_bin__"] = self._recycle_bin
            from xenopus.tools.recycle import file_delete_recycle

            handler = file_delete_recycle

        started = time.monotonic()
        try:
            result = asyncio.run(_materialize(handler(wired_arguments)))
        except Exception as err:
            return ToolResult.failure(
                "tool_crash", str(err), duration_seconds=time.monotonic() - started
            )
        return result

    def plan_invocation(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        correlation_id: str = "",
    ) -> GatewayDecision:
        """Pre-flight a call without executing it (planner support)."""
        contract, _ = self._registry.get(tool)
        request = PermissionRequest(
            subject=self._subject,
            action=next(iter(contract.required_permissions), ""),
            tool=contract.name,
        )
        permission = self._permissions.decide(request)
        destructive = "destructive" in contract.side_effects
        factors = RiskFactors(
            destructiveness=destructive,
            reversible=not destructive or self._recycle_bin is not None,
        )
        risk_outcome = self._risk.evaluate(factors, contract.risk)
        return GatewayDecision(
            permission=permission,
            risk_outcome=risk_outcome,
            approval_request_id=None,
            approved=False,
        )
