"""Persistent approval ledger: SQLite-backed, hash-bound, expiring.

Durable counterpart of the in-memory ApprovalLedger (Phase 4): approval
requests survive restarts, so a remote-authorized destructive action
cannot be lost to a crash mid-approval. Same security rules (addendum
75-76): hash-bound to the exact action, expiring, single-grant,
replay-proof.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from xenopus.runtime.approval import ApprovalError, ApprovalStatus

APPROVALS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS approvals (
    request_id TEXT PRIMARY KEY,
    action_hash TEXT NOT NULL,
    tool TEXT NOT NULL,
    subject TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL
)
"""

APPROVALS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals (status, expires_at)
"""


def persistent_action_hash(
    *,
    tool: str,
    arguments: dict[str, Any],
    subject: str,
    task_id: str,
) -> str:
    """Hash binding an approval to one exact action + task.

    Distinct from the runtime approval hash: the durable ledger binds to
    the owning TASK, not a transient correlation id — approvals must
    survive restarts where correlation ids may be regenerated.
    """
    canonical = json.dumps(
        {"tool": tool, "arguments": arguments, "subject": subject, "task_id": task_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalRow:
    """One persisted approval request."""

    request_id: str
    action_hash: str
    tool: str
    subject: str
    reason: str
    created_at: str
    expires_at: str
    status: str


class ApprovalStore:
    """SQLite approval ledger with the same semantics as the runtime ledger.

    Contract:
        create(): inserts a PENDING request with an expiry window.
        grant()/deny(): single-use — only PENDING may transition.
        resolve(): validates status, expiry, subject, and action hash;
            replay attempts and expired grants raise ApprovalError.
        reap_expired(): maintenance sweep — expired grants become
            EXPIRED rows (append-only history preserved).

    Failure modes:
        ApprovalError for unknown ids, double-processing, expiry,
        subject mismatch, and hash-bound replay attempts.
    """

    def __init__(
        self, path: str | None = None, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._conn = sqlite3.connect(path or ":memory:")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(APPROVALS_TABLE_DDL)
        self._conn.execute(APPROVALS_INDEX_DDL)
        self._conn.commit()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._counter = 0

    def create(
        self,
        *,
        tool: str,
        subject: str,
        action_hash: str,
        reason: str,
        validity_seconds: float = 300.0,
    ) -> ApprovalRow:
        """Create a PENDING approval with an expiry window."""
        if validity_seconds <= 0:
            msg = "validity_seconds must be > 0"
            raise ValueError(msg)
        now = self._clock()
        self._counter += 1
        request_id = f"appr-{self._counter:06d}"
        with self._conn:
            self._conn.execute(
                "INSERT INTO approvals (request_id, action_hash, tool, subject, "
                "reason, created_at, expires_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    action_hash,
                    tool,
                    subject,
                    reason,
                    now.isoformat(),
                    (now + timedelta(seconds=validity_seconds)).isoformat(),
                    ApprovalStatus.PENDING.value,
                ),
            )
        return self.get(request_id)

    def grant(self, request_id: str) -> ApprovalRow:
        """Grant a PENDING request (single-use)."""
        row = self.get(request_id)
        if row.status != ApprovalStatus.PENDING.value:
            msg = f"approval {request_id} already {row.status}"
            raise ApprovalError(msg)
        return self._update_status(request_id, ApprovalStatus.GRANTED)

    def deny(self, request_id: str) -> ApprovalRow:
        """Deny a PENDING request (single-use)."""
        row = self.get(request_id)
        if row.status != ApprovalStatus.PENDING.value:
            msg = f"approval {request_id} already {row.status}"
            raise ApprovalError(msg)
        return self._update_status(request_id, ApprovalStatus.DENIED)

    def resolve(
        self,
        *,
        request_id: str,
        action_hash: str,
        subject: str,
    ) -> ApprovalRow:
        """Validate a presented approval against the executing action."""
        row = self.get(request_id)
        if row.status != ApprovalStatus.GRANTED.value:
            msg = f"approval {request_id} is {row.status}, not GRANTED"
            raise ApprovalError(msg)
        if self._clock() > datetime.fromisoformat(row.expires_at):
            self._update_status(request_id, ApprovalStatus.EXPIRED)
            msg = f"approval {request_id} expired at {row.expires_at}"
            raise ApprovalError(msg)
        if row.subject != subject:
            msg = f"approval {request_id} bound to subject {row.subject!r}"
            raise ApprovalError(msg)
        if row.action_hash != action_hash:
            msg = f"approval {request_id} does not match this action (replay rejected)"
            raise ApprovalError(msg)
        return row

    def reap_expired(self) -> list[ApprovalRow]:
        """Mark lapsed grants EXPIRED; returns affected rows."""
        now_iso = self._clock().isoformat()
        rows = self._conn.execute(
            "SELECT request_id FROM approvals WHERE status = ? AND expires_at <= ?",
            (ApprovalStatus.GRANTED.value, now_iso),
        ).fetchall()
        expired: list[ApprovalRow] = []
        for (request_id,) in rows:
            expired.append(self._update_status(request_id, ApprovalStatus.EXPIRED))
        return expired

    def get(self, request_id: str) -> ApprovalRow:
        """Fetch one row; ApprovalError when unknown."""
        row = self._conn.execute(
            "SELECT request_id, action_hash, tool, subject, reason, created_at, "
            "expires_at, status FROM approvals WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            msg = f"unknown approval request: {request_id!r}"
            raise ApprovalError(msg)
        return ApprovalRow(
            request_id=row[0],
            action_hash=row[1],
            tool=row[2],
            subject=row[3],
            reason=row[4],
            created_at=row[5],
            expires_at=row[6],
            status=row[7],
        )

    def list_pending(self) -> list[ApprovalRow]:
        """All PENDING requests (approval-surface feed)."""
        rows = self._conn.execute(
            "SELECT request_id FROM approvals WHERE status = ? ORDER BY created_at, rowid",
            (ApprovalStatus.PENDING.value,),
        ).fetchall()
        return [self.get(row[0]) for row in rows]

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def _update_status(self, request_id: str, status: ApprovalStatus) -> ApprovalRow:
        with self._conn:
            self._conn.execute(
                "UPDATE approvals SET status = ? WHERE request_id = ?",
                (status.value, request_id),
            )
        return self.get(request_id)
