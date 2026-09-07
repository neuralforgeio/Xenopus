"""Memory store tests: lifecycle, promotion gate, scopes, supersession."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from xenopus.memory.store import MemoryStore
from xenopus.memory.types import (
    Evidence,
    MemoryDraft,
    MemoryError,
    MemoryKind,
    MemoryScope,
    MemorySearchQuery,
    MemoryStatus,
    PromotionGate,
)


@pytest.fixture
def store(tmp_path: Path) -> Generator[MemoryStore]:
    s = MemoryStore(tmp_path / "memory.sqlite")
    yield s
    s.close()


def draft(
    content: str = "note",
    scope: MemoryScope = MemoryScope.PROJECT,
    ref: str = "repo-A",
    confidence: float = 0.8,
    evidence: Evidence | None = None,
) -> MemoryDraft:
    return MemoryDraft(
        content=content,
        kind=MemoryKind.SEMANTIC,
        scope=scope,
        scope_ref=ref,
        source="test",
        confidence=confidence,
        evidence=evidence or Evidence(source="test", journal_correlation_id="corr-1"),
    )


class TestMemoryLifecycle:
    def test_create_starts_as_candidate(self, store: MemoryStore) -> None:
        record = store.create(draft())
        assert record.item.status is MemoryStatus.CANDIDATE

    def test_promotion_requires_evidence(self, store: MemoryStore) -> None:
        no_evidence = MemoryDraft(
            content="x",
            kind=MemoryKind.SEMANTIC,
            scope=MemoryScope.PROJECT,
            scope_ref="r",
            source="test",
            confidence=0.9,
            evidence=None,
        )
        record = store.create(no_evidence)
        with pytest.raises(MemoryError, match="no evidence"):
            store.promote(record.item.memory_id)

    def test_promotion_requires_confidence(self, store: MemoryStore) -> None:
        record = store.create(draft(confidence=0.2))
        with pytest.raises(MemoryError, match="below threshold"):
            store.promote(record.item.memory_id)

    def test_promotion_success(self, store: MemoryStore) -> None:
        record = store.create(draft())
        promoted = store.promote(record.item.memory_id)
        assert promoted.item.status is MemoryStatus.ACTIVE

    def test_double_promotion_refused(self, store: MemoryStore) -> None:
        record = store.create(draft())
        store.promote(record.item.memory_id)
        with pytest.raises(MemoryError, match="not CANDIDATE"):
            store.promote(record.item.memory_id)

    def test_reject_candidate(self, store: MemoryStore) -> None:
        record = store.create(draft())
        assert store.reject(record.item.memory_id).item.status is MemoryStatus.REJECTED

    def test_reject_active_refused(self, store: MemoryStore) -> None:
        record = store.create(draft())
        store.promote(record.item.memory_id)
        with pytest.raises(MemoryError, match="only CANDIDATE"):
            store.reject(record.item.memory_id)

    def test_evidence_without_reference_rejected(self, store: MemoryStore) -> None:
        with pytest.raises(ValueError, match="observation or a journal correlation"):
            Evidence(source="test")


class TestScopeIsolation:
    def test_search_is_scoped(self, store: MemoryStore) -> None:
        a = store.create(draft(content="repo A fact", ref="repo-A"))
        b = store.create(draft(content="repo B fact", ref="repo-B"))
        store.promote(a.item.memory_id)
        store.promote(b.item.memory_id)
        hits_a = store.search(MemorySearchQuery(scope=MemoryScope.PROJECT, scope_ref="repo-A"))
        hits_b = store.search(MemorySearchQuery(scope=MemoryScope.PROJECT, scope_ref="repo-B"))
        assert len(hits_a) == 1 and hits_a[0].item.content == "repo A fact"
        assert len(hits_b) == 1 and hits_b[0].item.content == "repo B fact"

    def test_search_default_excludes_stale(self, store: MemoryStore) -> None:
        record = store.create(draft())
        store.promote(record.item.memory_id)
        store.mark_stale(record.item.memory_id)
        fresh = store.search(MemorySearchQuery(scope=MemoryScope.PROJECT, scope_ref="repo-A"))
        with_stale = store.search(
            MemorySearchQuery(scope=MemoryScope.PROJECT, scope_ref="repo-A", include_stale=True)
        )
        assert fresh == []
        assert len(with_stale) == 1

    def test_candidates_are_not_searchable(self, store: MemoryStore) -> None:
        store.create(draft())  # never promoted
        hits = store.search(MemorySearchQuery(scope=MemoryScope.PROJECT, scope_ref="repo-A"))
        assert hits == []


class TestSupersession:
    def test_supersede_marks_old_and_creates_candidate(self, store: MemoryStore) -> None:
        old = store.create(draft(content="old fact"))
        store.promote(old.item.memory_id)
        superseded, replacement = store.supersede(
            old.item.memory_id,
            draft(content="corrected fact"),
        )
        assert superseded.item.status is MemoryStatus.SUPERSEDED
        assert superseded.superseded_by == replacement.item.memory_id
        assert replacement.item.status is MemoryStatus.CANDIDATE  # never auto-trusted

    def test_supersede_cross_scope_refused(self, store: MemoryStore) -> None:
        record = store.create(draft(ref="repo-A"))
        with pytest.raises(MemoryError, match="same scope"):
            store.supersede(record.item.memory_id, draft(ref="repo-B"))


class TestExpiryAndQuality:
    def test_ttl_expiry_transitions_active_to_expired(self, tmp_path: Path) -> None:
        now = {"t": datetime.now(UTC)}
        store = MemoryStore(
            tmp_path / "m.sqlite",
            promotion_gate=PromotionGate(),
            clock=lambda: now["t"],
        )
        try:
            record = store.create(
                MemoryDraft(
                    content="short-lived",
                    kind=MemoryKind.SEMANTIC,
                    scope=MemoryScope.TASK,
                    scope_ref="t1",
                    source="test",
                    confidence=0.9,
                    evidence=Evidence(source="t", journal_correlation_id="c"),
                    ttl_seconds=60,
                )
            )
            store.promote(record.item.memory_id)
            now["t"] = now["t"] + timedelta(seconds=61)
            expired = store.expire_stale()
            assert [r.item.memory_id for r in expired] == [record.item.memory_id]
            assert store.get(record.item.memory_id).item.status is MemoryStatus.EXPIRED
        finally:
            store.close()

    def test_quality_penalizes_corrections(self, store: MemoryStore) -> None:
        clean = store.create(draft())
        corrected = store.create(draft(content="often wrong"))
        for _ in range(3):
            store.record_correction(corrected.item.memory_id)
        assert (
            store.quality(clean.item.memory_id).score()
            > store.quality(corrected.item.memory_id).score()
        )

    def test_reuse_increments_counter(self, store: MemoryStore) -> None:
        record = store.create(draft())
        store.record_reuse(record.item.memory_id)
        assert store.get(record.item.memory_id).reuse_count == 1


class TestPromotionGateProperty:
    def test_gate_is_deterministic_for_same_inputs(self, store: MemoryStore) -> None:
        gate = PromotionGate()
        record = store.create(draft(confidence=0.4))
        d1 = gate.evaluate(record.item)
        d2 = gate.evaluate(record.item)
        assert d1.promoted == d2.promoted and d1.reason == d2.reason
