"""Tool registry: searchable catalog with lazy contract discovery.

Registration is explicit (master prompt 40: capability discovery without
loading every schema into context). Callers query by name, permission, or
risk; schemas are materialized on demand.
"""

from __future__ import annotations

from xenopus.tools.contracts import RiskLevel, ToolContract, ToolHandler
from xenopus.tools.files import (
    FILE_TOOL_CONTRACTS,
    file_delete,
    file_list,
    file_read,
    file_write,
)


class ToolQuarantineError(Exception):
    """Raised when invoking a quarantined tool (fails closed)."""


class ToolRegistryError(Exception):
    """Raised for registry misuse: duplicates, unknown tools."""


class ToolQuarantine:
    """Quarantine gate: blocked tools stay listed but cannot invoke.

    Addendum 120: a tool with high failure rates or a security concern is
    quarantined — no new invocations until reviewed. Quarantine is data
    (a set of names) owned here; the gateway refuses quarantined calls.
    """

    def __init__(self) -> None:
        self._blocked: set[str] = set()

    def quarantine(self, name: str) -> None:
        """Block a tool from invocation (review required to lift)."""
        self._blocked.add(name)

    def lift(self, name: str) -> None:
        """Restore a tool after review (explicit, audited by caller)."""
        self._blocked.discard(name)

    def is_quarantined(self, name: str) -> bool:
        """True when the tool is currently blocked."""
        return name in self._blocked

    def quarantined(self) -> frozenset[str]:
        """All quarantined tool names (deterministic ordering by caller)."""
        return frozenset(self._blocked)


class ToolRegistry:
    """Catalog of tool contracts + handlers.

    Contract:
        register(): binds one contract to one handler; duplicates rejected.
        get(): returns (contract, handler); unknown names raise.
        search(): filtered views WITHOUT touching handlers — the planner
            consults metadata only (lazy discovery, master prompt 40).

    Integration: an optional ToolQuarantine restricts get()/search()
    visibility — quarantined tools remain registered (auditable) but
    callers cannot reach their handlers.
    """

    def __init__(self, *, quarantine: ToolQuarantine | None = None) -> None:
        self._contracts: dict[str, ToolContract] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._quarantine = quarantine or ToolQuarantine()

    def register(self, contract: ToolContract, handler: ToolHandler) -> None:
        """Bind a contract to its handler implementation."""
        if contract.name in self._contracts:
            msg = f"tool already registered: {contract.name!r}"
            raise ToolRegistryError(msg)
        self._contracts[contract.name] = contract
        self._handlers[contract.name] = handler

    def get(self, name: str) -> tuple[ToolContract, ToolHandler]:
        """Fetch contract + handler; unknown tool -> typed error.

        Quarantined tools fail closed with 'tool_quarantined' semantics
        (the gateway maps this to a structured failure).
        """
        if self._quarantine.is_quarantined(name):
            msg = f"tool is quarantined pending review: {name!r}"
            raise ToolQuarantineError(msg)
        try:
            return self._contracts[name], self._handlers[name]
        except KeyError as err:
            msg = f"unknown tool: {name!r}"
            raise ToolRegistryError(msg) from err

    def has(self, name: str) -> bool:
        """Membership check without exceptions."""
        return name in self._contracts and not self._quarantine.is_quarantined(name)

    def quarantine(self) -> ToolQuarantine:
        """The registry's quarantine gate (for operators/tests)."""
        return self._quarantine

    def search(
        self,
        *,
        permission: str | None = None,
        max_risk: RiskLevel | None = None,
    ) -> list[ToolContract]:
        """Return matching contract metadata, sorted by name."""
        results = []
        for contract in self._contracts.values():
            if permission is not None and permission not in contract.required_permissions:
                continue
            if max_risk is not None and contract.risk > max_risk:
                continue
            results.append(contract)
        return sorted(results, key=lambda c: c.name)

    def register_builtin_files(self) -> None:
        """Register the built-in file tools with their contracts."""
        handlers = {
            "file_read": file_read,
            "file_write": file_write,
            "file_list": file_list,
            "file_delete": file_delete,
        }
        for name, meta in FILE_TOOL_CONTRACTS.items():
            self.register(
                ToolContract(
                    name=name,
                    description=str(meta["description"]),
                    input_schema={"type": "object"},
                    side_effects=str(meta["side_effects"]),
                    required_permissions=frozenset(str(p) for p in meta["permissions"]),
                    risk=RiskLevel(int(meta["risk"])),
                    timeout_seconds=30.0,
                    idempotent=bool(meta["idempotent"]),
                    failure_modes=tuple(meta["failure_modes"]),
                ),
                handlers[name],
            )
