"""Verifier: evaluates evidence into PASS / FAIL / UNCERTAIN.

UNCERTAIN is not PASS (master prompt 19): a check with no supporting
evidence is UNCERTAIN, and an UNCERTAIN verdict blocks completion claims.
The adversarial re-check actively searches for disproof (master prompt 20).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from xenopus.runtime.observer import Observation


class Verdict(StrEnum):
    """Evaluation outcomes; only PASS permits completion claims."""

    PASS = "PASS"  # noqa: S105 — verdict label, not a credential
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One evaluated check.

    Contract:
        name: what was checked.
        verdict: PASS only when positive evidence exists; absent or
            ambiguous evidence is UNCERTAIN, never PASS.
        detail: human-readable justification for the verdict.
    """

    name: str
    verdict: Verdict
    detail: str


CheckFn = Callable[[dict[str, Any]], CheckResult]


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Aggregated verdict over all checks.

    Invariants:
        overall is FAIL if any check fails; else UNCERTAIN if any check
        is UNCERTAIN; else PASS. PASS requires every check to PASS.
    """

    checks: tuple[CheckResult, ...]

    @property
    def overall(self) -> Verdict:
        """Worst-first aggregation: FAIL > UNCERTAIN > PASS."""
        verdicts = {c.verdict for c in self.checks}
        if Verdict.FAIL in verdicts:
            return Verdict.FAIL
        if Verdict.UNCERTAIN in verdicts:
            return Verdict.UNCERTAIN
        return Verdict.PASS


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """Declarative check: a predicate evaluated over observed evidence."""

    name: str
    predicate: CheckFn


class Verifier:
    """Runs declarative checks against observations.

    Contract:
        verify(): evaluates every check against the evidence map
        (observations indexed by kind). A check may return FAIL on
        explicit counter-evidence, PASS only on positive evidence,
        and UNCERTAIN when evidence is missing.
    """

    def verify(
        self,
        checks: list[VerificationCheck],
        observations: list[Observation],
    ) -> VerificationReport:
        """Evaluate all checks; aggregate worst-first."""
        results: list[CheckResult] = []
        for check in checks:
            results.append(check.predicate(_index(observations)))
        return VerificationReport(checks=tuple(results))


def _index(observations: list[Observation]) -> dict[str, Any]:
    """Index observations by kind for predicate consumption."""
    indexed: dict[str, Any] = {}
    for obs in observations:
        indexed.setdefault(obs.kind, []).append(obs.content)
    return indexed


def require_observation(name: str, kind: str) -> VerificationCheck:
    """Check asserting at least one observation of ``kind`` exists.

    Missing evidence -> UNCERTAIN (never PASS without evidence).
    """

    def predicate(index: dict[str, Any]) -> CheckResult:
        entries = index.get(kind, [])
        if entries:
            return CheckResult(name, Verdict.PASS, f"{len(entries)} observation(s)")
        return CheckResult(name, Verdict.UNCERTAIN, f"no observations of kind {kind!r}")

    return VerificationCheck(name=name, predicate=predicate)


def require_success_result(name: str, tool: str) -> VerificationCheck:
    """Check asserting ``tool`` produced at least one ok result.

    A failed result is counter-evidence -> FAIL; no result -> UNCERTAIN.
    """

    def predicate(index: dict[str, Any]) -> CheckResult:
        for entry in index.get("tool_result", []):
            if entry.get("tool") == tool:
                if entry.get("ok"):
                    return CheckResult(name, Verdict.PASS, f"{tool} succeeded")
                return CheckResult(name, Verdict.FAIL, f"{tool} failed: {entry.get('error_code')}")
        return CheckResult(name, Verdict.UNCERTAIN, f"{tool} never ran")

    return VerificationCheck(name=name, predicate=predicate)


def adversarial_recheck(
    name: str,
    tool: str,
) -> VerificationCheck:
    """Adversarial pass: search for any FAILED attempt of ``tool``.

    Master prompt 20 — 'how could this result be wrong?': a failed
    attempt hidden behind a later retry is FAIL; a crash-shaped entry
    is FAIL; only clean success across all attempts passes.
    """

    def predicate(index: dict[str, Any]) -> CheckResult:
        attempts = [e for e in index.get("tool_result", []) if e.get("tool") == tool]
        if not attempts:
            return CheckResult(name, Verdict.UNCERTAIN, f"{tool} never ran")
        failures = [e for e in attempts if not e.get("ok")]
        if failures:
            return CheckResult(
                name,
                Verdict.FAIL,
                f"found {len(failures)} failing attempt(s) of {tool}",
            )
        return CheckResult(name, Verdict.PASS, "no failing attempts found")

    return VerificationCheck(name=name, predicate=predicate)
