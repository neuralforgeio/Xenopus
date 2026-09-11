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


class TestFtsSearch:
    """ADR-028: FTS5 prefix search, injection safety, fallback."""

    def test_prefix_match_finds_partial_token(self, store: SessionStore) -> None:
        store.create("deploy the app")
        store.create("unrelated")
        hits = store.search("deplo")
        assert len(hits) == 1
        assert hits[0].title == "deploy the app"

    def test_multi_token_search_requires_all_tokens(self, store: SessionStore) -> None:
        store.create("deploy the app")
        store.create("deploy other thing")
        hits = store.search("deploy app")
        assert [h.title for h in hits] == ["deploy the app"]

    def test_fts_syntax_treated_as_literal(self, store: SessionStore) -> None:
        """OR/NEAR/column filters must be quoted into literals, never operators."""
        store.create("release notes")
        store.create("plain session")
        hits = store.search("OR")
        assert hits == []  # no title contains the literal token "OR"
        hits_never = store.search("release NEAR notes")
        assert hits_never == []  # NEAR quoted: no single token "release NEAR notes"

    def test_fts_syntax_cannot_leak_matches(self, store: SessionStore) -> None:
        """`title:deploy` must NOT become a column filter returning rows."""
        store.create("deploy the app")
        store.create("random")
        hits = store.search("title:deploy")
        assert hits == []  # quoted literal, not a column-filter expression

    def test_archived_exclusion_holds_under_fts(self, store: SessionStore) -> None:
        session = store.create("secret plan")
        store.archive(session.session_id)
        assert store.search("secret") == []
        assert store.search("secr") == []

    def test_like_fallback_full_contract(self, tmp_path: Path) -> None:
        """force_like mode: every search contract still holds.

        LIKE is substring-anywhere: it also matches mid-word ("ploy"),
        where FTS matches token prefixes only — both are correct
        within their documented semantics.
        """
        store = SessionStore(tmp_path / "like.sqlite", force_like=True)
        try:
            store.create("deploy the app")
            store.create("unrelated")
            assert [h.title for h in store.search("deploy")] == ["deploy the app"]
            assert [h.title for h in store.search("ploy")] == ["deploy the app"]
            assert store.search("xyzzy") == []
            session = store.create("secret plan")
            store.archive(session.session_id)
            assert store.search("secret") == []
            with pytest.raises(SessionError, match="non-empty"):
                store.search("  ")
        finally:
            store.close()

    def test_migration_rebuild_is_idempotent(self, tmp_path: Path) -> None:
        """Pre-FTS database upgrades transparently; re-open does not duplicate."""
        import sqlite3

        from xenopus.persistence.sessions import SESSIONS_TABLE_DDL

        db = tmp_path / "legacy.sqlite"
        conn = sqlite3.connect(db)
        conn.execute(SESSIONS_TABLE_DDL)
        conn.execute(
            "INSERT INTO sessions (session_id, title, parent_session_id, created_at, "
            "updated_at, archived) VALUES ('s1', 'legacy deploy', NULL, 't', 't', 0)"
        )
        conn.commit()
        conn.close()
        store = SessionStore(db)  # migration runs on open
        try:
            assert [h.title for h in store.search("deploy")] == ["legacy deploy"]
            assert [h.title for h in store.search("legac")] == ["legacy deploy"]
        finally:
            store.close()
        reopened = SessionStore(db)  # second open: no rebuild, still correct
        try:
            assert len(reopened.search("deploy")) == 1
        finally:
            reopened.close()

    def test_fts_index_tracks_updates_and_deletes(self, tmp_path: Path) -> None:
        """Triggers keep the index consistent with the content table."""
        store = SessionStore(tmp_path / "trig.sqlite")
        try:
            session = store.create("alpha beta")
            assert len(store.search("alpha")) == 1
            import sqlite3

            # rename via SQL (the trigger path; the store API has no rename)
            conn = sqlite3.connect(tmp_path / "trig.sqlite")
            conn.execute(
                "UPDATE sessions SET title = 'gamma delta' WHERE session_id = ?",
                (session.session_id,),
            )
            conn.commit()
            conn.close()
            store2 = SessionStore(tmp_path / "trig.sqlite")
            try:
                assert store2.search("alpha") == []
                assert [h.title for h in store2.search("gamma")] == ["gamma delta"]
            finally:
                store2.close()
        finally:
            store.close()
