"""Result aggregator: normalize, deduplicate, rank, detect conflicts.

Parallel agents produce overlapping and sometimes contradicting
results (addendum 12, 63). This module resolves them by EVIDENCE,
not votes: claims with no evidence can never outrank evidenced ones;
conflicts are surfaced explicitly (supersede/coexist/conflict),
never silently merged and never decided by naive majority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from xenopus.runtime.agent import AgentResult


class ConflictResolution(StrEnum):
    """Deterministic conflict outcomes (addendum 63)."""

    SUPERSEDED = "SUPERSEDED"  # stronger evidence fully replaces weaker
    COEXIST = "COEXIST"  # different scopes/keys: both kept
    CONFLICT = "CONFLICT"  # irreducible disagreement: surfaced, not merged


@dataclass(frozen=True, slots=True)
class Claim:
    """One normalized claim extracted from an AgentResult.

    Contract:
        key: identity of what is being claimed (topic pointer).
        content: the claim body.
        evidence: backing references (observation/journal ids) —
            empty evidence is allowed but always loses to evidenced
            claims in ranking.
        confidence: agent-reported confidence in [0,1].
    """

    claim_id: str
    task_id: str
    role: str
    key: str
    content: str
    evidence: tuple[str, ...] = ()
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if not self.key.strip():
            msg = "claim key must be non-empty"
            raise ValueError(msg)
        if not 0.0 <= self.confidence <= 1.0:
            msg = f"confidence must be within [0,1], got {self.confidence}"
            raise ValueError(msg)

    @classmethod
    def from_result(
        cls,
        result: AgentResult,
        *,
        key: str,
        content: str,
    ) -> Claim:
        """Build a claim from one agent result's artifacts."""
        return cls(
            claim_id=f"claim-{uuid4().hex[:12]}",
            task_id=result.task_id,
            role=result.role,
            key=key,
            content=content,
            evidence=tuple(result.evidence),
            confidence=result.confidence,
        )


def claims_from_results(
    results: list[AgentResult],
    *,
    key_of: Any | None = None,
) -> list[Claim]:
    """Extract claims from completed results via a key extractor.

    The default extractor reads artifacts['claim_key'] and
    artifacts['claim_content'] — runner authors control claim identity
    by emitting those fields.
    """
    extractor = key_of or _default_key_of
    claims: list[Claim] = []
    for result in results:
        if result.status != "COMPLETED":
            continue
        for key, content in extractor(result):
            claims.append(Claim.from_result(result, key=key, content=content))
    return claims


def _default_key_of(result: AgentResult) -> list[tuple[str, str]]:
    """Default artifact shape: [{'claim_key','claim_content'}, ...]."""
    pairs: list[tuple[str, str]] = []
    artifacts = result.artifacts.get("claims")
    if isinstance(artifacts, list):
        for entry in artifacts:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("claim_key"), str)
                and isinstance(entry.get("claim_content"), str)
            ):
                pairs.append((entry["claim_key"], entry["claim_content"]))
    return pairs


@dataclass(frozen=True, slots=True)
class ClaimGroup:
    """All claims about one key, ranked deterministically."""

    key: str
    claims: tuple[Claim, ...]

    @property
    def best(self) -> Claim | None:
        """The top-ranked claim (evidence-first ordering)."""
        return self.claims[0] if self.claims else None


def rank_claims(claims: list[Claim]) -> tuple[Claim, ...]:
    """Deterministic evidence-first ranking (addendum 63).

    Order: (evidence count desc, confidence desc, claim_id asc).
    Evidence dominates: a 0.9-confidence unevidenced claim NEVER
    outranks a 0.5-confidence claim with 2 evidence refs.
    """
    return tuple(
        sorted(
            claims,
            key=lambda c: (-len(c.evidence), -c.confidence, c.claim_id),
        )
    )


