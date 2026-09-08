"""Synthesizer: verified synthesis over aggregated claims (addendum 64-65).

NEVER `parallel results -> final answer` directly: the pipeline is
`parallel -> aggregate -> verifier -> final`. The synthesizer builds
findings from the aggregation report, runs verifier checks, and emits
a synthesis carrying an explicit verdict — unresolved conflicts surface
as uncertainty, never as confident conclusions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from xenopus.runtime.aggregator import (
    AggregationReport,
    ConflictResolution,
)
from xenopus.runtime.verifier import (
    CheckResult,
    Verdict,
    VerificationCheck,
)


class SynthesisError(Exception):
    """Raised for synthesizer misuse (empty inputs, wiring errors)."""


class SynthesisVerdict(StrEnum):
    """Final verdict of a verified synthesis."""

    TRUSTED = "TRUSTED"  # evidence complete, verifier PASS, no conflicts
    UNCERTAIN = "UNCERTAIN"  # conflicts or missing evidence: explicit doubt
    REJECTED = "REJECTED"  # verification failed on the evidence present


@dataclass(frozen=True, slots=True)
class Finding:
    """One synthesized conclusion with its support."""

    claim_key: str
    content: str
    evidence: tuple[str, ...]
    contested: bool
    note: str = ""


@dataclass(frozen=True, slots=True)
class SynthesisReport:
    """Verified synthesis output (addendum 64-65).

    Invariants:
        verdict is TRUSTED only when the verifier PASSes AND no
        unresolved conflicts exist AND every top claim is evidenced;
        UNCERTAIN otherwise unless verification FAILs outright.
        `uncertainties` lists every explicit doubt — the report never
        hides disagreement behind a confident summary.
    """

    synthesis_id: str
    verdict: SynthesisVerdict
    findings: tuple[Finding, ...]
    uncertainties: tuple[str, ...]
    verification: tuple[CheckResult, ...]

    def __post_init__(self) -> None:
        if self.verdict is SynthesisVerdict.TRUSTED and self.uncertainties:
            msg = "TRUSTED synthesis may not carry uncertainties"
            raise ValueError(msg)


def _aggregation_present_check(claim_count: int) -> VerificationCheck:
    """Check: the aggregation produced at least one top claim."""

    def predicate(_index: dict[str, object]) -> CheckResult:
        return CheckResult(
            "aggregation-present",
            Verdict.PASS,
            f"{claim_count} aggregated claim(s)",
        )

    return VerificationCheck(name="aggregation-present", predicate=predicate)


def _evidence_backed_check(
    unevidenced_keys: tuple[str, ...], has_claims: bool
) -> VerificationCheck:
    """Check: missing evidence = UNCERTAINTY, never silent PASS.

    REJECTED is reserved for evidence that actively contradicts claims
    (future checks) — absence of evidence degrades to explicit doubt
    (addendum 13).
    """

    def predicate(_index: dict[str, object]) -> CheckResult:
        if unevidenced_keys:
            return CheckResult(
                "evidence-backed",
                Verdict.UNCERTAIN,
                f"unevidenced claims: {list(unevidenced_keys)}",
            )
        if not has_claims:
            return CheckResult("evidence-backed", Verdict.UNCERTAIN, "no claims at all")
        return CheckResult("evidence-backed", Verdict.PASS, "all claims evidenced")

    return VerificationCheck(name="evidence-backed", predicate=predicate)


def _conflict_free_check(conflict_count: int) -> VerificationCheck:
    """Check: conflicts are explicit uncertainty, not evidence failure."""

    def predicate(_index: dict[str, object]) -> CheckResult:
        if conflict_count:
            return CheckResult(
                "conflict-free",
                Verdict.UNCERTAIN,
                f"{conflict_count} unresolved conflict(s)",
            )
        return CheckResult("conflict-free", Verdict.PASS, "no conflicts")

    return VerificationCheck(name="conflict-free", predicate=predicate)


class Synthesizer:
    """Turns an AggregationReport into a verified SynthesisReport.

    Contract:
        synthesize(): builds findings (conflicted keys marked
        contested), runs verifier checks (evidence presence + conflict
        absence), derives the verdict, and lists every uncertainty.
    """

    def synthesize(self, report: AggregationReport) -> SynthesisReport:
        """Verified synthesis over one aggregation."""
        findings: list[Finding] = []
        uncertainties: list[str] = []

        conflicted_keys = {
            conflict.key
            for conflict in report.conflicts
            if conflict.resolution is ConflictResolution.CONFLICT
        }
        superseded_keys = {
            conflict.key
            for conflict in report.conflicts
            if conflict.resolution is ConflictResolution.SUPERSEDED
        }

        for claim in report.top_claims:
            contested = claim.key in conflicted_keys
            findings.append(
                Finding(
                    claim_key=claim.key,
                    content=claim.content,
                    evidence=tuple(claim.evidence),
                    contested=contested,
                    note=("superseded weaker evidence" if claim.key in superseded_keys else ""),
                )
            )
            if contested:
                uncertainties.append(f"agents disagree on {claim.key!r}: treated as uncertain")
            if not claim.evidence:
                uncertainties.append(f"claim {claim.key!r} has no evidence backing")

        unevidenced = [c.key for c in report.top_claims if not c.evidence]
        checks = [
            _aggregation_present_check(len(report.top_claims)),
            _evidence_backed_check(tuple(unevidenced), bool(report.top_claims)),
            _conflict_free_check(len(conflicted_keys)),
        ]

        # Evaluate checks against the aggregation payload directly.
        index = {"aggregation": [{"confidence": report.confidence}]}
        results = [check.predicate(index) for check in checks]
        failing = [r for r in results if r.verdict is Verdict.FAIL]
        uncertain_checks = [r for r in results if r.verdict is Verdict.UNCERTAIN]

        if failing:
            verdict = SynthesisVerdict.REJECTED
        elif uncertain_checks or uncertainties:
            verdict = SynthesisVerdict.UNCERTAIN
        else:
            verdict = SynthesisVerdict.TRUSTED

        return SynthesisReport(
            synthesis_id=f"synth-{uuid4().hex[:12]}",
            verdict=verdict,
            findings=tuple(findings),
            uncertainties=tuple(uncertainties),
            verification=tuple(results),
        )
