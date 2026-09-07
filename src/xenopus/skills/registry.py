"""SQLite skill registry: staged lifecycle with deterministic gates.

Every stage transition passes the SkillLifecycleGate — the registry
refuses illegal or unevidenced promotions (master prompt 29/134).
Evaluations are appended as data; metrics are computed, never edited.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from xenopus.skills.types import (
    SkillError,
    SkillEvaluation,
    SkillLifecycleGate,
    SkillRecord,
    SkillSchema,
    SkillStage,
    new_skill_id,
)

SKILLS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    definition TEXT NOT NULL,
    stage TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    provenance TEXT NOT NULL,
    evaluations TEXT NOT NULL DEFAULT '[]',
    trusted_at TEXT
)
"""

SKILLS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_skills_stage ON skills (stage, name)
"""


class SkillRegistry:
    """Skill persistence + lifecycle enforcement.

    Contract:
        register(): stores a CANDIDATE (never trusted on arrival).
        record_evaluation(): appends metrics (immutable history).
        advance_stage(): applies the lifecycle gate; raises on refusal.
        search(): metadata queries by stage/name (planner support).

    Failure modes:
        SkillError for unknown ids, duplicate names, illegal or
        unevidenced stage transitions.
    """

    def __init__(self, path: Path, *, gate: SkillLifecycleGate | None = None) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(SKILLS_TABLE_DDL)
        self._conn.execute(SKILLS_INDEX_DDL)
        self._conn.commit()
        self._gate = gate or SkillLifecycleGate()

    def register(self, schema: SkillSchema, *, provenance: str) -> SkillRecord:
        """Store a new skill as CANDIDATE."""
        if not provenance.strip():
            msg = "skill provenance must be non-empty"
            raise SkillError(msg)
        existing = self._conn.execute(
            "SELECT 1 FROM skills WHERE name = ?", (schema.name,)
        ).fetchone()
        if existing:
            msg = f"skill name already registered: {schema.name!r}"
            raise SkillError(msg)
        skill_id = new_skill_id()
        with self._conn:
            self._conn.execute(
                "INSERT INTO skills (skill_id, name, definition, stage, version, "
                "created_at, provenance, evaluations, trusted_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, '[]', NULL)",
                (
                    skill_id,
                    schema.name,
                    json.dumps(asdict(schema)),
                    SkillStage.CANDIDATE.value,
                    datetime.now(UTC).isoformat(),
                    provenance,
                ),
            )
        return self.get(skill_id)

    def record_evaluation(
        self,
        skill_id: str,
        evaluation: SkillEvaluation,
    ) -> SkillRecord:
        """Append one evaluation (history is immutable)."""
        record = self.get(skill_id)
        evaluations = (*record.evaluations, evaluation)
        with self._conn:
            self._conn.execute(
                "UPDATE skills SET evaluations = ? WHERE skill_id = ?",
                (
                    json.dumps([_evaluation_to_dict(e) for e in evaluations]),
                    skill_id,
                ),
            )
        return self.get(skill_id)

    def advance_stage(self, skill_id: str, target: SkillStage) -> SkillRecord:
        """Apply the lifecycle gate and move the stage on success."""
        record = self.get(skill_id)
        decision = self._gate.check(record, target)
        if not decision.allowed:
            msg = f"stage transition refused for {skill_id}: {decision.reason}"
            raise SkillError(msg)
        trusted_at_raw = (
            record.trusted_at.isoformat()
            if record.trusted_at is not None and target is not SkillStage.TRUSTED
            else datetime.now(UTC).isoformat()
            if target is SkillStage.TRUSTED
            else None
        )
        with self._conn:
            self._conn.execute(
                "UPDATE skills SET stage = ?, trusted_at = ? WHERE skill_id = ?",
                (target.value, trusted_at_raw, skill_id),
            )
        return self.get(skill_id)

    def supersede(self, skill_id: str) -> SkillRecord:
        """Mark a skill SUPERSEDED (terminal)."""
        record = self.get(skill_id)
        decision = self._gate.check(record, SkillStage.SUPERSEDED)
        if not decision.allowed:
            msg = f"cannot supersede {skill_id}: {decision.reason}"
            raise SkillError(msg)
        with self._conn:
            self._conn.execute(
                "UPDATE skills SET stage = ? WHERE skill_id = ?",
                (SkillStage.SUPERSEDED.value, skill_id),
            )
        return self.get(skill_id)

    def get(self, skill_id: str) -> SkillRecord:
        """Fetch one record; SkillError when unknown."""
        row = self._conn.execute(
            "SELECT skill_id, name, definition, stage, version, created_at, "
            "provenance, evaluations, trusted_at FROM skills WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        if row is None:
            msg = f"unknown skill: {skill_id!r}"
            raise SkillError(msg)
        return _row_to_record(row)

    def search(
        self,
        *,
        stage: SkillStage | None = None,
        name_substring: str = "",
        limit: int = 50,
    ) -> list[SkillRecord]:
        """Metadata search; sorted by name for determinism."""
        if limit < 1:
            msg = "limit must be >= 1"
            raise ValueError(msg)
        if stage is None:
            rows = self._conn.execute(
                "SELECT skill_id, name, definition, stage, version, created_at, "
                "provenance, evaluations, trusted_at FROM skills WHERE name LIKE ? "
                "ORDER BY name LIMIT ?",
                (f"%{name_substring}%", limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT skill_id, name, definition, stage, version, created_at, "
                "provenance, evaluations, trusted_at FROM skills WHERE name LIKE ? "
                "AND stage = ? ORDER BY name LIMIT ?",
                (f"%{name_substring}%", stage.value, limit),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()


def _evaluation_to_dict(evaluation: SkillEvaluation) -> dict[str, object]:
    return {
        "ran_at": evaluation.ran_at.isoformat(),
        "successes": evaluation.successes,
        "failures": evaluation.failures,
        "corrections": evaluation.corrections,
        "retries": evaluation.retries,
    }


def _row_to_record(row: tuple[str, str, str, str, int, str, str, str, str | None]) -> SkillRecord:
    definition = json.loads(row[2])
    schema = SkillSchema(
        name=definition["name"],
        description=definition["description"],
        trigger=definition["trigger"],
        procedure=tuple(definition["procedure"]),
        constraints=tuple(definition.get("constraints", ())),
        examples=tuple(definition.get("examples", ())),
        failure_modes=tuple(definition.get("failure_modes", ())),
        success_criteria=tuple(definition.get("success_criteria", ())),
    )
    evaluations = tuple(
        SkillEvaluation(
            ran_at=datetime.fromisoformat(e["ran_at"]),
            successes=e["successes"],
            failures=e["failures"],
            corrections=e["corrections"],
            retries=e["retries"],
        )
        for e in json.loads(row[7])
    )
    return SkillRecord(
        skill_id=row[0],
        schema=schema,
        stage=SkillStage(row[3]),
        version=row[4],
        created_at=datetime.fromisoformat(row[5]),
        provenance=row[6],
        evaluations=evaluations,
        trusted_at=datetime.fromisoformat(row[8]) if row[8] else None,
    )
