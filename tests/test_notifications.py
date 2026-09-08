"""Notification router tests: priorities, quiet hours, rate limit, digest."""

from collections.abc import Generator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from xenopus.persistence.journal import EventJournal
from xenopus.runtime.events import EventType
from xenopus.runtime.notifications import (
    Notification,
    NotificationRouter,
    Priority,
    QuietHours,
    StdoutSink,
    SubscriberPolicy,
)


class CollectingSink(StdoutSink):
    """Captures deliveries instead of printing."""

    def __init__(self) -> None:
        self.delivered: list[str] = []

    def deliver(self, notification: Notification) -> None:
        self.delivered.append(notification.message)


@pytest.fixture
def journal(tmp_path: Path) -> Generator[EventJournal]:
    j = EventJournal(tmp_path / "j.sqlite")
    yield j
    j.close()


def make_router(
    journal: EventJournal | None = None,
    *,
    clock: object = None,
    priorities: dict[EventType, Priority] | None = None,
) -> NotificationRouter:
    return NotificationRouter(
        journal=journal,
        clock=clock,  # type: ignore[arg-type]
        priorities=priorities,
    )


class TestPriorities:
    def test_default_unknown_event_is_silent(self, journal: EventJournal) -> None:
        sink = CollectingSink()
        router = make_router(journal)
        router.subscribe(SubscriberPolicy(subscriber="user"), sink)
        delivered = router.route(
            event_type=EventType.MEMORY_CREATED,
            message="memory saved",
            correlation_id="c1",
        )
        assert delivered == []
        assert sink.delivered == []

    def test_critical_task_failure_delivers(self, journal: EventJournal) -> None:
        sink = CollectingSink()
        router = make_router(journal)
        router.subscribe(SubscriberPolicy(subscriber="user"), sink)
        delivered = router.route(
            event_type=EventType.TASK_FAILED,
            message="task exploded",
            correlation_id="c2",
        )
        assert len(delivered) == 1
        assert delivered[0].priority is Priority.CRITICAL
        assert sink.delivered == ["task exploded"]

    def test_priority_override_map(self, journal: EventJournal) -> None:
        sink = CollectingSink()
        router = make_router(journal, priorities={EventType.MEMORY_CREATED: Priority.NORMAL})
        router.subscribe(SubscriberPolicy(subscriber="user"), sink)
        delivered = router.route(
            event_type=EventType.MEMORY_CREATED,
            message="promoted",
            correlation_id="c3",
        )
        assert len(delivered) == 1


class TestQuietHours:
    def _night_clock(self) -> object:
        fixed = datetime(2026, 9, 8, 23, 30, tzinfo=UTC)

        def clock() -> datetime:
            return fixed

        return clock

    def test_normal_suppressed_in_quiet_hours(self, journal: EventJournal) -> None:
        sink = CollectingSink()
        router = make_router(journal, clock=self._night_clock())
        router.subscribe(
            SubscriberPolicy(
                subscriber="sleeper",
                quiet_hours=QuietHours(start=time(22, 0), end=time(7, 0)),
            ),
            sink,
        )
        delivered = router.route(
            event_type=EventType.TASK_COMPLETED,  # NORMAL
            message="done",
            correlation_id="c",
        )
        assert delivered == []
        assert sink.delivered == []

    def test_critical_bypasses_quiet_hours(self, journal: EventJournal) -> None:
        sink = CollectingSink()
        router = make_router(journal, clock=self._night_clock())
        router.subscribe(
            SubscriberPolicy(
                subscriber="sleeper",
                quiet_hours=QuietHours(start=time(22, 0), end=time(7, 0)),
            ),
            sink,
        )
        delivered = router.route(
            event_type=EventType.TASK_FAILED,  # CRITICAL
            message="production issue",
            correlation_id="c",
        )
        assert len(delivered) == 1

    def test_quiet_hours_midnight_window(self) -> None:
        quiet = QuietHours(start=time(22, 0), end=time(7, 0))
        assert quiet.is_quiet(datetime(2026, 9, 8, 23, 0, tzinfo=UTC))
        assert quiet.is_quiet(datetime(2026, 9, 8, 3, 0, tzinfo=UTC))
        assert not quiet.is_quiet(datetime(2026, 9, 8, 12, 0, tzinfo=UTC))

    def test_quiet_hours_same_day_window(self) -> None:
        quiet = QuietHours(start=time(13, 0), end=time(14, 0))
        assert quiet.is_quiet(datetime(2026, 9, 8, 13, 30, tzinfo=UTC))
        assert not quiet.is_quiet(datetime(2026, 9, 8, 14, 30, tzinfo=UTC))


