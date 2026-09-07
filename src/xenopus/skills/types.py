"""Skill types: schema, 7-stage lifecycle, evaluation metrics, quality.

Skills are HOW-to knowledge (master prompt 28-30, 90). A skill can alter
future agent behavior, so the lifecycle is adversarial by design: no
stage ever reaches TRUSTED without evaluation evidence, and promotion
thresholds are deterministic (master prompt 134).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4


class SkillError(Exception):
    """Raised for skill misuse: unknown ids, illegal transitions."""


class SkillStage(StrEnum):
    """The 7-stage lifecycle (master prompt 29).

    CANDIDATE -> STAGED -> EVALUATED -> EXPERIMENTAL -> TRUSTED is the
    only forward path to trust; DEGRADED re-enters evaluation; every
    stage is reachable only through the documented gate.
    """

    CANDIDATE = "CANDIDATE"
    STAGED = "STAGED"
    EVALUATED = "EVALUATED"
    EXPERIMENTAL = "EXPERIMENTAL"
    TRUSTED = "TRUSTED"
    DEGRADED = "DEGRADED"
    SUPERSEDED = "SUPERSEDED"


LEGAL_SKILL_TRANSITIONS: dict[SkillStage, frozenset[SkillStage]] = {
    SkillStage.CANDIDATE: frozenset({SkillStage.STAGED, SkillStage.SUPERSEDED}),
    SkillStage.STAGED: frozenset(
        {SkillStage.EVALUATED, SkillStage.CANDIDATE, SkillStage.SUPERSEDED}
    ),
    SkillStage.EVALUATED: frozenset({SkillStage.EXPERIMENTAL, SkillStage.SUPERSEDED}),
    SkillStage.EXPERIMENTAL: frozenset(
        {SkillStage.TRUSTED, SkillStage.DEGRADED, SkillStage.SUPERSEDED}
    ),
    SkillStage.TRUSTED: frozenset({SkillStage.DEGRADED, SkillStage.SUPERSEDED}),
    SkillStage.DEGRADED: frozenset({SkillStage.EVALUATED, SkillStage.SUPERSEDED}),
    SkillStage.SUPERSEDED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class SkillSchema:
    """Declared skill structure (master prompt 28 field set).

    Invariants: name non-empty; procedure steps non-empty; success
    criteria non-empty. failure_modes document what can go wrong so the
    planner can reason about applicability.
    """

    name: str
    description: str
    trigger: str
    procedure: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    failure_modes: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "skill name must be non-empty"
            raise ValueError(msg)
        if not self.procedure:
            msg = f"skill {self.name!r} must declare at least one procedure step"
            raise ValueError(msg)
        if not self.success_criteria:
            msg = f"skill {self.name!r} must declare success criteria"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SkillEvaluation:
    """One recorded evaluation run (actual metrics, master prompt 30)."""

    ran_at: datetime
    successes: int
    failures: int
    corrections: int
    retries: int

    @property
    def success_rate(self) -> float:
        """Successes over total runs (0.0 when no runs)."""
        total = self.successes + self.failures
        return self.successes / total if total else 0.0


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """Registry row: schema + lifecycle + metrics + provenance."""

    skill_id: str
    schema: SkillSchema
    stage: SkillStage
    version: int
    created_at: datetime
    provenance: str
    evaluations: tuple[SkillEvaluation, ...] = ()
    trusted_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            msg = "skill version must be >= 1"
            raise ValueError(msg)

    @property
    def total_successes(self) -> int:
        """Aggregated successes across evaluations."""
        return sum(e.successes for e in self.evaluations)

    @property
    def total_failures(self) -> int:
        """Aggregated failures across evaluations."""
        return sum(e.failures for e in self.evaluations)

    @property
    def total_corrections(self) -> int:
        """Aggregated corrections across evaluations."""
        return sum(e.corrections for e in self.evaluations)

    @property
    def success_rate(self) -> float:
        """Lifetime success rate (0.0 when never evaluated)."""
        total = self.total_successes + self.total_failures
        return self.total_successes / total if total else 0.0

    @property
    def post_trust_evaluations(self) -> tuple[SkillEvaluation, ...]:
        """Evaluations recorded since the skill reached TRUSTED.

        Regression detection (master prompt 37) must judge recent
        behavior, not the lifetime average: a 0.87 lifetime rate with a
        0.10 recent rate is a regression.
        """
        if self.trusted_at is None:
            return ()
        return tuple(e for e in self.evaluations if e.ran_at >= self.trusted_at)

    @property
    def post_trust_success_rate(self) -> float:
        """Success rate since reaching TRUSTED (0.0 when none)."""
        evaluations = self.post_trust_evaluations
        successes = sum(e.successes for e in evaluations)
        failures = sum(e.failures for e in evaluations)
        total = successes + failures
        return successes / total if total else 0.0


# Deterministic promotion thresholds (master prompt 134: sandbox -> score -> trust).
TRUST_MIN_EVALUATIONS = 3
TRUST_MIN_SUCCESS_RATE = 0.75
EXPERIMENTAL_MIN_SUCCESS_RATE = 0.5
EXPERIMENTAL_MIN_EVALUATIONS = 1
DEGRADE_MAX_SUCCESS_RATE = 0.4
DEGRADE_MIN_EVALUATIONS = 2


@dataclass(frozen=True, slots=True)
class PromotionCheck:
    """Auditable gate decision for one stage transition."""

    from_stage: SkillStage
    to_stage: SkillStage
    allowed: bool
    reason: str


class SkillLifecycleGate:
    """Deterministic lifecycle gates (master prompt 134 — no auto-trust).

    Rules (fixed thresholds, no heuristics):
        STAGED -> EVALUATED: any evaluation recorded.
        EVALUATED -> EXPERIMENTAL: >= 1 evaluation, success rate >= 0.5.
        EXPERIMENTAL -> TRUSTED: >= 3 evaluations, success rate >= 0.75.
        TRUSTED -> DEGRADED: >= 2 evaluations, success rate <= 0.40
            (regression detection, master prompt 37).
    """

    def check(
        self,
        record: SkillRecord,
        target: SkillStage,
    ) -> PromotionCheck:
        """Evaluate a stage transition; never mutates."""
        current = record.stage
        if target not in LEGAL_SKILL_TRANSITIONS[current]:
            return PromotionCheck(
                current,
                target,
                False,
                f"illegal transition {current.value} -> {target.value}",
            )
        if target is SkillStage.EVALUATED:
            if not record.evaluations:
                return PromotionCheck(current, target, False, "no evaluations recorded")
            return PromotionCheck(current, target, True, "evaluation evidence present")
        if target is SkillStage.EXPERIMENTAL:
            if len(record.evaluations) < EXPERIMENTAL_MIN_EVALUATIONS:
                return PromotionCheck(current, target, False, "needs at least one evaluation")
            if record.success_rate < EXPERIMENTAL_MIN_SUCCESS_RATE:
                return PromotionCheck(
                    current,
                    target,
                    False,
                    f"success rate {record.success_rate:.2f} below 0.50",
                )
            return PromotionCheck(current, target, True, "evaluation metrics met")
        if target is SkillStage.TRUSTED:
            if len(record.evaluations) < TRUST_MIN_EVALUATIONS:
                return PromotionCheck(
                    current,
                    target,
                    False,
                    f"needs {TRUST_MIN_EVALUATIONS} evaluations, has {len(record.evaluations)}",
                )
            if record.success_rate < TRUST_MIN_SUCCESS_RATE:
                return PromotionCheck(
                    current,
                    target,
                    False,
                    f"success rate {record.success_rate:.2f} below 0.75",
                )
            return PromotionCheck(current, target, True, "trust thresholds met")
        if target is SkillStage.DEGRADED:
            recent = record.post_trust_evaluations
            if len(recent) < DEGRADE_MIN_EVALUATIONS:
                return PromotionCheck(
                    current,
                    target,
                    False,
                    "insufficient post-trust evaluations to judge regression",
                )
            rate = record.post_trust_success_rate
            if rate > DEGRADE_MAX_SUCCESS_RATE:
                return PromotionCheck(
                    current,
                    target,
                    False,
                    f"post-trust success rate {rate:.2f} above degrade threshold 0.40",
                )
            return PromotionCheck(current, target, True, "regression detected")
        # STAGED / CANDIDATE / SUPERSEDED: structural moves, no metrics gate.
        return PromotionCheck(current, target, True, "structural transition")


def new_skill_id() -> str:
    """Fresh skill id."""
    return f"skill-{uuid4().hex[:14]}"


def now_utc() -> datetime:
    """Single 'now' source for the skills layer."""
    return datetime.now(UTC)
