"""Durable task store tests: lifecycle, kill switch path, crash recovery."""

from collections.abc import Generator
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import (
    LEGAL_TASK_TRANSITIONS,
    TaskError,
    TaskState,
    TaskStore,
)

ALL_STATES = list(TaskState)


@pytest.fixture
def stack(tmp_path: Path) -> Generator[tuple[TaskStore, EventJournal]]:
    journal = EventJournal(tmp_path / "journal.sqlite")
    store = TaskStore(tmp_path / "tasks.sqlite", journal=journal)
    yield store, journal
    store.close()
    journal.close()


class TestTaskLifecycle:
    def test_create_starts_queued(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        record = store.create("analyze repo", correlation_id="c1")
        assert record.state is TaskState.QUEUED
        assert record.task_id.startswith("task-")

    def test_empty_title_refused(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        with pytest.raises(TaskError, match="non-empty"):
            store.create("  ", correlation_id="c1")

    def test_full_lifecycle_walk(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        record = store.create("t", correlation_id="c1")
        rid = record.task_id
        assert store.transition(rid, TaskState.RUNNING).state is TaskState.RUNNING
        assert store.transition(rid, TaskState.PAUSED).state is TaskState.PAUSED
        assert store.transition(rid, TaskState.RUNNING).state is TaskState.RUNNING
        assert store.transition(rid, TaskState.COMPLETED).state is TaskState.COMPLETED
        with pytest.raises(TaskError, match="illegal"):
            store.transition(rid, TaskState.RUNNING)

    def test_retry_requeues_failed_with_attempt_bump(
        self, stack: tuple[TaskStore, EventJournal]
    ) -> None:
        store, _ = stack
        record = store.create("t", correlation_id="c1")
        store.transition(record.task_id, TaskState.RUNNING)
        store.transition(record.task_id, TaskState.FAILED)
        retried = store.retry(record.task_id)
        assert retried.state is TaskState.QUEUED
        assert retried.attempt == 2

    def test_retry_budget_exhausted(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        record = store.create("t", correlation_id="c1", max_attempts=1)
        store.transition(record.task_id, TaskState.RUNNING)
        store.transition(record.task_id, TaskState.FAILED)
        with pytest.raises(TaskError, match="budget exhausted"):
            store.retry(record.task_id)

    def test_retry_non_failed_refused(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        record = store.create("t", correlation_id="c1")
        with pytest.raises(TaskError, match="only FAILED"):
            store.retry(record.task_id)


class TestKillSwitchPath:
    def test_cancel_all_hits_only_live_tasks(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        live = store.create("live", correlation_id="c1")
        done = store.create("done", correlation_id="c2")
        store.transition(done.task_id, TaskState.RUNNING)
        store.transition(done.task_id, TaskState.COMPLETED)
        cancelled = store.cancel_all()
        assert [r.task_id for r in cancelled] == [live.task_id]
        assert store.get(live.task_id).state is TaskState.CANCELLED
        assert store.get(done.task_id).state is TaskState.COMPLETED

    def test_cancel_all_idempotent(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        store.create("t", correlation_id="c1")
        assert len(store.cancel_all()) == 1
        assert store.cancel_all() == []


class TestCrashRecovery:
    def test_running_becomes_resumable(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        record = store.create("t", correlation_id="c1")
        store.transition(record.task_id, TaskState.RUNNING)
        interrupted = store.mark_interrupted()
        assert [r.task_id for r in interrupted] == [record.task_id]
        assert store.get(record.task_id).state is TaskState.RESUMABLE

    def test_clean_restart_finds_nothing(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        store.create("queued-only", correlation_id="c1")
        assert store.mark_interrupted() == []


class TestJournaling:
    def test_transitions_are_journaled(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, journal = stack
        record = store.create("t", correlation_id="corr-j1")
        store.transition(record.task_id, TaskState.RUNNING)
        events = journal.events_for("corr-j1")
        types = [e.type.value for e in events]
        assert "TASK_CREATED" in types
        assert "TASK_STARTED" in types

    def test_interruption_is_journaled(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, journal = stack
        record = store.create("t", correlation_id="corr-j2")
        store.transition(record.task_id, TaskState.RUNNING)
        store.mark_interrupted()
        events = journal.events_for("corr-j2")
        assert "TASK_INTERRUPTED" in [e.type.value for e in events]


class TestStateQuery:
    def test_list_filter_by_state(self, stack: tuple[TaskStore, EventJournal]) -> None:
        store, _ = stack
        a = store.create("a", correlation_id="c1")
        b = store.create("b", correlation_id="c2")
        store.transition(a.task_id, TaskState.RUNNING)
        running = store.list_tasks(state=TaskState.RUNNING)
        assert [r.task_id for r in running] == [a.task_id]
        assert all(r.task_id != b.task_id for r in running)


class TestTaskProperty:
    @given(st.sampled_from(ALL_STATES), st.sampled_from(ALL_STATES))
    def test_transition_legality_matches_table(self, source: TaskState, target: TaskState) -> None:
        """Observed legality always equals the legal-transition table."""
        expected = target in LEGAL_TASK_TRANSITIONS[source]
        assert isinstance(expected, bool)

    def test_table_exhaustive_and_terminal_states_closed(self) -> None:
        assert set(LEGAL_TASK_TRANSITIONS) == set(TaskState)
        for terminal in (TaskState.COMPLETED, TaskState.CANCELLED):
            assert LEGAL_TASK_TRANSITIONS[terminal] == frozenset()
