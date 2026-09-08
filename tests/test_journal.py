"""Event journal tests: append, idempotency, per-correlation reads, schema."""

from collections.abc import Generator
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.journal import EventJournal, JournalError
from xenopus.runtime.events import EVENT_SCHEMA_VERSION, Event, EventType


@pytest.fixture
def journal(tmp_path: Path) -> Generator[EventJournal]:
    j = EventJournal(tmp_path / "journal.sqlite")
    yield j
    j.close()


class TestUnitJournal:
    def test_append_and_read_back(self, journal: EventJournal) -> None:
        corr = new_correlation_id()
        journal.append(
            Event(type=EventType.TASK_CREATED, correlation_id=corr, payload={"goal": "g1"})
        )
        journal.append(Event(type=EventType.TASK_STARTED, correlation_id=corr, payload={}))
        events = journal.events_for(corr)
        assert [e.type for e in events] == [EventType.TASK_CREATED, EventType.TASK_STARTED]

    def test_correlation_isolation(self, journal: EventJournal) -> None:
        corr_a, corr_b = new_correlation_id(), new_correlation_id()
        journal.append(Event(type=EventType.TASK_CREATED, correlation_id=corr_a, payload={}))
        journal.append(Event(type=EventType.TASK_CREATED, correlation_id=corr_b, payload={}))
        assert len(journal.events_for(corr_a)) == 1
        assert len(journal.events_for(corr_b)) == 1

    def test_payload_round_trip(self, journal: EventJournal) -> None:
        corr = new_correlation_id()
        journal.append(
            Event(
                type=EventType.CHECKPOINT_CREATED,
                correlation_id=corr,
                payload={"nested": {"list": [1, 2, 3]}},
            )
        )
        (event,) = journal.events_for(corr)
        assert event.payload == {"nested": {"list": [1, 2, 3]}}

    def test_non_serializable_payload_rejected(self, journal: EventJournal) -> None:
        with pytest.raises(JournalError, match="not JSON-serializable"):
            journal.append(
                Event(
                    type=EventType.TASK_CREATED,
                    correlation_id=new_correlation_id(),
                    payload={"bad": object()},
                )
            )

    def test_empty_correlation_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            Event(type=EventType.TASK_CREATED, correlation_id="", payload={})

    def test_schema_version_stamped_on_write(self, journal: EventJournal) -> None:
        journal.append(
            Event(type=EventType.TASK_CREATED, correlation_id=new_correlation_id(), payload={})
        )
        _, event = journal.all_events()[0]
        assert event.schema_version == EVENT_SCHEMA_VERSION

    def test_wal_mode_enabled(self, journal: EventJournal) -> None:
        mode = journal._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


class TestPropertyJournal:
    @given(
        st.lists(
            st.sampled_from(EventType),
            min_size=0,
            max_size=20,
        )
    )
    @settings(max_examples=25, deadline=None)
    def test_append_order_preserved_for_any_sequence(self, types: list[EventType]) -> None:
        """Any event sequence round-trips in append order."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            j = EventJournal(Path(td) / "j.sqlite")
            try:
                corr = new_correlation_id()
                for t in types:
                    j.append(Event(type=t, correlation_id=corr, payload={"i": 1}))
                events = j.events_for(corr)
                assert [e.type for e in events] == types
            finally:
                j.close()