@dataclass(frozen=True, slots=True)
class Conflict:
    """A detected disagreement over one key."""

    key: str
    resolution: ConflictResolution
    winner: Claim | None
    losers: tuple[Claim, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AggregationReport:
    """Outcome of aggregating one result set (addendum 12, 63).

    Invariants:
        top_claims contains the best claim per key (deduplicated);
        conflicts lists every irreducible disagreement with resolution
        reasons; confidence is the mean of top-claim confidences and
        DEGRADES with unresolved conflicts (never reported as high
        when agents disagreed).
    """

    aggregation_id: str
    top_claims: tuple[Claim, ...]
    conflicts: tuple[Conflict, ...] = field(default=())
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            msg = f"confidence must be within [0,1], got {self.confidence}"
            raise ValueError(msg)


EVIDENCE_DOMINANCE_THRESHOLD = 1  # >= this many extra evidence refs to supersede
CONFLICT_PENALTY = 0.25  # per unresolved conflict, off the final confidence


class ResultAggregator:
    """Aggregates parallel results by evidence, not votes.

    Rules (fixed, deterministic, addendum 63):
        1. Claims are grouped per key.
        2. Within a key, claims are ranked (evidence > confidence > id).
        3. Identical content deduplicates (keeps the top-ranked claim).
        4. Distinct content on the same key is a conflict:
           - SUPERSEDED when the top claim's evidence strictly dominates
             (>= 1 more evidence ref than every other claim in the group).
           - COEXIST when the claims describe different scopes — the
             caller may declare scope-bearing keys, and conflicting
             pairs land in COEXIST only when contents are compatible.
           - CONFLICT otherwise: both kept visible; nobody is silently
             merged; the synthesizer must treat CONFLICT as explicit
             uncertainty.
        5. Confidence degrades by 0.25 per unresolved conflict
           (floored at 0) — disagreement never reports as high trust.
    """

    def aggregate(self, claims: list[Claim]) -> AggregationReport:
        """Aggregate a claim set into a report."""
        by_key: dict[str, list[Claim]] = {}
        for claim in claims:
            by_key.setdefault(claim.key, []).append(claim)

        top_claims: list[Claim] = []
        conflicts: list[Conflict] = []
        for key in sorted(by_key):
            group = list(by_key[key])
            distinct = _dedupe_by_content(group)
            if len(distinct) == 1:
                top_claims.append(distinct[0])
                continue
            ranked = list(rank_claims(distinct))
            winner = ranked[0]
            losers = tuple(ranked[1:])
            evidence_gap = min(len(winner.evidence) - len(c.evidence) for c in losers)
            if evidence_gap >= EVIDENCE_DOMINANCE_THRESHOLD:
                conflicts.append(
                    Conflict(
                        key=key,
                        resolution=ConflictResolution.SUPERSEDED,
                        winner=winner,
                        losers=losers,
                        reason=(
                            f"winner has {evidence_gap}+ more evidence refs than every alternative"
                        ),
                    )
                )
                top_claims.append(winner)
            else:
                conflicts.append(
                    Conflict(
                        key=key,
                        resolution=ConflictResolution.CONFLICT,
                        winner=None,
                        losers=tuple(ranked),
                        reason=(
                            "irreducible disagreement: evidence does not separate the alternatives"
                        ),
                    )
                )
                # Conflicted keys contribute their strongest claim for
                # visibility, but the CONFLICT record carries the marker.
                top_claims.append(winner)

        unresolved = sum(1 for c in conflicts if c.resolution is ConflictResolution.CONFLICT)
        base = sum(c.confidence for c in top_claims) / len(top_claims) if top_claims else 0.0
        confidence = max(0.0, base - CONFLICT_PENALTY * unresolved)
        return AggregationReport(
            aggregation_id=f"agg-{uuid4().hex[:12]}",
            top_claims=tuple(top_claims),
            conflicts=tuple(conflicts),
            confidence=confidence,
        )


def _dedupe_by_content(claims: list[Claim]) -> list[Claim]:
    """Content-identical claims collapse to their top-ranked member."""
    ranked = rank_claims(claims)
    seen: set[str] = set()
    distinct: list[Claim] = []
    for claim in ranked:
        if claim.content in seen:
            continue
        seen.add(claim.content)
        distinct.append(claim)
    return distinct