class TestRateLimit:
    def test_over_limit_traffic_suppressed(self, journal: EventJournal) -> None:
        sink = CollectingSink()
        start = {"t": datetime(2026, 9, 8, 12, 0, tzinfo=UTC)}

        def clock() -> datetime:
            return start["t"]

        router = make_router(journal, clock=clock)
        router.subscribe(SubscriberPolicy(subscriber="flooded", rate_limit_per_minute=3), sink)
        delivered_count = 0
        for i in range(6):
            result = router.route(
                event_type=EventType.TASK_FAILED,  # CRITICAL always considered
                message=f"incident {i}",
                correlation_id=f"c{i}",
            )
            delivered_count += len(result)
        assert delivered_count == 3  # rest suppressed by rate limit

    def test_rate_window_resets(self, journal: EventJournal) -> None:
        sink = CollectingSink()
        start = {"t": datetime(2026, 9, 8, 12, 0, tzinfo=UTC)}

        def clock() -> datetime:
            return start["t"]

        router = make_router(journal, clock=clock)
        router.subscribe(SubscriberPolicy(subscriber="u", rate_limit_per_minute=2), sink)
        for i in range(2):
            router.route(
                event_type=EventType.TASK_FAILED,
                message=f"a{i}",
                correlation_id=f"c{i}",
            )
        assert sink.delivered == ["a0", "a1"]
        start["t"] = start["t"] + timedelta(seconds=61)
        delivered = router.route(
            event_type=EventType.TASK_FAILED, message="after", correlation_id="c9"
        )
        assert len(delivered) == 1


class TestDigest:
    def test_digest_holds_and_flushes(self, journal: EventJournal) -> None:
        sink = CollectingSink()
        router = make_router(journal)
        router.subscribe(SubscriberPolicy(subscriber="digest-user", digest=True), sink)
        for i in range(3):
            router.route(
                event_type=EventType.TASK_COMPLETED,
                message=f"done {i}",
                correlation_id=f"c{i}",
            )
        assert sink.delivered == []  # held for digest
        held = router.flush_digest("digest-user")
        assert len(held) == 3
        assert len(sink.delivered) == 1  # the digest summary line
        assert sink.delivered[0].startswith("digest: 3 held")

    def test_flush_empty_digest_returns_nothing(self, journal: EventJournal) -> None:
        router = make_router(journal)
        router.subscribe(SubscriberPolicy(subscriber="u"), CollectingSink())
        assert router.flush_digest("u") == []

    def test_flush_unknown_subscriber_raises(self, journal: EventJournal) -> None:
        router = make_router(journal)
        with pytest.raises(Exception, match="unknown subscriber"):
            router.flush_digest("ghost")


class TestJournalEvidence:
    def test_delivered_and_suppressed_both_journaled(self, journal: EventJournal) -> None:
        router = make_router(journal)
        sink = CollectingSink()
        router.subscribe(
            SubscriberPolicy(
                subscriber="evidence", quiet_hours=QuietHours(start=time(0, 0), end=time(23, 59))
            ),
            sink,
        )
        router.route(event_type=EventType.TASK_COMPLETED, message="suppressed", correlation_id="c1")
        router.route(event_type=EventType.TASK_FAILED, message="delivered", correlation_id="c2")
        types = [e.type.value for _, e in journal.all_events()]
        assert "NOTIFICATION_SUPPRESSED" in types
        assert "NOTIFICATION_DELIVERED" in types

    def test_duplicate_subscription_refused(self, journal: EventJournal) -> None:
        router = make_router(journal)
        router.subscribe(SubscriberPolicy(subscriber="u"), CollectingSink())
        with pytest.raises(Exception, match="already registered"):
            router.subscribe(SubscriberPolicy(subscriber="u"), CollectingSink())
