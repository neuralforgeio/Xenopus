"""Synthesizer + team + reliability tests."""

from collections.abc import Generator
from pathlib import Path

import pytest

from xenopus.persistence.reliability import ReliabilityStore
from xenopus.runtime.agent import AgentContract, AgentProfile, AgentResult
from xenopus.runtime.agent_pool import AgentPool, PoolLimits
from xenopus.runtime.aggregator import (
    Claim,
    ResultAggregator,
)
from xenopus.runtime.goal import GoalDraft, GoalManager
from xenopus.runtime.orchestrator import Orchestrator
from xenopus.runtime.plan import Plan, PlanEngine, TaskNode
from xenopus.runtime.synthesizer import SynthesisReport, SynthesisVerdict, Synthesizer
from xenopus.runtime.team import (
    TeamCoordinator,
    TeamError,
    TeamRunLedger,
    default_team,
)


def claim(
    key: str,
    content: str,
    evidence: int = 1,
    confidence: float = 0.8,
) -> Claim:
    return Claim(
        claim_id=f"c-{key}-{content}",
        task_id="t",
        role="r",
        key=key,
        content=content,
        evidence=tuple(f"e{i}" for i in range(evidence)),
        confidence=confidence,
    )


class TestSynthesizer:
    def test_clean_aggregation_synthesizes_trusted(self) -> None:
        report = ResultAggregator().aggregate([claim("a", "fact", evidence=2)])
        synthesis = Synthesizer().synthesize(report)
        assert synthesis.verdict is SynthesisVerdict.TRUSTED
        assert synthesis.uncertainties == ()

    def test_conflict_forces_uncertain_verdict(self) -> None:
        report = ResultAggregator().aggregate(
            [claim("a", "X", evidence=1), claim("a", "Y", evidence=1)]
        )
        synthesis = Synthesizer().synthesize(report)
        assert synthesis.verdict is SynthesisVerdict.UNCERTAIN
        assert any("disagree" in u for u in synthesis.uncertainties)

    def test_unevidenced_claim_is_uncertainty(self) -> None:
        report = ResultAggregator().aggregate([claim("a", "guess", evidence=0)])
        synthesis = Synthesizer().synthesize(report)
        assert synthesis.verdict is SynthesisVerdict.UNCERTAIN
        assert any("no evidence" in u for u in synthesis.uncertainties)

    def test_trusted_rejects_uncertainty_invariant(self) -> None:
        with pytest.raises(ValueError, match="TRUSTED"):
            SynthesisReport(
                synthesis_id="s",
                verdict=SynthesisVerdict.TRUSTED,
                findings=(),
                uncertainties=("doubt",),
                verification=(),
            )


class TestReliabilityStore:
    @pytest.fixture
    def store(self) -> Generator[ReliabilityStore]:
        s = ReliabilityStore()
        yield s
        s.close()

    def test_record_and_stats(self, store: ReliabilityStore) -> None:
        store.record(kind="tool", subject="file_read", success=True, duration_seconds=0.1)
        store.record(kind="tool", subject="file_read", success=False, duration_seconds=0.3)
        stats = store.stats_for("tool", "file_read")
        assert stats.invocations == 2
        assert stats.success_rate == pytest.approx(0.5)
        assert stats.avg_duration_seconds == pytest.approx(0.2)

    def test_best_ranks_speed_and_success(self, store: ReliabilityStore) -> None:
        for _ in range(5):
            store.record(kind="tool", subject="slow_ok", success=True, duration_seconds=10)
            store.record(
                kind="tool", subject="fast_slightly_worse", success=True, duration_seconds=0.1
            )
        best = store.best_for("tool")
        assert best is not None
        assert best.subject == "fast_slightly_worse"

    def test_empty_kind_returns_none(self, store: ReliabilityStore) -> None:
        assert store.best_for("ghost") is None

    def test_zero_invocations_default(self, store: ReliabilityStore) -> None:
        stats = store.stats_for("tool", "never-called")
        assert stats.invocations == 0
        assert stats.success_rate == 0.0


@pytest.fixture
def reliability() -> Generator[ReliabilityStore]:
    s = ReliabilityStore()
    yield s
    s.close()


