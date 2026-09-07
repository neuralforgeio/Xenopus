"""Risk engine + approval ledger tests (deterministic tables, replay)."""

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xenopus.runtime.approval import (
    ApprovalError,
    ApprovalLedger,
    ApprovalStatus,
    action_hash,
)
from xenopus.runtime.risk import RiskEngine, RiskFactors, RiskOutcome
from xenopus.tools.contracts import RiskLevel


class TestRiskEngine:
    def test_pure_low_risk_is_auto(self) -> None:
        engine = RiskEngine()
        assert engine.evaluate(RiskFactors(), RiskLevel.LOW) is RiskOutcome.AUTO

    def test_destructive_irreversible_is_deny_regardless_of_score(self) -> None:
        engine = RiskEngine()
        outcome = engine.evaluate(
            RiskFactors(destructiveness=True, reversible=False),
            RiskLevel.LOW,
        )
        assert outcome is RiskOutcome.DENY

    def test_moderate_combination_requires_approval(self) -> None:
        engine = RiskEngine()
        # external(2) + sensitive(2) + LOW(0) = 4 -> APPROVAL threshold.
        outcome = engine.evaluate(
            RiskFactors(external_side_effects=True, sensitive_data=True),
            RiskLevel.LOW,
        )
        assert outcome is RiskOutcome.APPROVAL

    def test_scores_are_monotonic_in_factors(self) -> None:
        base = RiskFactors().score()
        assert RiskFactors(destructiveness=True).score() == base + 3
        assert RiskFactors(reversible=False).score() == base + 4
        assert RiskFactors(blast_radius=RiskLevel.HIGH).score() == base + 2

    @given(
        destructive=st.booleans(),
        external=st.booleans(),
        reversible=st.booleans(),
        sensitive=st.booleans(),
        blast=st.sampled_from(RiskLevel),
    )
    def test_destructive_irreversible_always_deny(
        self,
        destructive: bool,
        external: bool,
        reversible: bool,
        sensitive: bool,
        blast: RiskLevel,
    ) -> None:
        """The deny row is absolute for any other factor combination."""
        engine = RiskEngine()
        factors = RiskFactors(
            destructiveness=destructive,
            external_side_effects=external,
            reversible=reversible,
            sensitive_data=sensitive,
            blast_radius=blast,
        )
        outcome = engine.evaluate(factors, RiskLevel.LOW)
        if destructive and not reversible:
            assert outcome is RiskOutcome.DENY
        else:
            assert outcome is not RiskOutcome.DENY or factors.score() >= 8


class TestApprovalLedger:
    def _hash(self, tool: str = "file_delete", corr: str = "c1") -> str:
        return action_hash(tool=tool, arguments={"path": "x"}, subject="agent", correlation_id=corr)

    def test_create_pending(self) -> None:
        ledger = ApprovalLedger()
        req = ledger.create(
            tool="file_delete",
            subject="agent",
            action_hash_value=self._hash(),
            reason="destructive",
        )
        assert req.status is ApprovalStatus.PENDING

    def test_grant_then_resolve(self) -> None:
        ledger = ApprovalLedger()
        req = ledger.create(
            tool="file_delete",
            subject="agent",
            action_hash_value=self._hash(),
            reason="destructive",
        )
        ledger.grant(req.request_id)
        resolved = ledger.resolve(
            request_id=req.request_id, action_hash_value=self._hash(), subject="agent"
        )
        assert resolved.status is ApprovalStatus.GRANTED

    def test_replay_with_different_action_rejected(self) -> None:
        ledger = ApprovalLedger()
        req = ledger.create(
            tool="file_delete",
            subject="agent",
            action_hash_value=self._hash(),
            reason="destructive",
        )
        ledger.grant(req.request_id)
        other_hash = action_hash(
            tool="file_delete",
            arguments={"path": "different"},
            subject="agent",
            correlation_id="c1",
        )
        with pytest.raises(ApprovalError, match="replay rejected"):
            ledger.resolve(request_id=req.request_id, action_hash_value=other_hash, subject="agent")

    def test_expired_approval_rejected(self) -> None:
        now = {"t": datetime.now(UTC)}
        ledger = ApprovalLedger(clock=lambda: now["t"])
        req = ledger.create(
            tool="file_delete",
            subject="agent",
            action_hash_value=self._hash(),
            reason="destructive",
            validity_seconds=60,
        )
        ledger.grant(req.request_id)
        now["t"] = now["t"] + timedelta(seconds=61)
        with pytest.raises(ApprovalError, match="expired"):
            ledger.resolve(
                request_id=req.request_id,
                action_hash_value=self._hash(),
                subject="agent",
            )

    def test_wrong_subject_rejected(self) -> None:
        ledger = ApprovalLedger()
        req = ledger.create(
            tool="file_delete",
            subject="agent",
            action_hash_value=self._hash(),
            reason="destructive",
        )
        ledger.grant(req.request_id)
        with pytest.raises(ApprovalError, match="subject"):
            ledger.resolve(
                request_id=req.request_id,
                action_hash_value=self._hash(),
                subject="other-agent",
            )

    def test_double_grant_rejected(self) -> None:
        ledger = ApprovalLedger()
        req = ledger.create(
            tool="file_delete",
            subject="agent",
            action_hash_value=self._hash(),
            reason="destructive",
        )
        ledger.grant(req.request_id)
        with pytest.raises(ApprovalError, match="already GRANTED"):
            ledger.grant(req.request_id)

    def test_resolve_pending_rejected(self) -> None:
        ledger = ApprovalLedger()
        req = ledger.create(
            tool="file_delete",
            subject="agent",
            action_hash_value=self._hash(),
            reason="destructive",
        )
        with pytest.raises(ApprovalError, match="not GRANTED"):
            ledger.resolve(
                request_id=req.request_id,
                action_hash_value=self._hash(),
                subject="agent",
            )

    def test_action_hash_is_argument_order_insensitive(self) -> None:
        a = action_hash(tool="t", arguments={"x": 1, "y": 2}, subject="s", correlation_id="c")
        b = action_hash(tool="t", arguments={"y": 2, "x": 1}, subject="s", correlation_id="c")
        assert a == b
