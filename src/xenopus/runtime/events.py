"""Runtime event vocabulary (v1) and the canonical Event envelope.

The append-only journal (``xenopus.persistence.journal``) is the canonical
event source; this module defines what may be written. The vocabulary is
versioned: adding new event types is a minor schema change; renaming or
removing existing ones is a major change (ADR-004).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """Canonical runtime event vocabulary (v1).

    Only events that a consumer demonstrably reacts to belong here
    (master prompt 85 / addendum 43: "use only useful events").
    """

    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_WAITING = "TASK_WAITING"
    TASK_PAUSED = "TASK_PAUSED"
    TASK_RESUMED = "TASK_RESUMED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    AGENT_FAILED = "AGENT_FAILED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_PASSED = "VERIFICATION_PASSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


EVENT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable journal event envelope.

    Contract:
        type: canonical vocabulary entry (never a free-form string).
        correlation_id: end-to-end trace identifier propagated across
            every boundary (Protocol v9 Section 12.4).
        payload: JSON-serializable data; must never contain secrets or PII.
        schema_version: journal schema version at write time.

    Invariants:
        ``schema_version`` always equals the current EVENT_SCHEMA_VERSION
        at construction time; older rows are upgraded on read.
    """

    type: EventType
    correlation_id: str
    payload: dict[str, Any]
    schema_version: int = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.correlation_id:
            msg = "correlation_id must be a non-empty string"
            raise ValueError(msg)
