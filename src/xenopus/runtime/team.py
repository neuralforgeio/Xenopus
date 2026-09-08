"""Team coordination: one orchestrator run aggregated and synthesized.

A team (addendum 16) is a COMPOSITION: coordinator + specialists +
verifier + synthesizer over the existing engines — no new execution
engine, no duplicated agent brain. TeamRecord captures the team's
identity, bounds, and shared references; TeamCoordinator drives
plan -> orchestrate -> aggregate -> synthesize end-to-end with an
explicit verdict and conflict surfacing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from xenopus.runtime.agent import AgentResult
from xenopus.runtime.aggregator import Claim, ResultAggregator
from xenopus.runtime.orchestrator import OrchestrationReport, Orchestrator
from xenopus.runtime.plan import Plan
from xenopus.runtime.synthesizer import SynthesisReport, Synthesizer


class TeamError(Exception):
    """Raised for team misuse: invalid composition, illegal states."""


@dataclass(frozen=True, slots=True)
class TeamRecord:
    """Identity and bounds of one agent team (addendum 16).

    Contract:
        team_id: stable identity.
        plan_id/goal_ref: the team's task graph binding.
        allowed_roles: the specialist roles the team may use (pool
            admission still applies per agent).
        budget_ref: the shared envelope reference.
    """

    team_id: str
    name: str
    goal_ref: str
    plan_id: str
    allowed_roles: frozenset[str]
    created_at: str
    coordinator_subject: str = "team-coordinator"

    def __post_init__(self) -> None:
        if not self.name.strip():
            msg = "team name must be non-empty"
            raise ValueError(msg)
        if not self.allowed_roles:
            msg = "a team must declare at least one role"
            raise ValueError(msg)


class TeamCoordinator:
    """Drives a plan through orchestration into verified synthesis.

    Contract:
        run(): executes the plan (via the injected Orchestrator),
            extracts claims from completed results, aggregates by
            evidence, synthesizes with verification, and returns the
            full chain (orchestration + synthesis) for audit.

    Failure modes:
        TeamError when the orchestration fails to launch (cost gate
        refusals propagate — the team never silently degrades to
        unverified partial answers).
    """

    def __init__(
        self,
        *,
        orchestrator: Orchestrator,
        aggregator: ResultAggregator | None = None,
        synthesizer: Synthesizer | None = None,
        claim_extractor: Any | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._aggregator = aggregator or ResultAggregator()
        self._synthesizer = synthesizer or Synthesizer()
        self._claim_extractor = claim_extractor or _default_claim_extractor

    async def run(
        self,
        team: TeamRecord,
        plan: Plan,
        *,
        correlation_id: str,
    ) -> tuple[OrchestrationReport, SynthesisReport]:
        """Execute and synthesize; returns the full evidence chain."""
        orchestration = await self._orchestrator.execute(plan, correlation_id=correlation_id)
        results: list[AgentResult] = [
            result for layer in orchestration.layers for result in layer.results
        ]
        claims = self._claim_extractor(results)
        aggregation = self._aggregator.aggregate(claims)
        synthesis = self._synthesizer.synthesize(aggregation)
        return orchestration, synthesis


def default_team(
    *,
    name: str,
    goal_ref: str,
    plan: Plan,
    roles: frozenset[str],
) -> TeamRecord:
    """Build a TeamRecord for one plan run."""
    return TeamRecord(
        team_id=f"team-{uuid4().hex[:12]}",
        name=name,
        goal_ref=goal_ref,
        plan_id=plan.plan_id,
        allowed_roles=roles,
        created_at=datetime.now(UTC).isoformat(),
    )


@dataclass(slots=True)
class TeamRunLedger:
    """Lightweight registry of team runs (audit spine, in-memory)."""

    _entries: dict[str, tuple[TeamRecord, OrchestrationReport, SynthesisReport]] = field(
        default_factory=dict
    )

    def record(
        self,
        team: TeamRecord,
        orchestration: OrchestrationReport,
        synthesis: SynthesisReport,
    ) -> str:
        """Store one run's chain; returns the correlation anchor."""
        self._entries[team.team_id] = (team, orchestration, synthesis)
        return team.team_id

    def get(self, team_id: str) -> tuple[TeamRecord, OrchestrationReport, SynthesisReport]:
        """Fetch a run chain; TeamError when unknown."""
        try:
            return self._entries[team_id]
        except KeyError as err:
            msg = f"unknown team run: {team_id!r}"
            raise TeamError(msg) from err


def _default_claim_extractor(results: list[AgentResult]) -> list[Claim]:
    """Default extraction using the aggregator's artifact contract."""
    from xenopus.runtime.aggregator import claims_from_results

    return claims_from_results(results)
