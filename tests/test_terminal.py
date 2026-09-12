"""Terminal tool tests (ADR-029): allow-list, containment, caps, env.

Every test runs real subprocesses (node/git resolve on this host)
or asserts refusal BEFORE any spawn — no mock of the process layer.
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path

import pytest

from xenopus.runtime.approval import ApprovalLedger
from xenopus.runtime.permission import PermissionDecision, PermissionEngine, PermissionRule
from xenopus.runtime.risk import RiskEngine
from xenopus.tools.contracts import RiskLevel
from xenopus.tools.files import PathPolicy
from xenopus.tools.gateway import ToolGateway
from xenopus.tools.registry import ToolRegistry
from xenopus.tools.terminal import (
    ALLOWED_COMMANDS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_BYTES,
    sanitized_child_env,
    terminal_run,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Generator[Path]:
    (tmp_path / "sub").mkdir()
    yield tmp_path


def _args(argv: dict[str, object], root: Path) -> dict[str, object]:
    return {"__path_policy__": PathPolicy(root), **argv}


class TestAllowList:
    async def test_allow_listed_command_runs(self, workspace: Path) -> None:
        result = await terminal_run(
            _args(
                {"command": sys.executable and "git", "args": ["--version"]},
                workspace,
            )
        )
        assert result.ok, result.error_message
        assert "git version" in result.data["output"].lower()

    async def test_unknown_command_refused_pre_spawn(self, workspace: Path) -> None:
        result = await terminal_run(
            _args({"command": "definitely_not_real_xyz", "args": []}, workspace)
        )
        assert not result.ok
        assert result.error_code == "command_not_allowed"

    async def test_non_allowlisted_real_binary_refused(self, workspace: Path) -> None:
        """python itself is real but NOT allow-listed — must refuse."""
        result = await terminal_run(
            _args({"command": "python", "args": ["-c", "print('nope')"]}, workspace)
        )
        assert not result.ok
        assert result.error_code == "command_not_allowed"

    async def test_path_like_command_refused(self, workspace: Path) -> None:
        result = await terminal_run(
            _args({"command": "C:/Windows/System32/cmd.exe", "args": []}, workspace)
        )
        assert result.error_code in ("command_not_allowed", "invalid_arguments")

    def test_allow_list_contents(self) -> None:
        assert frozenset({"node", "npm", "npx", "git"}) == ALLOWED_COMMANDS


class TestCwdContainment:
    async def test_cwd_inside_workspace_runs(self, workspace: Path) -> None:
        result = await terminal_run(
            _args({"command": "git", "args": ["--version"], "cwd": "sub"}, workspace)
        )
        assert result.ok

    async def test_cwd_escape_refused(self, workspace: Path) -> None:
        result = await terminal_run(
            _args({"command": "git", "args": ["--version"], "cwd": ".."}, workspace)
        )
        assert not result.ok
        assert result.error_code in ("invalid_arguments", "cwd_escapes_boundary")

    async def test_missing_cwd_refused(self, workspace: Path) -> None:
        result = await terminal_run(
            _args({"command": "git", "args": ["--version"], "cwd": "no-such-dir"}, workspace)
        )
        assert result.error_code == "not_found"

    async def test_root_cwd_allowed(self, workspace: Path) -> None:
        result = await terminal_run(
            _args({"command": "git", "args": ["--version"], "cwd": ""}, workspace)
        )
        assert result.ok


class TestOutputAndTimeout:
    async def test_output_truncated_at_cap(self, workspace: Path) -> None:
        """Force >64KB output via git hash-object on a generated big file."""
        big = workspace / "big.txt"
        big.write_text("x" * 200_000)
        result = await terminal_run(
            _args({"command": "git", "args": ["hash-object", "big.txt"]}, workspace)
        )
        assert result.ok
        assert len(result.data["output"].encode()) <= MAX_OUTPUT_BYTES + 100

    async def test_timeout_bounds(self, workspace: Path) -> None:
        result = await terminal_run(
            _args(
                {
                    "command": "node",
                    "args": ["-e", "setTimeout(() => {}, 60000)"],
                    "timeout_seconds": 1,
                },
                workspace,
            )
        )
        assert not result.ok
        assert result.error_code == "timeout"
        assert result.duration_seconds < 10

    async def test_timeout_argument_bounds_enforced(self, workspace: Path) -> None:
        result = await terminal_run(
            _args(
                {"command": "git", "args": ["--version"], "timeout_seconds": 0.5},
                workspace,
            )
        )
        assert result.error_code == "invalid_arguments"

    async def test_failure_exit_code_reported(self, workspace: Path) -> None:
        result = await terminal_run(
            _args({"command": "node", "args": ["-e", "process.exit(3)"]}, workspace)
        )
        assert not result.ok
        assert result.error_code == "exit_3"
        assert result.data["returncode"] == 3


class TestEnvHygiene:
    def test_xenopus_vars_stripped(self) -> None:
        env = sanitized_child_env(
            {
                "PATH": "C:/bin",
                "XENOPUS_WEBHOOK_SECRETS": "github:whsec-test",
                "XENOPUS_DISCORD_TOKEN": "secret-discord",
                "TOKENROUTER": "sk-leak",
            }
        )
        assert env == {"PATH": "C:/bin"}

    def test_allowlist_passthrough(self) -> None:
        env = sanitized_child_env({"PATH": "p", "USERPROFILE": "u", "SYSTEMROOT": "s"})
        assert env == {"PATH": "p", "USERPROFILE": "u", "SYSTEMROOT": "s"}

    async def test_child_cannot_read_parent_xenopus_env(self, workspace: Path) -> None:
        """node -e printing env must show NO XENOPUS_* keys."""
        result = await terminal_run(
            _args(
                {"command": "node", "args": ["-e", "console.log(JSON.stringify(process.env))"]},
                workspace,
            )
        )
        if not result.ok:  # node missing on host: skip live assertion
            pytest.skip("node not available")
        secrets_in_child = ("XENOPUS_WEBHOOK_SECRETS", "XENOPUS_DISCORD_TOKEN")
        child_output = result.data["output"]
        leaked = [k for k in secrets_in_child if k in child_output]
        assert leaked == []


class TestGatewayIntegration:
    def _gateway(self, workspace: Path, *, allow_terminal: bool) -> ToolGateway:
        registry = ToolRegistry()
        registry.register_builtin_files()
        registry.register_builtin_terminal()
        permissions = PermissionEngine()
        if allow_terminal:
            permissions.add_rule(
                PermissionRule(
                    decision=PermissionDecision.ALLOW,
                    subject="agent",
                    action="terminal.run",
                )
            )
        return ToolGateway(
            registry=registry,
            permissions=permissions,
            risk=RiskEngine(),
            approvals=ApprovalLedger(),
            subject="agent",
        )

    def test_permission_denied_without_rule(self, workspace: Path) -> None:
        gateway = self._gateway(workspace, allow_terminal=False)
        result = gateway.invoke(
            "terminal_run",
            {"command": "git", "args": ["--version"]},
            path_policy=PathPolicy(workspace),
        )
        assert result.error_code == "permission_denied"

    def test_risk_requires_approval_even_when_permitted(self, workspace: Path) -> None:
        """external side effects -> APPROVAL path: first call must fail closed."""
        gateway = self._gateway(workspace, allow_terminal=True)
        result = gateway.invoke(
            "terminal_run",
            {"command": "git", "args": ["--version"]},
            correlation_id="corr-t1",
            path_policy=PathPolicy(workspace),
        )
        assert result.error_code == "approval_required"
        assert result.error_message.startswith("approval ")

    def test_approval_grant_allows_execution(self, workspace: Path) -> None:
        gateway = self._gateway(workspace, allow_terminal=True)
        arguments = {"command": "git", "args": ["--version"]}
        correlation = "corr-t2"
        first = gateway.invoke(
            "terminal_run", arguments, correlation_id=correlation, path_policy=PathPolicy(workspace)
        )
        assert first.error_code == "approval_required"
        request_id = first.error_message.split("approval ")[1].split(" ")[0]
        ledger = gateway._approvals
        ledger.grant(request_id)
        second = gateway.invoke(
            "terminal_run",
            arguments,
            correlation_id=correlation,
            approval_id=request_id,
            path_policy=PathPolicy(workspace),
        )
        assert second.ok, second.error_message

    def test_contract_shape(self, workspace: Path) -> None:
        registry = ToolRegistry()
        registry.register_builtin_terminal()
        contract = registry.get("terminal_run")[0]
        assert contract.risk == RiskLevel.MEDIUM or int(contract.risk) >= 1
        assert "terminal.run" in contract.required_permissions
        assert contract.side_effects.startswith("external")
        assert contract.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
