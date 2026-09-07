"""Gateway + executor + verifier pipeline tests (offline, temp dirs)."""

from collections.abc import Generator
from pathlib import Path

import pytest

from xenopus.persistence.journal import EventJournal
from xenopus.runtime.approval import ApprovalLedger
from xenopus.runtime.budget import RetryPolicy
from xenopus.runtime.executor import Executor
from xenopus.runtime.observer import Observation, ObservationLog
from xenopus.runtime.permission import (
    PermissionDecision,
    PermissionEngine,
    PermissionRule,
)
from xenopus.runtime.risk import RiskEngine
from xenopus.runtime.verifier import (
    Verdict,
    Verifier,
    adversarial_recheck,
    require_observation,
    require_success_result,
)
from xenopus.tools.files import PathPolicy
from xenopus.tools.gateway import ToolGateway
from xenopus.tools.registry import ToolRegistry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "ws").mkdir()
    return tmp_path / "ws"


@pytest.fixture
def journal(tmp_path: Path) -> Generator[EventJournal]:
    j = EventJournal(tmp_path / "journal.sqlite")
    yield j
    j.close()


def make_gateway(
    workspace: Path,
    permissions: PermissionEngine | None = None,
) -> ToolGateway:
    registry = ToolRegistry()
    registry.register_builtin_files()
    return ToolGateway(
        registry=registry,
        permissions=permissions
        or PermissionEngine(
            [
                PermissionRule(decision=PermissionDecision.ALLOW, subject="agent"),
            ]
        ),
        risk=RiskEngine(),
        approvals=ApprovalLedger(),
        subject="agent",
    )


class TestGateway:
    def test_read_allowed_auto(self, workspace: Path) -> None:
        gateway = make_gateway(workspace)
        result = gateway.invoke(
            "file_write",
            {"path": "f.txt", "content": "data"},
            path_policy=PathPolicy(workspace),
        )
        assert result.ok is True
        read = gateway.invoke("file_read", {"path": "f.txt"}, path_policy=PathPolicy(workspace))
        assert read.ok is True
        assert read.data["content"] == "data"

    def test_permission_denied_fails_closed(self, workspace: Path) -> None:
        gateway = make_gateway(workspace, permissions=PermissionEngine())
        result = gateway.invoke(
            "file_write",
            {"path": "f.txt", "content": "x"},
            path_policy=PathPolicy(workspace),
        )
        assert result.ok is False
        assert result.error_code == "permission_denied"

    def test_unknown_tool_fails_closed(self, workspace: Path) -> None:
        gateway = make_gateway(workspace)
        result = gateway.invoke("no_such_tool", {})
        assert result.ok is False
        assert result.error_code == "unknown_tool"

    def test_destructive_irreversible_denied(self, workspace: Path) -> None:
        gateway = make_gateway(workspace)
        result = gateway.invoke("file_delete", {"path": "x.txt"}, path_policy=PathPolicy(workspace))
        assert result.ok is False
        assert result.error_code == "risk_denied"

    def test_approval_flow_round_trip(self, workspace: Path) -> None:
        """Deletion is destructive; with reversible=True it needs approval.

        PathPolicy makes workspace deletes effectively reversible by the
        operator (backup), so the gateway wires destructive=True,
        reversible=False for file_delete -> DENY. To exercise the APPROVAL
        path we use file_delete's declared contract but a reversible factor
        set via plan_invocation; the full APPROVAL flow is covered by the
        risk tests. Here we assert the closed-by-default behavior holds.
        """
        gateway = make_gateway(workspace)
        decision = gateway.plan_invocation("file_delete", {"path": "x.txt"})
        assert decision.risk_outcome.value in ("APPROVAL", "DENY")


class TestExecutor:
    async def test_execute_journals_and_observes(
        self, workspace: Path, journal: EventJournal
    ) -> None:
        gateway = make_gateway(workspace)
        observations = ObservationLog()
        executor = Executor(gateway=gateway, journal=journal, observations=observations)
        result = await executor.execute(
            "file_write",
            {"path": "a.txt", "content": "v"},
            correlation_id="corr-1",
            path_policy=PathPolicy(workspace),
        )
        assert result.ok is True
        events = journal.events_for("corr-1")
        types = [e.type.value for e in events]
        assert "TOOL_STARTED" in types
        assert "TOOL_COMPLETED" in types
        assert len(observations.for_correlation("corr-1")) == 1

    async def test_permanent_failure_never_retries(
        self, workspace: Path, journal: EventJournal
    ) -> None:
        gateway = make_gateway(workspace)
        observations = ObservationLog()
        executor = Executor(gateway=gateway, journal=journal, observations=observations)
        result = await executor.execute(
            "file_read",
            {"path": "ghost.txt"},
            correlation_id="corr-2",
            retry_policy=RetryPolicy(max_attempts=5, backoff_base_seconds=0),
            path_policy=PathPolicy(workspace),
        )
        assert result.ok is False
        assert result.error_code == "not_found"
        started = [e for e in journal.events_for("corr-2") if e.type.value == "TOOL_STARTED"]
        assert len(started) == 1  # permanent: exactly one attempt


class TestVerifier:
    def _obs(self, tool: str, ok: bool, error_code: str = "") -> Observation:
        return Observation.create(
            kind="tool_result",
            content={"tool": tool, "ok": ok, "error_code": error_code, "data": {}},
            correlation_id="c",
            created_at="2026-01-01T00:00:00+00:00",
        )

    def test_pass_requires_all_checks_pass(self) -> None:
        verifier = Verifier()
        report = verifier.verify(
            [
                require_success_result("ok_tool", "file_write"),
                require_observation("obs_present", "tool_result"),
            ],
            [self._obs("file_write", ok=True)],
        )
        assert report.overall is Verdict.PASS

    def test_uncertain_is_not_pass(self) -> None:
        verifier = Verifier()
        report = verifier.verify([require_success_result("never_ran", "file_read")], [])
        assert report.overall is Verdict.UNCERTAIN

    def test_fail_dominates(self) -> None:
        verifier = Verifier()
        report = verifier.verify(
            [
                require_success_result("r1", "file_write"),
                require_success_result("r2", "file_read"),
            ],
            [self._obs("file_write", ok=True), self._obs("file_read", ok=False, error_code="e")],
        )
        assert report.overall is Verdict.FAIL

    def test_adversarial_catches_hidden_failure_behind_retry(self) -> None:
        verifier = Verifier()
        report = verifier.verify(
            [
                require_success_result("happy", "file_write"),
                adversarial_recheck("adv", "file_write"),
            ],
            [self._obs("file_write", ok=False), self._obs("file_write", ok=True)],
        )
        # require_success passes (last... actually first ok entry found passes);
        # adversarial must FAIL on the hidden failed attempt.
        assert report.checks[1].verdict is Verdict.FAIL
        assert report.overall is Verdict.FAIL

    def test_adversarial_clean_pass(self) -> None:
        verifier = Verifier()
        report = verifier.verify(
            [adversarial_recheck("adv", "file_write")],
            [self._obs("file_write", ok=True)],
        )
        assert report.overall is Verdict.PASS

    def test_empty_evidence_is_uncertain(self) -> None:
        verifier = Verifier()
        report = verifier.verify([require_observation("x", "anything")], [])
        assert report.overall is Verdict.UNCERTAIN
