"""Notification router: policy-gated delivery to LOCAL sinks (Phase 9).

Routes runtime events to subscribers under per-subscriber policy
(addendum 41-42, 96-99): priorities (SILENT/NORMAL/CRITICAL), quiet
hours (non-critical held, critical pass), digest aggregation, and a
per-subscriber rate limit (anti-spam). Sinks are LOCAL ONLY in this
phase (stdout + journal); channel adapters (Telegram etc.) arrive in
Phase 12+ against this same router.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from uuid import uuid4

from xenopus.persistence.journal import EventJournal
from xenopus.runtime.events import Event, EventType
from xenopus.runtime.observer import Observation


class NotificationError(Exception):
    """Raised for router misuse: unknown subscribers, bad policy."""


class Priority(StrEnum):
    """Notification priorities (addendum 42)."""

    SILENT = "SILENT"  # journal-only, never surfaced
    NORMAL = "NORMAL"  # deliver immediately (unless quiet hours)
    CRITICAL = "CRITICAL"  # always deliver, even in quiet hours


class QuietHours:
    """A quiet window in wall-clock time (addendum 97).

    Supports windows crossing midnight (e.g. 22:00 -> 07:00).
    Critical notifications are EXEMPT (policy, not accident).
    """

    def __init__(self, *, start: time, end: time) -> None:
        self._start = start
        self._end = end

    def is_quiet(self, at: datetime) -> bool:
        """True when ``at`` falls inside the quiet window."""
        moment = at.time()
        if self._start <= self._end:
            return self._start <= moment < self._end
        # Crosses midnight: e.g. 22:00 -> 07:00.
        return moment >= self._start or moment < self._end


@dataclass(frozen=True, slots=True)
class SubscriberPolicy:
    """Delivery policy for one subscriber (addendum 96)."""

    subscriber: str
    rate_limit_per_minute: int = 10
    quiet_hours: QuietHours | None = None
    digest: bool = False  # hold NORMAL traffic for digest flush


DEFAULT_EVENT_PRIORITIES: dict[EventType, Priority] = {
    EventType.TASK_COMPLETED: Priority.NORMAL,
    EventType.TASK_FAILED: Priority.CRITICAL,
    EventType.TASK_CANCELLED: Priority.NORMAL,
    EventType.APPROVAL_REQUIRED: Priority.CRITICAL,
    EventType.APPROVAL_GRANTED: Priority.NORMAL,
    EventType.APPROVAL_DENIED: Priority.NORMAL,
    EventType.KILLSWITCH_TRIGGERED: Priority.CRITICAL,
    EventType.VERIFICATION_FAILED: Priority.NORMAL,
    EventType.SCHEDULE_FIRED: Priority.NORMAL,
    EventType.TASK_INTERRUPTED: Priority.NORMAL,
}


@dataclass(frozen=True, slots=True)
class Notification:
    """One routable notification."""

    notification_id: str
    subscriber: str
    priority: Priority
    message: str
    event_type: str
    correlation_id: str
    created_at: str


class Sink:
    """Delivery sink interface (local only in Phase 9)."""

    def deliver(self, notification: Notification) -> None:
        """Deliver one notification."""
        raise NotImplementedError


class StdoutSink(Sink):
    """Prints notifications to stdout (CLI/TUI-local surface)."""

    def deliver(self, notification: Notification) -> None:
        """Print the notification line."""
        print(
            f"[{notification.priority.value}] {notification.event_type}: "
            f"{notification.message} ({notification.correlation_id})"
        )


@dataclass(slots=True)
class _WindowCounter:
    """Per-minute rate counter (deterministic with injected clock)."""

    _hits: dict[str, list[datetime]]

    def allow(self, subscriber: str, at: datetime, limit: int) -> bool:
        """True when the subscriber is under the per-minute limit."""
        recent = [t for t in self._hits.get(subscriber, []) if (at - t).total_seconds() < 60]
        if len(recent) >= limit:
            return False
        recent.append(at)
        self._hits[subscriber] = recent
        return True


class NotificationRouter:
    """Routes events to subscribers under policy (addendum 41-42, 96-99).

    Contract:
        subscribe(): register a subscriber with a policy and sink.
        route(): applies priority mapping (event -> Priority, default
        SILENT), quiet hours (CRITICAL exempt), digest holding, and the
        per-minute rate limit; delivers or suppresses with journal
        evidence for BOTH outcomes.

    Invariants:
        SILENT events never deliver; rate-limited traffic is suppressed
        (never queued past the limit — anti-spam wins, addendum 42).
    """

    def __init__(
        self,
        *,
        journal: EventJournal | None = None,
        clock: Callable[[], datetime] | None = None,
        priorities: dict[EventType, Priority] | None = None,
    ) -> None:
        self._journal = journal
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._priorities = dict(DEFAULT_EVENT_PRIORITIES)
        if priorities:
            self._priorities.update(priorities)
        self._subscribers: dict[str, tuple[SubscriberPolicy, Sink]] = {}
        self._digest: dict[str, list[Notification]] = {}
        self._counter = _WindowCounter({})

    def subscribe(self, policy: SubscriberPolicy, sink: Sink) -> None:
        """Register a subscriber."""
        if policy.subscriber in self._subscribers:
            msg = f"subscriber already registered: {policy.subscriber!r}"
            raise NotificationError(msg)
        self._subscribers[policy.subscriber] = (policy, sink)

    def event_priority(self, event_type: EventType) -> Priority:
        """Priority for an event type (default SILENT — never noisy)."""
        return self._priorities.get(event_type, Priority.SILENT)

    def route(
        self,
        *,
        event_type: EventType,
        message: str,
        correlation_id: str,
        observation: Observation | None = None,
    ) -> list[Notification]:
        """Route one event to all subscribers; returns deliveries."""
        priority = self.event_priority(event_type)
        delivered: list[Notification] = []
        for subscriber, (policy, sink) in self._subscribers.items():
            notification = Notification(
                notification_id=f"ntf-{uuid4().hex[:12]}",
                subscriber=subscriber,
                priority=priority,
                message=message,
                event_type=event_type.value,
                correlation_id=correlation_id,
                created_at=self._clock().isoformat(),
            )
            if priority is Priority.SILENT:
                self._record(notification, delivered=False, reason="silent")
                continue
            now = self._clock()
            if (
                priority is Priority.NORMAL
                and policy.quiet_hours is not None
                and policy.quiet_hours.is_quiet(now)
            ):
                if policy.digest:
                    self._digest.setdefault(subscriber, []).append(notification)
                    self._record(
                        notification, delivered=False, reason="quiet-hours:held-for-digest"
                    )
                else:
                    self._record(notification, delivered=False, reason="quiet-hours")
                continue
            if not self._counter.allow(subscriber, now, policy.rate_limit_per_minute):
                self._record(notification, delivered=False, reason="rate-limited")
                continue
            if policy.digest and priority is Priority.NORMAL:
                self._digest.setdefault(subscriber, []).append(notification)
                self._record(notification, delivered=False, reason="digest")
                continue
            sink.deliver(notification)
            delivered.append(notification)
            self._record(notification, delivered=True, reason="delivered")
        return delivered

    def flush_digest(self, subscriber: str) -> list[Notification]:
        """Deliver the held digest for one subscriber (addendum 98)."""
        policy_tuple = self._subscribers.get(subscriber)
        if policy_tuple is None:
            msg = f"unknown subscriber: {subscriber!r}"
            raise NotificationError(msg)
        _, sink = policy_tuple
        held = self._digest.get(subscriber, [])
        if not held:
            return []
        summary = Notification(
            notification_id=f"ntf-{uuid4().hex[:12]}",
            subscriber=subscriber,
            priority=Priority.NORMAL,
            message=(
                f"digest: {len(held)} held notification(s): "
                + "; ".join(n.message for n in held[:5])
                + ("..." if len(held) > 5 else "")
            ),
            event_type="DIGEST",
            correlation_id="digest",
            created_at=self._clock().isoformat(),
        )
        sink.deliver(summary)
        self._digest[subscriber] = []
        self._record(summary, delivered=True, reason="digest-flushed")
        return held

    def _record(self, notification: Notification, *, delivered: bool, reason: str) -> None:
        """Journal the routing outcome (evidence for both paths)."""
        if self._journal is None:
            return
        self._journal.append(
            Event(
                type=(
                    EventType.NOTIFICATION_DELIVERED
                    if delivered
                    else EventType.NOTIFICATION_SUPPRESSED
                ),
                correlation_id=notification.correlation_id,
                payload={
                    "subscriber": notification.subscriber,
                    "priority": notification.priority.value,
                    "event_type": notification.event_type,
                    "reason": reason,
                },
            )
        )
