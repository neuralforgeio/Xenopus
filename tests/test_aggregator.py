"""Aggregator tests: ranking, dedup, conflict resolution — no voting."""

from hypothesis import given
from hypothesis import strategies as st

from xenopus.runtime.agent import AgentResult
from xenopus.runtime.aggregator import (
    Claim,
    ConflictResolution,
    ResultAggregator,
    claims_from_results,
    rank_claims,
)


def claim(
    key: str = "k",
    content: str = "v",
    evidence: int = 0,
    confidence: float = 0.5,
    cid: str | None = None,
) -> Claim:
    return Claim(
        claim_id=cid or f"claim-{evidence}-{confidence}-{key}".replace(".", "-"),
        task_id="t1",
        role="researcher",
        key=key,
        content=content,
        evidence=tuple(f"ev-{i}" for i in range(evidence)),
        confidence=confidence,
    )


class TestRanking:
    def test_evidence_dominates_confidence(self) -> None:
        unevidenced = claim(confidence=0.9, evidence=0)
        evidenced = claim(confidence=0.5, evidence=2)
        ranked = rank_claims([unevidenced, evidenced])
        assert ranked[0] is evidenced

    def test_confidence_breaks_evidence_ties(self) -> None:
        low = claim(evidence=2, confidence=0.3)
        high = claim(evidence=2, confidence=0.8)
        assert rank_claims([low, high])[0] is high

    def test_claim_id_breaks_full_ties(self) -> None:
        a = claim(evidence=1, confidence=0.5, cid="claim-a")
        b = claim(evidence=1, confidence=0.5, cid="claim-b")
        assert rank_claims([b, a])[0] is a

    def test_claim_validation(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="key"):
            Claim(
                claim_id="c",
                task_id="t",
                role="r",
                key=" ",
                content="x",
            )
        with pytest.raises(ValueError, match="confidence"):
            claim(confidence=1.5)


class TestDedupAndAggregation:
    def test_identical_content_collapses_to_top_ranked(self) -> None:
        weak = claim(key="k", content="same", evidence=0, confidence=0.4)
        strong = claim(key="k", content="same", evidence=3, confidence=0.6)
        report = ResultAggregator().aggregate([weak, strong])
        assert len(report.top_claims) == 1
        assert report.top_claims[0] is strong
        assert report.conflicts == ()

    def test_evidence_dominance_supersedes(self) -> None:
        weak = claim(key="k", content="A", evidence=0, confidence=0.9)
        strong = claim(key="k", content="B", evidence=3, confidence=0.2)
        report = ResultAggregator().aggregate([weak, strong])
        (conflict,) = report.conflicts
        assert conflict.resolution is ConflictResolution.SUPERSEDED
        assert conflict.winner is strong
        assert conflict.losers == (weak,)

    def test_irreducible_disagreement_is_conflict_not_merged(self) -> None:
        a = claim(key="k", content="A", evidence=2, confidence=0.5)
        b = claim(key="k", content="B", evidence=2, confidence=0.5)
        report = ResultAggregator().aggregate([a, b])
        (conflict,) = report.conflicts
        assert conflict.resolution is ConflictResolution.CONFLICT
        assert conflict.winner is None

    def test_conflict_degrades_confidence(self) -> None:
        clean = ResultAggregator().aggregate([claim(key="a", confidence=0.9, evidence=1)])
        conflicted = ResultAggregator().aggregate(
            [
                claim(key="a", content="A", evidence=1, confidence=0.9),
                claim(key="a", content="B", evidence=1, confidence=0.9),
            ]
        )
        assert conflicted.confidence < clean.confidence
        assert conflicted.confidence == 0.9 - 0.25

    def test_disjoint_keys_coexist_without_conflicts(self) -> None:
        report = ResultAggregator().aggregate([claim(key="a"), claim(key="b", content="other")])
        assert len(report.top_claims) == 2
        assert report.conflicts == ()

    def test_unevidenced_top_claims_lower_uncertainty_later(self) -> None:
        report = ResultAggregator().aggregate([claim(key="k", evidence=0)])
        assert report.top_claims[0].evidence == ()


class TestClaimsFromResults:
    def test_extracts_from_default_artifact_shape(self) -> None:
        result = AgentResult.completed(
            task_id="t1",
            role="researcher",
            summary="found things",
            artifacts={
                "claims": [
                    {"claim_key": "finding-1", "claim_content": "X is true"},
                ]
            },
            evidence=("obs-1",),
            confidence=0.9,
        )
        claims = claims_from_results([result])
        assert len(claims) == 1
        assert claims[0].key == "finding-1"
        assert claims[0].evidence == ("obs-1",)

    def test_skips_non_completed_results(self) -> None:
        failed = AgentResult.failed(task_id="t", role="r", error="boom")
        assert claims_from_results([failed]) == []


class TestNoNaiveVoting:
    @given(
        evidence_counts=st.lists(st.integers(min_value=0, max_value=2), min_size=2, max_size=4),
    )
    def test_majority_never_beats_stronger_evidence(self, evidence_counts: list[int]) -> None:
        """N weaker-evidenced claims never outvote one with MORE evidence.

        Anti-voting property (addendum 63): even 4 claims with <= 2
        evidence refs against 1 claim with 3 evidence refs must rank
        the strongly-evidenced claim first — count of claims is never
        the deciding factor, evidence depth is.
        """
        weak = [
            claim(key="k", content=f"majority-{i}", evidence=e, confidence=0.9)
            for i, e in enumerate(evidence_counts)
        ]
        strong = claim(key="k", content="evidenced", evidence=3, confidence=0.1)
        ranked = rank_claims([*weak, strong])
        assert ranked[0] is strong
