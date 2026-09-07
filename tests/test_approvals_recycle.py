"""Persistent approval store + recycle bin + gateway rewiring tests."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from xenopus.persistence.approvals_store import (
    ApprovalStore,
    persistent_action_hash,
)
from xenopus.runtime.approval import ApprovalError
from xenopus.tools.files import PathPolicy
from xenopus.tools.recycle import RecycleBin


@pytest.fixture
def approvals() -> Generator[ApprovalStore]:
    store = ApprovalStore()
    yield store
    store.close()


def make_hash(tool: str = "file_delete", task: str = "task-1") -> str:
    return persistent_action_hash(tool=tool, arguments={"path": "x"}, subject="agent", task_id=task)


class TestApprovalStore:
    def test_create_pending_persists(self, approvals: ApprovalStore) -> None:
        row = approvals.create(
            tool="file_delete",
            subject="agent",
            action_hash=make_hash(),
            reason="destructive",
        )
        assert approvals.get(row.request_id).status == "PENDING"

    def test_grant_then_resolve(self, approvals: ApprovalStore) -> None:
        row = approvals.create(
            tool="file_delete",
            subject="agent",
            action_hash=make_hash(),
            reason="destructive",
        )
        approvals.grant(row.request_id)
        resolved = approvals.resolve(
            request_id=row.request_id, action_hash=make_hash(), subject="agent"
        )
        assert resolved.status == "GRANTED"

    def test_replay_rejected(self, approvals: ApprovalStore) -> None:
        row = approvals.create(
            tool="file_delete",
            subject="agent",
            action_hash=make_hash(),
            reason="destructive",
        )
        approvals.grant(row.request_id)
        other = make_hash(task="task-2")
        with pytest.raises(ApprovalError, match="replay rejected"):
            approvals.resolve(request_id=row.request_id, action_hash=other, subject="agent")

    def test_expired_rejected_and_reaped(self) -> None:
        now = {"t": datetime.now(UTC)}
        store = ApprovalStore(clock=lambda: now["t"])
        try:
            row = store.create(
                tool="file_delete",
                subject="agent",
                action_hash=make_hash(),
                reason="destructive",
                validity_seconds=30,
            )
            store.grant(row.request_id)
            now["t"] = now["t"] + timedelta(seconds=31)
            with pytest.raises(ApprovalError, match="expired"):
                store.resolve(
                    request_id=row.request_id,
                    action_hash=make_hash(),
                    subject="agent",
                )
            assert store.get(row.request_id).status == "EXPIRED"
        finally:
            store.close()

    def test_pending_listing(self, approvals: ApprovalStore) -> None:
        approvals.create(
            tool="file_delete",
            subject="agent",
            action_hash=make_hash(),
            reason="destructive",
        )
        assert len(approvals.list_pending()) == 1

    def test_double_grant_rejected(self, approvals: ApprovalStore) -> None:
        row = approvals.create(
            tool="file_delete",
            subject="agent",
            action_hash=make_hash(),
            reason="destructive",
        )
        approvals.grant(row.request_id)
        with pytest.raises(ApprovalError, match="already GRANTED"):
            approvals.grant(row.request_id)


class TestRecycleBin:
    def test_delete_and_restore_file(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        (tmp_path / "f.txt").write_text("data", encoding="utf-8")
        bin_ = RecycleBin(policy)
        entry = bin_.delete(tmp_path / "f.txt", "f.txt")
        assert not (tmp_path / "f.txt").exists()
        restored = bin_.restore(entry.entry_id)
        assert restored == (tmp_path / "f.txt").resolve()
        assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "data"

    def test_restore_refuses_overwrite(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        (tmp_path / "f.txt").write_text("old", encoding="utf-8")
        bin_ = RecycleBin(policy)
        entry = bin_.delete(tmp_path / "f.txt", "f.txt")
        (tmp_path / "f.txt").write_text("new occupant", encoding="utf-8")
        with pytest.raises(ValueError, match="already exists"):
            bin_.restore(entry.entry_id)

    def test_entries_listing(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        bin_ = RecycleBin(policy)
        bin_.delete(tmp_path / "a.txt", "a.txt")
        bin_.delete(tmp_path / "b.txt", "b.txt")
        assert len(bin_.entries()) == 2

    def test_purge_clears_bin(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        bin_ = RecycleBin(policy)
        bin_.delete(tmp_path / "a.txt", "a.txt")
        assert bin_.purge() == 1
        assert bin_.entries() == []

    def test_unknown_entry_refused(self, tmp_path: Path) -> None:
        bin_ = RecycleBin(PathPolicy(tmp_path))
        with pytest.raises(ValueError, match="unknown recycle entry"):
            bin_.restore("rcy-nope")
