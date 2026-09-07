"""Skill registry tests: lifecycle gates, thresholds, regression."""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from xenopus.skills.registry import SkillRegistry
from xenopus.skills.types import (
    SkillError,
    SkillEvaluation,
    SkillLifecycleGate,
    SkillRecord,
    SkillSchema,
    SkillStage,
)


@pytest.fixture
def registry(tmp_path: Path) -> Generator[SkillRegistry]:
    r = SkillRegistry(tmp_path / "skills.sqlite")
    yield r
    r.close()


def schema(name: str = "deploy-nextjs") -> SkillSchema:
    return SkillSchema(
        name=name,
        description="Deploy a Next.js app safely",
        trigger="user asks to deploy",
        procedure=("run build", "run tests", "deploy"),
        constraints=("staging first",),
        success_criteria=("deployment live", "smoke test passes"),
    )


def eval_run(successes: int, failures: int) -> SkillEvaluation:
    return SkillEvaluation(
        ran_at=datetime.now(UTC),
        successes=successes,
        failures=failures,
        corrections=0,
        retries=0,
    )


class TestSkillRegistration:
    def test_register_starts_as_candidate(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="agent-1")
        assert record.stage is SkillStage.CANDIDATE

    def test_duplicate_name_refused(self, registry: SkillRegistry) -> None:
        registry.register(schema(), provenance="agent-1")
        with pytest.raises(SkillError, match="already registered"):
            registry.register(schema(), provenance="agent-2")

    def test_empty_provenance_refused(self, registry: SkillRegistry) -> None:
        with pytest.raises(SkillError, match="provenance"):
            registry.register(schema(), provenance="  ")

    def test_schema_requires_procedure(self) -> None:
        with pytest.raises(ValueError, match="procedure"):
            SkillSchema(
                name="empty",
                description="d",
                trigger="t",
                procedure=(),
                success_criteria=("x",),
            )

    def test_schema_requires_success_criteria(self) -> None:
        with pytest.raises(ValueError, match="success criteria"):
            SkillSchema(
                name="empty",
                description="d",
                trigger="t",
                procedure=("step",),
                success_criteria=(),
            )


class TestLifecycleGates:
    def test_staged_without_evaluation_refused(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="agent-1")
        registry.advance_stage(record.skill_id, SkillStage.STAGED)
        with pytest.raises(SkillError, match="no evaluations"):
            registry.advance_stage(record.skill_id, SkillStage.EVALUATED)

    def test_full_trust_path_with_strong_metrics(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="agent-1")
        rid = record.skill_id
        registry.advance_stage(rid, SkillStage.STAGED)
        registry.record_evaluation(rid, eval_run(8, 2))
        registry.advance_stage(rid, SkillStage.EVALUATED)
        registry.advance_stage(rid, SkillStage.EXPERIMENTAL)
        registry.record_evaluation(rid, eval_run(9, 1))
        registry.record_evaluation(rid, eval_run(9, 1))
        # 3 evaluations, lifetime success 26/30 = 0.867 >= 0.75
        registry.advance_stage(rid, SkillStage.TRUSTED)
        assert registry.get(rid).stage is SkillStage.TRUSTED

    def test_trust_requires_three_evaluations(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="agent-1")
        rid = record.skill_id
        registry.advance_stage(rid, SkillStage.STAGED)
        registry.record_evaluation(rid, eval_run(10, 0))
        registry.advance_stage(rid, SkillStage.EVALUATED)
        registry.advance_stage(rid, SkillStage.EXPERIMENTAL)
        # only 1 evaluation so far
        with pytest.raises(SkillError, match="evaluations"):
            registry.advance_stage(rid, SkillStage.TRUSTED)

    def test_trust_refuses_weak_success_rate(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="agent-1")
        rid = record.skill_id
        registry.advance_stage(rid, SkillStage.STAGED)
        registry.record_evaluation(rid, eval_run(6, 4))  # 0.60
        registry.advance_stage(rid, SkillStage.EVALUATED)
        registry.advance_stage(rid, SkillStage.EXPERIMENTAL)
        registry.record_evaluation(rid, eval_run(6, 4))
        registry.record_evaluation(rid, eval_run(6, 4))  # lifetime 0.60 < 0.75
        with pytest.raises(SkillError, match=r"below 0\.75"):
            registry.advance_stage(rid, SkillStage.TRUSTED)

    def test_regression_degrades_trusted_skill(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="agent-1")
        rid = record.skill_id
        registry.advance_stage(rid, SkillStage.STAGED)
        for _ in range(3):
            registry.record_evaluation(rid, eval_run(9, 1))
        registry.advance_stage(rid, SkillStage.EVALUATED)
        registry.advance_stage(rid, SkillStage.EXPERIMENTAL)
        registry.advance_stage(rid, SkillStage.TRUSTED)
        registry.record_evaluation(rid, eval_run(0, 10))
        registry.record_evaluation(rid, eval_run(1, 9))
        degraded = registry.advance_stage(rid, SkillStage.DEGRADED)
        assert degraded.stage is SkillStage.DEGRADED

    def test_illegal_transitions_refused(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="agent-1")
        with pytest.raises(SkillError, match="illegal transition"):
            registry.advance_stage(record.skill_id, SkillStage.TRUSTED)

    def test_supersede_is_terminal(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="agent-1")
        superseded = registry.supersede(record.skill_id)
        assert superseded.stage is SkillStage.SUPERSEDED
        with pytest.raises(SkillError, match="illegal transition"):
            registry.advance_stage(record.skill_id, SkillStage.CANDIDATE)

    def test_degraded_reenters_evaluation(self, registry: SkillRegistry) -> None:
        gate = SkillLifecycleGate()
        record = SkillRecord(
            skill_id="s",
            schema=schema(),
            stage=SkillStage.DEGRADED,
            version=1,
            created_at=datetime.now(UTC),
            provenance="p",
            evaluations=(eval_run(1, 9), eval_run(0, 10)),
        )
        decision = gate.check(record, SkillStage.EVALUATED)
        assert decision.allowed is True


class TestRegistryQueries:
    def test_search_by_stage_and_name(self, registry: SkillRegistry) -> None:
        trusted = registry.register(schema("alpha"), provenance="a")
        registry.register(schema("beta"), provenance="b")
        results = registry.search(stage=SkillStage.CANDIDATE, name_substring="alph")
        assert [r.skill_id for r in results] == [trusted.skill_id]

    def test_metrics_aggregate(self, registry: SkillRegistry) -> None:
        record = registry.register(schema(), provenance="a")
        registry.record_evaluation(record.skill_id, eval_run(8, 2))
        registry.record_evaluation(record.skill_id, eval_run(6, 4))
        current = registry.get(record.skill_id)
        assert current.total_successes == 14
        assert current.success_rate == pytest.approx(14 / 20)
