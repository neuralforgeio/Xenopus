"""Session store tests: create, fork lineage, turns, search, archive, usage."""

from collections.abc import Generator
from pathlib import Path

import pytest

from xenopus.persistence.sessions import SessionError, SessionStore


@pytest.fixture
def store(tmp_path: Path) -> Generator[SessionStore]:
    s = SessionStore(tmp_path / "sessions.sqlite")
    yield s
    s.close()


class TestSessionStore:
    def test_create_root_session(self, store: SessionStore) -> None:
        session = store.create("First session")
        assert session.parent_session_id is None
        assert session.archived is False
        assert session.session_id.startswith("sess-")

    def test_empty_title_rejected(self, store: SessionStore) -> None:
        with pytest.raises(SessionError, match="non-empty"):
            store.create("   ")

    def test_append_and_read_turns_in_order(self, store: SessionStore) -> None:
        session = store.create("s")
        store.append_turn(session.session_id, "user", "first")
        store.append_turn(session.session_id, "assistant", "second")
        turns = store.turns(session.session_id)
        assert [t.content for t in turns] == ["first", "second"]
        assert [t.role for t in turns] == ["user", "assistant"]

    def test_fork_preserves_lineage_and_copies_turns(self, store: SessionStore) -> None:
        parent = store.create("original")
        store.append_turn(parent.session_id, "user", "q1")
        child = store.fork(parent.session_id, "my fork")
        assert child.parent_session_id == parent.session_id
        assert [t.content for t in store.turns(child.session_id)] == ["q1"]

    def test_fork_lineage_chain(self, store: SessionStore) -> None:
        root = store.create("root")
        branch = store.fork(root.session_id, "branch")
        leaf = store.fork(branch.session_id, "leaf")
        chain = store.lineage(leaf.session_id)
        assert [s.session_id for s in chain] == [
            root.session_id,
            branch.session_id,
            leaf.session_id,
        ]

    def test_fork_default_title_derives_from_parent(self, store: SessionStore) -> None:
        parent = store.create("parent title")
        child = store.fork(parent.session_id, "  ")
        assert child.title == "fork: parent title"

    def test_search_matches_title(self, store: SessionStore) -> None:
        store.create("deploy the app")
        store.create("unrelated")
        hits = store.search("deploy")
        assert len(hits) == 1
        assert hits[0].title == "deploy the app"

    def test_search_excludes_archived(self, store: SessionStore) -> None:
        session = store.create("secret plan")
        store.archive(session.session_id)
        assert store.search("secret") == []

    def test_archived_session_rejects_writes(self, store: SessionStore) -> None:
        session = store.create("s")
        store.archive(session.session_id)
        with pytest.raises(SessionError, match="immutable"):
            store.append_turn(session.session_id, "user", "nope")

    def test_archive_is_idempotent(self, store: SessionStore) -> None:
        session = store.create("s")
        first = store.archive(session.session_id)
        second = store.archive(session.session_id)
        assert first.archived is True
        assert second.archived is True

    def test_usage_totals_accumulate(self, store: SessionStore) -> None:
        session = store.create("s")
        store.append_turn(session.session_id, "user", "a", prompt_tokens=10, cost_usd_micros=5)
        store.append_turn(
            session.session_id, "assistant", "b", completion_tokens=7, cost_usd_micros=3
        )
        prompt, completion, cost = store.usage_totals(session.session_id)
        assert (prompt, completion, cost) == (10, 7, 8)

    def test_unknown_session_raises(self, store: SessionStore) -> None:
        with pytest.raises(SessionError, match="unknown session"):
            store.get("sess-nonexistent")

    def test_empty_search_query_rejected(self, store: SessionStore) -> None:
        with pytest.raises(SessionError, match="non-empty"):
            store.search("  ")
