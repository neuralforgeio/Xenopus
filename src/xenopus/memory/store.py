"""SQLite memory store: scoped, provenance-tracked, promotion-gated.

Persistence for memory items with scope isolation, evidence-gated
promotion, superseding, expiry, and quality tracking (master prompt
22-27, 92). Contradictions resolve through supersession — the store
never silently merges content.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from xenopus.memory.types import (
    Evidence,
    MemoryDraft,
    MemoryError,
    MemoryItem,
    MemoryKind,
    MemoryQuality,
    MemoryRecord,
    MemoryScope,
    MemorySearchQuery,
    MemoryStatus,
    PromotionGate,
    compute_expiry,
    new_memory_id,
    now_utc,
)

MEMORY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_ref TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_source TEXT,
    evidence_observation_id TEXT,
    evidence_correlation_id TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL,
    reuse_count INTEGER NOT NULL DEFAULT 0,
    correction_count INTEGER NOT NULL DEFAULT 0,
    superseded_by TEXT
)
"""

MEMORY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_memories_scope
    ON memories (scope, scope_ref, status)
"""

ACTIVE_STATUSES = (MemoryStatus.ACTIVE.value,)
LOOKABLE_STATUSES = (MemoryStatus.ACTIVE.value, MemoryStatus.STALE.value)


class MemoryStore:
    """Scoped memory persistence with a deterministic promotion gate.

    Contract:
        create(): stores a CANDIDATE (never ACTIVE — auto-trust is
        structurally impossible).
        promote(): applies the PromotionGate; only passing candidates
        become ACTIVE.
        supersede(): marks old as SUPERSEDED and stores the replacement
        as a fresh CANDIDATE in the same scope.
        expire_stale(): transitions ACTIVE items past expiry to EXPIRED.

    Failure modes:
        MemoryError for unknown ids, cross-scope access attempts, and
        illegal status transitions.
    """

    def __init__(
        self,
        path: Path,
        *,
        promotion_gate: PromotionGate | None = None,
        clock: Any = None,
    ) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(MEMORY_TABLE_DDL)
        self._conn.execute(MEMORY_INDEX_DDL)
        self._conn.commit()
        self._gate = promotion_gate or PromotionGate()
        self._clock = clock

    def _now(self) -> datetime:
        """Current time via the injectable clock (UTC default)."""
        return self._clock() if self._clock else now_utc()

    def create(self, draft: MemoryDraft) -> MemoryRecord:
        """Store one CANDIDATE item (promotion is a separate gate)."""
        created_at = self._now()
        item = MemoryItem(
            memory_id=new_memory_id(),
            content=draft.content.strip(),
            kind=draft.kind,
            scope=draft.scope,
            scope_ref=draft.scope_ref,
            source=draft.source,
            confidence=draft.confidence,
            evidence=draft.evidence,
            created_at=created_at,
            expires_at=compute_expiry(created_at, draft.ttl_seconds),
            status=MemoryStatus.CANDIDATE,
        )
        self._insert(item)
        return MemoryRecord(item=item)

    def promote(self, memory_id: str) -> MemoryRecord:
        """Apply the promotion gate; raises on refusal (auditable)."""
        record = self.get(memory_id)
        decision = self._gate.evaluate(record.item, now=self._now())
        if not decision.promoted:
            msg = f"promotion refused for {memory_id}: {decision.reason}"
            raise MemoryError(msg)
        updated = self._replace_status(record, MemoryStatus.ACTIVE)
        return updated

    def reject(self, memory_id: str) -> MemoryRecord:
        """Mark a CANDIDATE REJECTED (terminal for that candidate)."""
        record = self.get(memory_id)
        if record.item.status is not MemoryStatus.CANDIDATE:
            msg = f"only CANDIDATE items can be rejected; {memory_id} is {record.item.status.value}"
            raise MemoryError(msg)
        return self._replace_status(record, MemoryStatus.REJECTED)

    def supersede(
        self,
        memory_id: str,
        replacement_draft: MemoryDraft,
    ) -> tuple[MemoryRecord, MemoryRecord]:
        """Supersede old content with a new CANDIDATE in the same scope.

        The old item becomes SUPERSEDED pointing at the replacement; the
        replacement starts as CANDIDATE and passes the promotion gate
        separately — supersession never auto-trusts.
        """
        record = self.get(memory_id)
        if record.item.scope != replacement_draft.scope or (
            record.item.scope_ref != replacement_draft.scope_ref
        ):
            msg = "replacement draft must live in the same scope as the superseded item"
            raise MemoryError(msg)
        replacement = self.create(replacement_draft)
        with self._conn:
            self._conn.execute(
                "UPDATE memories SET status = ?, superseded_by = ? WHERE memory_id = ?",
                (MemoryStatus.SUPERSEDED.value, replacement.item.memory_id, memory_id),
            )
        return self.get(memory_id), replacement

    def record_reuse(self, memory_id: str) -> MemoryRecord:
        """Increment the reuse counter (retrieval hit)."""
        with self._conn:
            self._conn.execute(
                "UPDATE memories SET reuse_count = reuse_count + 1 WHERE memory_id = ?",
                (memory_id,),
            )
        return self.get(memory_id)

    def record_correction(self, memory_id: str) -> MemoryRecord:
        """Increment the correction counter (quality penalty input)."""
        with self._conn:
            self._conn.execute(
                "UPDATE memories SET correction_count = correction_count + 1 WHERE memory_id = ?",
                (memory_id,),
            )
        return self.get(memory_id)

    def mark_stale(self, memory_id: str) -> MemoryRecord:
        """Flag an ACTIVE item as possibly outdated (manual/periodic review)."""
        record = self.get(memory_id)
        if record.item.status is not MemoryStatus.ACTIVE:
            msg = f"only ACTIVE items can go stale; {memory_id} is {record.item.status.value}"
            raise MemoryError(msg)
        return self._replace_status(record, MemoryStatus.STALE)

    def expire_stale(self) -> list[MemoryRecord]:
        """Transition expired ACTIVE items to EXPIRED; returns them."""
        now_iso = self._now().isoformat()
        rows = self._conn.execute(
            "SELECT memory_id FROM memories WHERE status = ? AND expires_at IS NOT NULL "
            "AND expires_at <= ?",
            (MemoryStatus.ACTIVE.value, now_iso),
        ).fetchall()
        expired: list[MemoryRecord] = []
        with self._conn:
            for (memory_id,) in rows:
                self._conn.execute(
                    "UPDATE memories SET status = ? WHERE memory_id = ?",
                    (MemoryStatus.EXPIRED.value, memory_id),
                )
                expired.append(self.get(memory_id))
        return expired

    def search(self, query: MemorySearchQuery) -> list[MemoryRecord]:
        """Scoped search; scope isolation is mandatory by construction."""
        statuses = LOOKABLE_STATUSES if query.include_stale else ACTIVE_STATUSES
        rows = self._conn.execute(
            "SELECT memory_id FROM memories WHERE scope = ? AND scope_ref = ? "
            "AND status IN (?, ?) AND confidence >= ? AND content LIKE ? "
            "ORDER BY confidence DESC, created_at DESC LIMIT ?",
            (
                query.scope.value,
                query.scope_ref,
                statuses[0],
                statuses[-1],
                query.min_confidence,
                f"%{query.text_substring}%",
                query.limit,
            ),
        ).fetchall()
        records = [self.get(memory_id) for (memory_id,) in rows]
        if query.kinds:
            records = [r for r in records if r.item.kind in query.kinds]
        return records

    def quality(self, memory_id: str) -> MemoryQuality:
        """Computed quality score for one item."""
        record = self.get(memory_id)
        evidence_bonus = 1.0 if record.item.evidence is not None else 0.3
        freshness = 1.0 if record.item.expires_at is None else 0.7
        return MemoryQuality(
            provenance=evidence_bonus,
            freshness=freshness,
            reuse_success=record.reuse_count,
            corrections=record.correction_count,
        )

    def get(self, memory_id: str) -> MemoryRecord:
        """Fetch one record; MemoryError when unknown."""
        row = self._conn.execute(
            "SELECT memory_id, content, kind, scope, scope_ref, source, confidence, "
            "evidence_source, evidence_observation_id, evidence_correlation_id, "
            "created_at, expires_at, status, reuse_count, correction_count, superseded_by "
            "FROM memories WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if row is None:
            msg = f"unknown memory: {memory_id!r}"
            raise MemoryError(msg)
        return self._row_to_record(row)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    # -- internals ---------------------------------------------------------

    def _insert(self, item: MemoryItem) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO memories (memory_id, content, kind, scope, scope_ref, source, "
                "confidence, evidence_source, evidence_observation_id, "
                "evidence_correlation_id, created_at, expires_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.memory_id,
                    item.content,
                    item.kind.value,
                    item.scope.value,
                    item.scope_ref,
                    item.source,
                    item.confidence,
                    item.evidence.source if item.evidence else None,
                    item.evidence.tool_observation_id if item.evidence else None,
                    item.evidence.journal_correlation_id if item.evidence else None,
                    item.created_at.isoformat(),
                    item.expires_at.isoformat() if item.expires_at else None,
                    item.status.value,
                ),
            )

    def _replace_status(self, record: MemoryRecord, status: MemoryStatus) -> MemoryRecord:
        with self._conn:
            self._conn.execute(
                "UPDATE memories SET status = ? WHERE memory_id = ?",
                (status.value, record.item.memory_id),
            )
        return self.get(record.item.memory_id)

    @staticmethod
    def _row_to_record(
        row: tuple[
            str,
            str,
            str,
            str,
            str,
            str,
            float,
            str | None,
            str | None,
            str | None,
            str,
            str | None,
            str,
            int,
            int,
            str | None,
        ],
    ) -> MemoryRecord:
        evidence = None
        if row[7] is not None:
            evidence = Evidence(
                source=row[7],
                tool_observation_id=row[8],
                journal_correlation_id=row[9],
            )
        item = MemoryItem(
            memory_id=row[0],
            content=row[1],
            kind=MemoryKind(row[2]),
            scope=MemoryScope(row[3]),
            scope_ref=row[4],
            source=row[5],
            confidence=row[6],
            evidence=evidence,
            created_at=_parse_dt(row[10]),
            expires_at=_parse_dt(row[11]) if row[11] else None,
            status=MemoryStatus(row[12]),
        )
        return MemoryRecord(
            item=item,
            reuse_count=row[13],
            correction_count=row[14],
            superseded_by=row[15],
        )


def _parse_dt(iso: str) -> datetime:
    """Parse an ISO-8601 stored timestamp."""
    return datetime.fromisoformat(iso)
