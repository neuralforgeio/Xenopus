"""Memory types: provenance, lifecycle statuses, scopes, and quality model.

Every memory item starts as CANDIDATE and may only be promoted with
evidence (master prompt 23/133: memory-poisoning defense). Statuses and
scopes are closed enums; scope isolation is enforced by the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4


class MemoryError(Exception):
    """Raised for memory misuse: unknown ids, illegal transitions."""


class MemoryStatus(StrEnum):
    """Lifecycle statuses (master prompt 23).

    Only the promotion gate moves CANDIDATE -> ACTIVE, and only with
    declared evidence; STALE/SUPERSEDED/EXPIRED/ARCHIVED/REJECTED are
    terminal-ish states with documented re-entry rules.
    """

    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"
    REJECTED = "REJECTED"


class MemoryScope(StrEnum):
    """Isolation scopes (master prompt 24). No leakage between scopes."""

    GLOBAL = "GLOBAL"
    USER = "USER"
    WORKSPACE = "WORKSPACE"
    PROJECT = "PROJECT"
    TASK = "TASK"
    TEMPORARY = "TEMPORARY"


class MemoryKind(StrEnum):
    """Memory content kinds (master prompt 22 subset for P0/P1)."""

    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PROCEDURAL = "PROCEDURAL"
    PREFERENCE = "PREFERENCE"
    PROJECT = "PROJECT"
    FAILURE = "FAILURE"


@dataclass(frozen=True, slots=True)
class Evidence:
    """Evidence backing a memory item or its promotion.

    Contract: at least one of tool_observation_id or journal_correlation_id
    must reference real recorded evidence — claims without evidence are
    rejected by the store (P1: never claim what was not verified).
    """

    source: str
    tool_observation_id: str | None = None
    journal_correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source.strip():
            msg = "evidence source must be non-empty"
            raise ValueError(msg)
        if self.tool_observation_id is None and self.journal_correlation_id is None:
            msg = "evidence must reference an observation or a journal correlation"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """One stored memory record (master prompt 23 field set).

    Invariants:
        confidence in [0,1]; provenance/source never empty; scope-kind
        pairs are immutable after creation (updates create new versions
        that supersede).
    """

    memory_id: str
    content: str
    kind: MemoryKind
    scope: MemoryScope
    scope_ref: str
    source: str
    confidence: float
    evidence: Evidence | None
    created_at: datetime
    expires_at: datetime | None
    status: MemoryStatus

    def __post_init__(self) -> None:
        if not self.content.strip():
            msg = "memory content must be non-empty"
            raise ValueError(msg)
        if not 0.0 <= self.confidence <= 1.0:
            msg = f"confidence must be within [0,1], got {self.confidence}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MemoryDraft:
    """Input shape for creating a CANDIDATE memory."""

    content: str
    kind: MemoryKind
    scope: MemoryScope
    scope_ref: str
    source: str
    confidence: float = 0.5
    evidence: Evidence | None = None
    ttl_seconds: float | None = None


def new_memory_id() -> str:
    """Fresh memory id."""
    return f"mem-{uuid4().hex[:14]}"


def compute_expiry(created_at: datetime, ttl_seconds: float | None) -> datetime | None:
    """Expiry timestamp from a TTL (None = no expiry)."""
    if ttl_seconds is None:
        return None
    if ttl_seconds <= 0:
        msg = "ttl_seconds must be > 0 when provided"
        raise ValueError(msg)
    return created_at + timedelta(seconds=ttl_seconds)


def now_utc() -> datetime:
    """Single source for 'now' (UTC) across the memory layer."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class MemorySearchQuery:
    """Scoped search filter for the store.

    scope + scope_ref are mandatory: every query is scoped, preventing
    accidental cross-project retrieval (scope isolation).
    """

    scope: MemoryScope
    scope_ref: str
    kinds: frozenset[MemoryKind] = frozenset()
    include_stale: bool = False
    min_confidence: float = 0.0
    text_substring: str = ""
    limit: int = 50

    def __post_init__(self) -> None:
        if self.limit < 1:
            msg = "limit must be >= 1"
            raise ValueError(msg)
        if not 0.0 <= self.min_confidence <= 1.0:
            msg = "min_confidence must be within [0,1]"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """Outcome of a promotion-gate evaluation (auditable)."""

    memory_id: str
    promoted: bool
    reason: str
    checked_at: datetime


PROMOTION_MIN_CONFIDENCE = 0.5
PROMOTION_REQUIRES_EVIDENCE = True


@dataclass(frozen=True, slots=True)
class PromotionGate:
    """Deterministic promotion rules (master prompt 133).

    A CANDIDATE becomes ACTIVE only when: evidence is attached AND
    confidence >= PROMOTION_MIN_CONFIDENCE. Same inputs -> same decision.
    """

    min_confidence: float = PROMOTION_MIN_CONFIDENCE

    def evaluate(self, item: MemoryItem, *, now: datetime | None = None) -> PromotionDecision:
        """Decide promotion for one item; never mutates."""
        checked_at = now or now_utc()
        if item.status is not MemoryStatus.CANDIDATE:
            return PromotionDecision(
                item.memory_id, False, f"status is {item.status.value}, not CANDIDATE", checked_at
            )
        if PROMOTION_REQUIRES_EVIDENCE and item.evidence is None:
            return PromotionDecision(item.memory_id, False, "no evidence attached", checked_at)
        if item.confidence < self.min_confidence:
            return PromotionDecision(
                item.memory_id,
                False,
                f"confidence {item.confidence} below threshold {self.min_confidence}",
                checked_at,
            )
        return PromotionDecision(item.memory_id, True, "evidence + confidence ok", checked_at)


@dataclass(frozen=True, slots=True)
class MemoryQuality:
    """Quality score dimensions (master prompt 92) — computed, not stored."""

    provenance: float
    freshness: float
    reuse_success: int
    corrections: int

    def score(self) -> float:
        """Weighted quality score in [0,1]."""
        base = 0.5 * self.provenance + 0.3 * self.freshness
        reuse = min(1.0, self.reuse_success / 10.0)
        penalty = min(0.5, self.corrections * 0.1)
        return max(0.0, min(1.0, 0.8 * base + 0.2 * reuse - penalty))


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """Store-layer row: item + bookkeeping counters."""

    item: MemoryItem
    reuse_count: int = 0
    correction_count: int = 0
    superseded_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
