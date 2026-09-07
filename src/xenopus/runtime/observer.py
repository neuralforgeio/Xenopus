"""Observer: raw capture of execution evidence, never interpretation.

Observation ≠ interpretation (master prompt 18): the observer records
tool results, outputs, timings, and errors verbatim into an observation
record that the verifier evaluates separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Observation:
    """One captured fact about an execution step.

    Contract:
        kind: 'tool_result' | 'command_output' | 'file_change' | 'error'.
        content: the raw captured value (JSON-serializable).
        correlation_id: trace linkage across the pipeline.
    """

    observation_id: str
    kind: str
    content: dict[str, Any]
    correlation_id: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        kind: str,
        content: dict[str, Any],
        correlation_id: str,
        created_at: str,
    ) -> Observation:
        """Build an observation with a fresh id."""
        if not kind.strip():
            msg = "observation kind must be non-empty"
            raise ValueError(msg)
        return cls(
            observation_id=f"obs-{uuid4().hex[:14]}",
            kind=kind,
            content=content,
            correlation_id=correlation_id,
            created_at=created_at,
        )


@dataclass(slots=True)
class ObservationLog:
    """Collects observations for one task run (in-memory; journal in 6)."""

    _entries: list[Observation] = field(default_factory=list)

    def record(
        self,
        *,
        kind: str,
        content: dict[str, Any],
        correlation_id: str,
        created_at: str,
    ) -> Observation:
        """Append one observation; returns it for reference."""
        observation = Observation.create(
            kind=kind,
            content=content,
            correlation_id=correlation_id,
            created_at=created_at,
        )
        self._entries.append(observation)
        return observation

    def entries(self) -> list[Observation]:
        """All observations in record order."""
        return list(self._entries)

    def for_correlation(self, correlation_id: str) -> list[Observation]:
        """Observations filtered to one trace id."""
        return [o for o in self._entries if o.correlation_id == correlation_id]
