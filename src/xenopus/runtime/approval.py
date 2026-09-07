"""Approval requests: hash-bound, expiring, non-replayable.

An approval authorizes exactly ONE action hash within its validity
window (addendum 75-76): task + action hash + scope + subject +
timestamp. Old approvals never authorize different actions; expired
approvals must be re-requested.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any


class ApprovalError(Exception):
    """Raised for invalid/expired/replayed approvals."""


class ApprovalStatus(StrEnum):
    """Lifecycle of an approval request."""

    PENDING = "PENDING"
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


def action_hash(
    *,
    tool: str,
    arguments: dict[str, Any],
    subject: str,
    correlation_id: str,
) -> str:
    """Stable hash binding an approval to one exact action.

    Canonical JSON with sorted keys ensures the same arguments always
    produce the same hash — argument-order trickery cannot mint a new
    approval out of an old one.
    """
    canonical = json.dumps(
        {
            "tool": tool,
            "arguments": arguments,
            "subject": subject,
            "correlation_id": correlation_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """One approval ask.

    Contract:
        hash: action_hash binding — grants validate against this exact
            hash; a grant for another action is a replay attempt.
        expires_at: validity deadline (wall clock, UTC).
        reason: human-readable justification shown to the approver.
    """

    request_id: str
    action_hash_value: str
    tool: str
    subject: str
    reason: str
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING


class ApprovalLedger:
    """In-memory approval store (durable persistence arrives in Phase 6).

    Rules:
        - grant/deny only work on PENDING requests (no double-processing).
        - resolve() validates hash match, status, and expiry; violations
          raise ApprovalError (never silently pass).
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._requests: dict[str, ApprovalRequest] = {}
        self._counter = 0

    def create(
        self,
        *,
        tool: str,
        subject: str,
        action_hash_value: str,
        reason: str,
        validity_seconds: float = 300.0,
    ) -> ApprovalRequest:
        """Create a PENDING approval with an expiry window (default 5 min)."""
        if validity_seconds <= 0:
            msg = "validity_seconds must be > 0"
            raise ValueError(msg)
        now = self._clock()
        self._counter += 1
        request = ApprovalRequest(
            request_id=f"appr-{self.counter:06d}",
            action_hash_value=action_hash_value,
            tool=tool,
            subject=subject,
            reason=reason,
            created_at=now,
            expires_at=now + timedelta(seconds=validity_seconds),
        )
        self._requests[request.request_id] = request
        return request

    @property
    def counter(self) -> int:
        """Monotonic counter for deterministic request ids in tests."""
        return self._counter

    def grant(self, request_id: str) -> ApprovalRequest:
        """Mark a PENDING request GRANTED."""
        request = self._require(request_id)
        self._assert_pending(request)
        return self._update(request, ApprovalStatus.GRANTED)

    def deny(self, request_id: str) -> ApprovalRequest:
        """Mark a PENDING request DENIED."""
        request = self._require(request_id)
        self._assert_pending(request)
        return self._update(request, ApprovalStatus.DENIED)

    def resolve(
        self,
        *,
        request_id: str,
        action_hash_value: str,
        subject: str,
    ) -> ApprovalRequest:
        """Validate a presented approval against the action being executed.

        Raises ApprovalError when: the request is unknown, not granted,
        expired, bound to another subject, or bound to a different action
        hash (replay attempt — addendum 76).
        """
        request = self._require(request_id)
        if request.status is not ApprovalStatus.GRANTED:
            msg = f"approval {request_id} is {request.status.value}, not GRANTED"
            raise ApprovalError(msg)
        if self._clock() > request.expires_at:
            expired = self._update(request, ApprovalStatus.EXPIRED)
            msg = f"approval {request_id} expired at {expired.expires_at.isoformat()}"
            raise ApprovalError(msg)
        if request.subject != subject:
            msg = f"approval {request_id} bound to subject {request.subject!r}"
            raise ApprovalError(msg)
        if request.action_hash_value != action_hash_value:
            msg = f"approval {request_id} does not match this action (replay rejected)"
            raise ApprovalError(msg)
        return request

    def get(self, request_id: str) -> ApprovalRequest:
        """Fetch one request; ApprovalError when unknown."""
        return self._require(request_id)

    def _require(self, request_id: str) -> ApprovalRequest:
        try:
            return self._requests[request_id]
        except KeyError as err:
            msg = f"unknown approval request: {request_id!r}"
            raise ApprovalError(msg) from err

    def _assert_pending(self, request: ApprovalRequest) -> None:
        if request.status is not ApprovalStatus.PENDING:
            msg = f"approval {request.request_id} already {request.status.value}"
            raise ApprovalError(msg)

    def _update(self, request: ApprovalRequest, status: ApprovalStatus) -> ApprovalRequest:
        updated = ApprovalRequest(
            request_id=request.request_id,
            action_hash_value=request.action_hash_value,
            tool=request.tool,
            subject=request.subject,
            reason=request.reason,
            created_at=request.created_at,
            expires_at=request.expires_at,
            status=status,
        )
        self._requests[updated.request_id] = updated
        return updated
