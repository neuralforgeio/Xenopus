"""Tool quarantine tests (addendum 120)."""

import pytest

from xenopus.runtime.approval import ApprovalLedger
from xenopus.runtime.permission import (
    PermissionDecision,
    PermissionEngine,
    PermissionRule,
)
from xenopus.runtime.risk import RiskEngine
from xenopus.tools.contracts import RiskLevel, ToolContract, ToolResult
from xenopus.tools.gateway import ToolGateway
from xenopus.tools.registry import (
    ToolQuarantine,
    ToolQuarantineError,
    ToolRegistry,
    ToolRegistryError,
)


async def _noop(arguments: dict[str, object]) -> ToolResult:
    return ToolResult.success({"ok": True})


def contract(name: str) -> ToolContract:
    return ToolContract(
        name=name,
        description="test tool",
        input_schema={"type": "object"},
        side_effects="none",
        required_permissions=frozenset({"test.read"}),
        risk=RiskLevel.LOW,
        timeout_seconds=5.0,
        idempotent=True,
    )


def make_gateway(quarantine: ToolQuarantine) -> ToolGateway:
    registry = ToolRegistry(quarantine=quarantine)
    registry.register(contract("tool_a"), _handler_a)
    registry.register(contract("tool_b"), _handler_b)
    return ToolGateway(
        registry=registry,
        permissions=PermissionEngine(
            [PermissionRule(decision=PermissionDecision.ALLOW, subject="agent")]
        ),
        risk=RiskEngine(),
        approvals=ApprovalLedger(),
        subject="agent",
    )


async def _handler_a(arguments: dict[str, object]) -> ToolResult:
    return ToolResult.success({"tool": "a"})


async def _handler_b(arguments: dict[str, object]) -> ToolResult:
    return ToolResult.success({"tool": "b"})


class TestQuarantine:
    def test_quarantined_tool_fails_closed_through_gateway(self) -> None:
        quarantine = ToolQuarantine()
        gateway = make_gateway(quarantine)
        ok = gateway.invoke("tool_a", {})
        assert ok.ok is True
        quarantine.quarantine("tool_a")
        blocked = gateway.invoke("tool_a", {})
        assert blocked.ok is False
        assert blocked.error_code == "tool_quarantined"

    def test_lift_restores_invocation(self) -> None:
        quarantine = ToolQuarantine()
        gateway = make_gateway(quarantine)
        quarantine.quarantine("tool_a")
        quarantine.lift("tool_a")
        assert gateway.invoke("tool_a", {}).ok is True

    def test_quarantine_does_not_affect_siblings(self) -> None:
        quarantine = ToolQuarantine()
        gateway = make_gateway(quarantine)
        quarantine.quarantine("tool_a")
        assert gateway.invoke("tool_b", {}).ok is True

    def test_registry_get_raises_quarantine_error(self) -> None:
        quarantine = ToolQuarantine()
        registry = ToolRegistry(quarantine=quarantine)
        registry.register(contract("tool_a"), _handler_a)
        quarantine.quarantine("tool_a")
        with pytest.raises(ToolQuarantineError, match="quarantined"):
            registry.get("tool_a")

    def test_registry_unknown_still_distinct_error(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(ToolRegistryError, match="unknown tool"):
            registry.get("ghost")

    def test_has_reflects_quarantine(self) -> None:
        quarantine = ToolQuarantine()
        registry = ToolRegistry(quarantine=quarantine)
        registry.register(contract("tool_a"), _handler_a)
        assert registry.has("tool_a") is True
        quarantine.quarantine("tool_a")
        assert registry.has("tool_a") is False

    def test_quarantined_listing(self) -> None:
        quarantine = ToolQuarantine()
        quarantine.quarantine("x")
        assert "x" in quarantine.quarantined()
