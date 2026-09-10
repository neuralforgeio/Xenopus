"""Self-improvement loop: deterministic consolidation (ADR-026).

Closes Reflect -> Learn -> Consolidate from the core loop. The
ReflectionLoop reads EMPIRICAL evidence only (reliability records,
ADR-018), derives a closed set of statistical insights, and writes
them through the EXISTING Phase 5 gates — MemoryStore candidates
under the SAME PromotionGate, skill evaluations under the SAME
SkillLifecycleGate ladder. No model calls, no free-form reflections,
no self-modification: improvement is read-only over past outcomes
and write-only through the gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from xenopus.memory.store import MemoryStore
from xenopus.memory.types import (
    Evidence,
    MemoryDraft,
    MemoryKind,
    MemoryScope,
    MemorySearchQuery,
)
from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.reliability import ReliabilityStats, ReliabilityStore
from xenopus.runtime.events import Event, EventType
from xenopus.skills.registry import SkillRegistry
from xenopus.skills.types import SkillEvaluation, SkillStage

SCOPE_REF = "xenopus-self"
MIN_FAILURES_FOR_INSIGHT = 3
MAX_SUCCESS_RATE_FOR_INSIGHT = 0.9
MAX_MEMORY_CANDIDATES_PER_RUN = 10
MAX_SKILL_EVALUATIONS_PER_RUN = 10
INSIGHT_KINDS = ("agent", "tool")
MEMORY_TTL_SECONDS = 30 * 24 * 3600.0


class ReflectionError(Exception):
    """Raised for loop misuse (never wraps store failures — those abort)."""


@dataclass(frozen=True, slots=True)
class Insight:
    """One derived statistical insight (pre-write, gate-checked later)."""

    subject: str
    kind: str  # reliability kind: "tool" or "agent"
    stats: ReliabilityStats
    fingerprint: str

    def draft(self, correlation_id: str) -> MemoryDraft:
        """Render the insight as a CANDIDATE memory draft."""
        if self.kind == "tool":
            content = (
                f"tool {self.subject} failed {self.stats.failures} of "
                f"{self.stats.invocations} recorded invocation(s) "
                f"(rate {self.stats.success_rate:.2f}); prefer alternatives "
                "or review constraints"
            )
            memory_kind = MemoryKind.FAILURE
        else:
            content = (
                f"agent role {self.subject} failed {self.stats.failures} of "
                f"{self.stats.invocations} recorded run(s) "
                f"(rate {self.stats.success_rate:.2f}); review role fit"
            )
            memory_kind = MemoryKind.SEMANTIC
        return MemoryDraft(
            content=content,
            kind=memory_kind,
            scope=MemoryScope.WORKSPACE,
            scope_ref=SCOPE_REF,
            source="reflection-loop",
            confidence=min(0.9, 0.5 + self.stats.invocations / 100.0),
            evidence=Evidence(
                source="reflection-loop",
                journal_correlation_id=correlation_id,
            ),
            ttl_seconds=MEMORY_TTL_SECONDS,
        )


@dataclass(frozen=True, slots=True)
class ReflectionReport:
    """Outcome of one consolidation run (fully auditable)."""

    correlation_id: str
    proposed: list[str]
    promoted: list[str]
    gate_refused: list[str]
    skill_evaluations: list[str]
    skipped_duplicates: int
    aborted: str | None = None


def _fingerprint(kind: str, subject: str) -> str:
    """Stable content-independent id of one insight class."""
    return f"{kind}:{subject}:{MIN_FAILURES_FOR_INSIGHT}"


def _fingerprint_of_content(content: str) -> str | None:
    """Re-derive an insight fingerprint from stored memory content.

    Stored insight content starts with ``tool <subject> ...`` or
    ``agent role <subject> ...`` (Insight.draft renders it); other
    content is not loop-written and returns None.
    """
    parts = content.strip().split()
    if len(parts) >= 2 and parts[0] == "tool":
        return _fingerprint("tool", parts[1])
    if len(parts) >= 3 and parts[0] == "agent" and parts[1] == "role":
        return _fingerprint("agent", parts[2])
    return None


class ReflectionLoop:
    """Deterministic consolidation over recorded evidence (ADR-026).

    Contract:
        run(): one consolidation pass. Derives insights from the
        reliability store, de-duplicates against existing xenopus-self
        memories, writes at most MAX_MEMORY_CANDIDATES_PER_RUN
        candidates through the promotion gate (refusals recorded,
        never overridden), and appends skill evaluations for staged
        skills with recorded outcomes. Returns a full report; every
        outcome is journaled.

    Invariants:
        - Evidence is statistical (reliability records), never prose.
        - A gate refusal is RECORDED, never retried or overridden.
        - Writes land only in WORKSPACE/xenopus-self.
        - Idempotent: unchanged data -> no new proposals.
        - Any store error aborts the run (journaled) without raising
          into the caller's tick loop.

    Failure modes:
        ReflectionError for construction-time misuse; store failures
        abort the run and surface via the report's ``aborted`` field.
    """

    def __init__(
        self,
        *,
        reliability: ReliabilityStore,
        memory: MemoryStore,
        skills: SkillRegistry | None = None,
        journal: EventJournal | None = None,
    ) -> None:
        self._reliability = reliability
        self._memory = memory
        self._skills = skills
        self._journal = journal

    def run(self) -> ReflectionReport:
        """One consolidation pass over the recorded evidence."""
        correlation_id = f"reflect-{new_correlation_id()}"
        proposed: list[str] = []
        promoted: list[str] = []
        refused: list[str] = []
        evaluations: list[str] = []
        duplicates = 0
        try:
            known = self._known_fingerprints()
            for insight in self._derive_insights():
                if len(proposed) >= MAX_MEMORY_CANDIDATES_PER_RUN:
                    break
                if insight.fingerprint in known:
                    duplicates += 1
                    continue
                record = self._memory.create(insight.draft(correlation_id))
                proposed.append(record.item.memory_id)
                try:
                    self._memory.promote(record.item.memory_id)
                    promoted.append(record.item.memory_id)
                except Exception as err:  # gate refusal: record, never override
                    refused.append(f"{record.item.memory_id}: {err}")
            if self._skills is not None:
                evaluations = self._evaluate_staged_skills()
        except Exception as err:  # abort cleanly; a hosting tick survives
            report = ReflectionReport(
                correlation_id, proposed, promoted, refused, evaluations, duplicates, str(err)
            )
            self._journal_run(report)
            return report
        report = ReflectionReport(
            correlation_id, proposed, promoted, refused, evaluations, duplicates, None
        )
        self._journal_run(report)
        return report

    def _derive_insights(self) -> list[Insight]:
        """Closed-set statistical rules over reliability records."""
        insights: list[Insight] = []
        for kind in INSIGHT_KINDS:
            for subject in self._reliability.subjects_for(kind):
                stats = self._reliability.stats_for(kind=kind, subject=subject)
                if stats.failures >= MIN_FAILURES_FOR_INSIGHT and (
                    stats.success_rate < MAX_SUCCESS_RATE_FOR_INSIGHT
                ):
                    insights.append(
                        Insight(
                            subject=subject,
                            kind=kind,
                            stats=stats,
                            fingerprint=_fingerprint(kind, subject),
                        )
                    )
        return insights

    def _known_fingerprints(self) -> set[str]:
        """Fingerprints of insights already present (idempotence)."""
        rows = self._memory.search(
            MemorySearchQuery(
                scope=MemoryScope.WORKSPACE,
                scope_ref=SCOPE_REF,
                include_stale=True,
                limit=500,
            )
        )
        known: set[str] = set()
        for record in rows:
            fingerprint = _fingerprint_of_content(record.item.content)
            if fingerprint is not None:
                known.add(fingerprint)
        return known

    def _evaluate_staged_skills(self) -> list[str]:
        """Append one evaluation per STAGED skill with recorded outcomes."""
        evaluated: list[str] = []
        if self._skills is None:
            return evaluated
        staged = self._skills.search(stage=SkillStage.STAGED, limit=100)
        for record in staged:
            if len(evaluated) >= MAX_SKILL_EVALUATIONS_PER_RUN:
                break
            stats = self._reliability.stats_for(kind="skill", subject=record.schema.name)
            if stats.invocations == 0:
                continue  # no recorded outcomes: nothing to evaluate with
            self._skills.record_evaluation(
                record.skill_id,
                SkillEvaluation(
                    ran_at=datetime.now(UTC),
                    successes=stats.successes,
                    failures=stats.failures,
                    corrections=0,
                    retries=0,
                ),
            )
            evaluated.append(record.skill_id)
        return evaluated

    def _journal_run(self, report: ReflectionReport) -> None:
        """Evidence for the audit trail (both outcomes)."""
        if self._journal is None:
            return
        payload: dict[str, object] = {
            "loop": "reflection",
            "proposed": len(report.proposed),
            "promoted": len(report.promoted),
            "gate_refused": len(report.gate_refused),
            "skill_evaluations": len(report.skill_evaluations),
            "duplicates_skipped": report.skipped_duplicates,
        }
        if report.aborted is not None:
            payload["aborted"] = report.aborted
        self._journal.append(
            Event(
                type=(
                    EventType.NOTIFICATION_SUPPRESSED
                    if report.aborted is not None
                    else EventType.NOTIFICATION_DELIVERED
                ),
                correlation_id=report.correlation_id,
                payload=payload,
            )
        )
