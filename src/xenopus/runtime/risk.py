"""Risk engine: deterministic AUTO / APPROVAL / DENY from decision tables.

Risk is computed from declared factors (reversibility, privilege,
destructiveness, data sensitivity, blast radius — master prompt 69) using
a table, not vibes. HIGH risk with no approval path is DENY, never AUTO.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from xenopus.tools.contracts import RiskLevel


class RiskOutcome(StrEnum):
    """What the pipeline must do with the action."""

    AUTO = "AUTO"  # proceed without a human
    APPROVAL = "APPROVAL"  # proceed only with a valid approval
    DENY = "DENY"  # never proceed


@dataclass(frozen=True, slots=True)
class RiskFactors:
    """Declared risk inputs for one action.

    Contract:
        destructiveness: does the action destroy data (delete/overwrite)?
        external_side_effects: does it touch the world outside the workspace
            (network writes, emails, deployments)?
        reversible: can the effect be undone within the rollback budget?
        sensitive_data: does it read secrets/PII-adjacent state?
        blast_radius: how much of the system the action can affect.
    """

    destructiveness: bool = False
    external_side_effects: bool = False
    reversible: bool = True
    sensitive_data: bool = False
    blast_radius: RiskLevel = RiskLevel.LOW

    def score(self) -> int:
        """Deterministic numeric risk score (higher = riskier).

        Every factor adds a fixed weight; the weights are the decision
        table. Reversible actions subtract nothing; irreversibility is
        the dominant term (master prompt 69: reversibility first).
        """
        score = 0
        if self.destructiveness:
            score += 3
        if self.external_side_effects:
            score += 2
        if not self.reversible:
            score += 4
        if self.sensitive_data:
            score += 2
        score += int(self.blast_radius)  # LOW=0, MEDIUM=1, HIGH=2
        return score


class RiskEngine:
    """Maps risk scores to outcomes via fixed thresholds.

    Thresholds (documented, deterministic):
        score >= 8 and destructive/irreversible combination -> DENY
        score >= 4  -> APPROVAL
        otherwise   -> AUTO

    The DENY row also fires for destructive AND irreversible actions
    regardless of score — a delete with no rollback path is never AUTO.
    """

    APPROVAL_THRESHOLD = 4
    DENY_THRESHOLD = 8

    def evaluate(self, factors: RiskFactors, declared_risk: RiskLevel) -> RiskOutcome:
        """Combine static tool risk with dynamic factors -> outcome."""
        score = factors.score() + int(declared_risk)
        if factors.destructiveness and not factors.reversible:
            return RiskOutcome.DENY
        if score >= self.DENY_THRESHOLD:
            return RiskOutcome.DENY
        if score >= self.APPROVAL_THRESHOLD:
            return RiskOutcome.APPROVAL
        return RiskOutcome.AUTO
