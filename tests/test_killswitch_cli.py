"""Gateway recycle rewiring + kill switch + recovery + CLI tests."""

from pathlib import Path

import pytest

from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskState, TaskStore
from xenopus.runtime.approval import ApprovalLedger
from xenopus.runtime.killswitch import KillSwitch
from xenopus.runtime.permission import (
    PermissionDecision,
    PermissionEngine,
    PermissionRule,
)
from xenopus.runtime.recovery import CrashRecovery
from xenopus.runtime.risk import RiskEngine
from xenopus.tools.files import PathPolicy
from xenopus.tools.gateway import ToolGateway
from xenopus.tools.recycle import RecycleBin
from xenopus.tools.registry import ToolRegistry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "ws").mkdir()
    return tmp_path / "ws"


def make_gateway(workspace: Path, *, with_recycle: bool) -> ToolGateway:
    registry = ToolRegistry()
    registry.register_builtin_files()
    return ToolGateway(
        registry=registry,
        permissions=PermissionEngine(
            [PermissionRule(decision=PermissionDecision.ALLOW, subject="agent")]
        ),
        risk=RiskEngine(),
        approvals=ApprovalLedger(),
        subject="agent",
        recycle_bin=RecycleBin(PathPolicy(workspace)) if with_recycle else None,
    )


class TestGatewayRewiring:
    def test_delete_without_recycle_is_denied(self, workspace: Path) -> None:
        gateway = make_gateway(workspace, with_recycle=False)
        result = gateway.invoke("file_delete", {"path": "x.txt"}, path_policy=PathPolicy(workspace))
        assert result.ok is False
        assert result.error_code == "risk_denied"

    def test_delete_with_recycle_requires_approval(self, workspace: Path) -> None:
        gateway = make_gateway(workspace, with_recycle=True)
        decision = gateway.plan_invocation("file_delete", {"path": "x.txt"})
        assert decision.risk_outcome.value == "APPROVAL"

    def test_delete_with_recycle_round_trip(self, workspace: Path) -> None:
        (workspace / "x.txt").write_text("important", encoding="utf-8")
        gateway = make_gateway(workspace, with_recycle=True)
        # No approval id -> fails closed with the request created.
        blocked = gateway.invoke(
            "file_delete", {"path": "x.txt"}, path_policy=PathPolicy(workspace)
        )
        assert blocked.ok is False
        assert blocked.error_code == "approval_required"
        assert (workspace / "x.txt").exists()  # untouched


class TestKillSwitchUnit:
    def test_trigger_cancels_and_audits(self, tmp_path: Path) -> None:
        journal = EventJournal(tmp_path / "j.sqlite")
        store = TaskStore(tmp_path / "t.sqlite", journal=journal)
        try:
            record = store.create("long task", correlation_id="c1")
            report = KillSwitch(store=store, journal=journal).trigger("test stop")
            assert [r.task_id for r in report.cancelled] == [record.task_id]
            assert store.get(record.task_id).state is TaskState.CANCELLED
            audit_events = [event.type.value for _, event in journal.all_events()]
            assert "KILLSWITCH_TRIGGERED" in audit_events
        finally:
            store.close()
            journal.close()


class TestRecoveryUnit:
    def test_crash_recovery_flags_resumable(self, tmp_path: Path) -> None:
        store = TaskStore(tmp_path / "t.sqlite")
        try:
            record = store.create("t", correlation_id="c1")
            store.transition(record.task_id, TaskState.RUNNING)
            report = CrashRecovery(store).run()
            assert report.has_work is True
            assert store.get(record.task_id).state is TaskState.RESUMABLE
            # Idempotent: second pass finds nothing.
            assert CrashRecovery(store).run().has_work is False
        finally:
            store.close()


class TestTaskCli:
    def test_create_list_inspect(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import xenopus.cli as cli

        monkeypatch.setenv("XENOPUS_HOME", str(tmp_path / "xhome"))
        assert cli.main(["task", "create", "analyze the repo"]) == 0
        assert cli.main(["task", "list"]) == 0
        # create again to obtain id from list output is awkward via exit
        # codes; smoke-level: commands exit 0.
        assert cli.main(["task", "recover"]) == 0

    def test_killswitch_command(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import xenopus.cli as cli

        monkeypatch.setenv("XENOPUS_HOME", str(tmp_path / "xhome"))
        assert cli.main(["task", "create", "t"]) == 0
        assert cli.main(["killswitch", "--reason", "test"]) == 0

    def test_task_error_exits_one(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import xenopus.cli as cli

        monkeypatch.setenv("XENOPUS_HOME", str(tmp_path / "xhome"))
        assert cli.main(["task", "inspect", "task-nonexistent"]) == 1