class TestTeamCoordinator:
    def _setup_plan(self) -> tuple[str, Plan]:
        goals = GoalManager()
        goal = goals.create(GoalDraft(objective="O", success_criteria=["c"]))
        goals.activate(goal.id)
        plan = PlanEngine(goals).create(
            goal.id,
            [
                TaskNode(id="r1", summary="research A"),
                TaskNode(id="r2", summary="research B"),
            ],
        )
        return goal.id, plan

    @pytest.mark.asyncio
    async def test_team_run_produces_verified_synthesis(
        self, reliability: ReliabilityStore
    ) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            return AgentResult.completed(
                task_id=contract.task_id,
                role=profile.role,
                summary="done",
                artifacts={
                    "claims": [
                        {
                            "claim_key": f"finding-{contract.task_id}",
                            "claim_content": f"{contract.task_id} result",
                        }
                    ]
                },
                evidence=("obs-1",),
                confidence=0.9,
            )

        goal_id, plan = self._setup_plan()
        orchestrator = Orchestrator(
            pool=AgentPool(limits=PoolLimits()),
            runner=runner,
            reliability=reliability,
        )
        team = default_team(
            name="analysis team",
            goal_ref=goal_id,
            plan=plan,
            roles=frozenset({"researcher"}),
        )
        coordinator = TeamCoordinator(orchestrator=orchestrator)
        orchestration, synthesis = await coordinator.run(team, plan, correlation_id="corr-team")
        assert orchestration.overall == "COMPLETED"
        assert synthesis.verdict is SynthesisVerdict.TRUSTED
        assert len(synthesis.findings) == 2

        # Reliability recorded per agent run.
        assert reliability.stats_for("agent", "worker").invocations == 2

    @pytest.mark.asyncio
    async def test_team_ledger_records_chain(self) -> None:
        async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
            return AgentResult.completed(
                task_id=contract.task_id,
                role=profile.role,
                summary="ok",
                artifacts={"claims": [{"claim_key": "k", "claim_content": "v"}]},
                evidence=("obs-1",),
            )

        goal_id, plan = self._setup_plan()
        orchestrator = Orchestrator(pool=AgentPool(limits=PoolLimits()), runner=runner)
        team = default_team(name="t", goal_ref=goal_id, plan=plan, roles=frozenset({"worker"}))
        coordinator = TeamCoordinator(orchestrator=orchestrator)
        orchestration, synthesis = await coordinator.run(team, plan, correlation_id="c")
        ledger = TeamRunLedger()
        ledger.record(team, orchestration, synthesis)
        stored_team, stored_orch, stored_synth = ledger.get(team.team_id)
        assert stored_team.team_id == team.team_id
        assert stored_orch is orchestration
        assert stored_synth is synthesis
        with pytest.raises(TeamError, match="unknown team run"):
            ledger.get("team-ghost")

    def test_team_record_validation(self) -> None:
        from datetime import UTC, datetime

        from xenopus.runtime.team import TeamRecord

        with pytest.raises(ValueError, match="role"):
            TeamRecord(
                team_id="t",
                name="n",
                goal_ref="g",
                plan_id="p",
                allowed_roles=frozenset(),
                created_at=datetime.now(UTC).isoformat(),
            )


class TestExecutorReliabilityWiring:
    @pytest.mark.asyncio
    async def test_executor_records_tool_outcome(
        self, tmp_path: Path, reliability: ReliabilityStore
    ) -> None:

        from xenopus.persistence.journal import EventJournal
        from xenopus.runtime.approval import ApprovalLedger
        from xenopus.runtime.executor import Executor
        from xenopus.runtime.observer import ObservationLog
        from xenopus.runtime.permission import (
            PermissionDecision,
            PermissionEngine,
            PermissionRule,
        )
        from xenopus.runtime.risk import RiskEngine
        from xenopus.tools.gateway import ToolGateway
        from xenopus.tools.registry import ToolRegistry

        journal = EventJournal(tmp_path / "j.sqlite")
        registry = ToolRegistry()
        registry.register_builtin_files()
        gateway = ToolGateway(
            registry=registry,
            permissions=PermissionEngine(
                [PermissionRule(decision=PermissionDecision.ALLOW, subject="agent")]
            ),
            risk=RiskEngine(),
            approvals=ApprovalLedger(),
            subject="agent",
        )
        executor = Executor(
            gateway=gateway,
            journal=journal,
            observations=ObservationLog(),
            reliability=reliability,
        )
        try:
            from xenopus.tools.files import PathPolicy

            (tmp_path / "ws").mkdir()
            result = await executor.execute(
                "file_read",
                {"path": "missing.txt"},
                correlation_id="c1",
                path_policy=PathPolicy(tmp_path / "ws"),
            )
            assert result.ok is False  # not_found
            stats = reliability.stats_for("tool", "file_read")
            assert stats.invocations == 1
            assert stats.successes == 0
        finally:
            journal.close()
