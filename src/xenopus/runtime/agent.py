"""Agent model: profiles, contracts, results, and health.

An agent is a bounded execution unit (addendum 4-11): it receives a
structured AgentContract (never a raw prompt), runs under an isolated
profile (tools, permissions, budget), and returns a structured
AgentResult (never raw text). Health states support the heartbeat
watchdog (addendum 19, 59).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from xenopus.runtime.budget import Budget


class AgentError(Exception):
    """Raised for agent misuse: invalid contracts, unknown ids."""


class AgentHealth(StrEnum):
    """Health states driving the watchdog and admission (addendum 59)."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STUCK = "STUCK"
    FAILED = "FAILED"
    RECOVERING = "RECOVERING"


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Static definition of an agent role (addendum 70).

    Contract:
        role: logical capability ('researcher', 'coder', ...).
        allowed_tools/forbidden_tools: tool-selection bounds enforced
            by the orchestrator's executor wiring.
        permissions_subject: the permission-engine subject this agent
            acts as — per-agent permission isolation (addendum 9).
        budget: hard resource envelope for any run of this profile.
    """

    role: str
    allowed_tools: frozenset[str]
    forbidden_tools: frozenset[str]
    permissions_subject: str
    budget: Budget = field(default_factory=Budget.unlimited)
    max_depth: int = 2
    max_children: int = 4
    heartbeat_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.role.strip():
            msg = "agent role must be non-empty"
            raise ValueError(msg)
        if self.max_depth < 0:
            msg = "max_depth must be >= 0"
            raise ValueError(msg)
        if self.max_children < 0:
            msg = "max_children must be >= 0"
            raise ValueError(msg)
        overlap = self.allowed_tools & self.forbidden_tools
        if overlap:
            msg = f"tools both allowed and forbidden: {sorted(overlap)}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AgentContract:
    """What one agent run receives (addendum 10 — never a raw prompt).

    Invariants: mission non-empty; task references exist upstream;
    output_schema describes the expected AgentResult.artifacts shape.
    """

    mission: str
    task_id: str
    inputs: dict[str, Any] = field(default_factory=dict)
    success_criteria: tuple[str, ...] = ()
    output_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.mission.strip():
            msg = "agent mission must be non-empty"
            raise ValueError(msg)
        if not self.task_id.strip():
            msg = "agent contract must reference a task id"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Structured output of one agent run (addendum 11 — never raw text).

    Contract:
        status: COMPLETED / FAILED / TIMEOUT — never implicit success.
        artifacts: structured payload shaped by the contract schema.
        evidence: references (observation/journal ids) backing claims.
        confidence: [0,1]; PARTIAL results must lower it.
    """

    agent_run_id: str
    task_id: str
    role: str
    status: str
    summary: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    started_at: str = ""
    finished_at: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            msg = f"confidence must be within [0,1], got {self.confidence}"
            raise ValueError(msg)

    @classmethod
    def completed(
        cls,
        *,
        task_id: str,
        role: str,
        summary: str,
        artifacts: dict[str, Any] | None = None,
        evidence: tuple[str, ...] = (),
        confidence: float = 1.0,
    ) -> AgentResult:
        """Build a COMPLETED result."""
        now = datetime.now(UTC).isoformat()
        return cls(
            agent_run_id=f"arun-{uuid4().hex[:12]}",
            task_id=task_id,
            role=role,
            status="COMPLETED",
            summary=summary,
            artifacts=artifacts or {},
            evidence=evidence,
            confidence=confidence,
            started_at=now,
            finished_at=now,
        )

    @classmethod
    def failed(
        cls,
        *,
        task_id: str,
        role: str,
        error: str,
        confidence: float = 0.0,
    ) -> AgentResult:
        """Build a FAILED result carrying the error."""
        now = datetime.now(UTC).isoformat()
        return cls(
            agent_run_id=f"arun-{uuid4().hex[:12]}",
            task_id=task_id,
            role=role,
            status="FAILED",
            errors=(error,),
            confidence=confidence,
            started_at=now,
            finished_at=now,
        )

    @classmethod
    def timeout(cls, *, task_id: str, role: str, detail: str) -> AgentResult:
        """Build a TIMEOUT result (partial evidence may exist)."""
        now = datetime.now(UTC).isoformat()
        return cls(
            agent_run_id=f"arun-{uuid4().hex[:12]}",
            task_id=task_id,
            role=role,
            status="TIMEOUT",
            warnings=(detail,),
            confidence=0.0,
            started_at=now,
            finished_at=now,
        )
