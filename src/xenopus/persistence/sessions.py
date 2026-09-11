"""Session store: persistent, searchable, forkable conversation sessions.

Sessions live in SQLite under the Xenopus home directory (master prompt
53-54): create, append turns, fork with lineage, search titles via FTS5
with a LIKE fallback (ADR-028), archive. Usage (tokens/cost) accumulates
per turn for the cost manager (master prompt 66).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

SESSIONS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    parent_session_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
)
"""

SESSION_TURNS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS session_turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd_micros INTEGER NOT NULL DEFAULT 0
)
"""

SESSION_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_turns_session
    ON session_turns (session_id, created_at)
"""

SESSION_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    title, content='sessions', content_rowid='rowid'
)
"""

SESSION_FTS_TRIGGERS_DDL = (
    """
    CREATE TRIGGER IF NOT EXISTS sessions_fts_insert AFTER INSERT ON sessions BEGIN
        INSERT INTO sessions_fts(rowid, title) VALUES (new.rowid, new.title);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS sessions_fts_delete AFTER DELETE ON sessions BEGIN
        INSERT INTO sessions_fts(sessions_fts, rowid, title)
        VALUES ('delete', old.rowid, old.title);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS sessions_fts_update AFTER UPDATE OF title ON sessions BEGIN
        INSERT INTO sessions_fts(sessions_fts, rowid, title)
        VALUES ('delete', old.rowid, old.title);
        INSERT INTO sessions_fts(rowid, title) VALUES (new.rowid, new.title);
    END
    """,
)


class SessionError(Exception):
    """Raised for session-store misuse: unknown ids, illegal operations."""


@dataclass(frozen=True, slots=True)
class SessionTurn:
    """One stored conversation turn."""

    turn_id: str
    session_id: str
    role: str
    content: str
    created_at: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd_micros: int


@dataclass(frozen=True, slots=True)
class Session:
    """Session metadata row (turns fetched separately)."""

    session_id: str
    title: str
    parent_session_id: str | None
    created_at: str
    updated_at: str
    archived: bool


def _fts5_available(conn: sqlite3.Connection) -> bool:
    """Probe FTS5 support (exotic SQLite builds may lack it)."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _quote_tokens(query: str) -> str:
    """Render a search string as a safe FTS5 prefix query.

    Every token is double-quoted so FTS5 syntax (OR/AND/NOT/NEAR,
    column filters) becomes literal text; `*` grants prefix
    matching. Untrusted input can therefore never alter query
    semantics (ADR-028 injection defense).
    """
    tokens = query.replace('"', " ").split()
    if not tokens:
        return ""
    return " ".join(f'"{token}"*' for token in tokens)


class SessionStore:
    """SQLite-backed session persistence.

    Contract:
        create(): new session; fork(): new session with lineage to the
            parent (master prompt 54: forking preserves lineage).
        append_turn(): appends one message with usage accounting.
        turns()/lineage()/search()/archive() read or update rows.
        search(): FTS5 prefix matching over non-archived titles when
            the build provides FTS5, LIKE substring otherwise; the
            API and ordering are identical either way (ADR-028).

    Failure modes:
        SessionError for unknown session ids and archived-session writes
        (archived sessions are immutable history).
    """

    def __init__(self, path: Path, *, force_like: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(SESSIONS_TABLE_DDL)
        self._conn.execute(SESSION_TURNS_TABLE_DDL)
        self._conn.execute(SESSION_INDEX_DDL)
        self._fts = not force_like and _fts5_available(self._conn)
        if self._fts:
            existed = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'sessions_fts'"
            ).fetchone()
            self._conn.execute(SESSION_FTS_DDL)
            for trigger_ddl in SESSION_FTS_TRIGGERS_DDL:
                self._conn.execute(trigger_ddl)
            if existed is None:
                # Fresh index over existing rows (idempotent migration).
                self._conn.execute("INSERT INTO sessions_fts(sessions_fts) VALUES('rebuild')")
        self._conn.commit()

    def create(self, title: str) -> Session:
        """Create a root session (no parent)."""
        if not title.strip():
            msg = "session title must be non-empty"
            raise SessionError(msg)
        now = datetime.now(UTC).isoformat()
        session = Session(
            session_id=f"sess-{uuid4().hex[:12]}",
            title=title.strip(),
            parent_session_id=None,
            created_at=now,
            updated_at=now,
            archived=False,
        )
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions "
                "(session_id, title, parent_session_id, created_at, updated_at, archived) "
                "VALUES (?, ?, NULL, ?, ?, 0)",
                (session.session_id, session.title, now, now),
            )
        return session

    def fork(self, parent_session_id: str, title: str) -> Session:
        """Fork a session: new session, lineage preserved (Phase 54)."""
        parent = self.get(parent_session_id)
        now = datetime.now(UTC).isoformat()
        forked = Session(
            session_id=f"sess-{uuid4().hex[:12]}",
            title=title.strip() if title.strip() else f"fork: {parent.title}",
            parent_session_id=parent.session_id,
            created_at=now,
            updated_at=now,
            archived=False,
        )
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions "
                "(session_id, title, parent_session_id, created_at, updated_at, archived) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (
                    forked.session_id,
                    forked.title,
                    forked.parent_session_id,
                    now,
                    now,
                ),
            )
            # Fork copies the parent's turns up to the fork point: lineage
            # carries content, not just the id chain.
            self._conn.execute(
                "INSERT INTO session_turns "
                "(turn_id, session_id, role, content, created_at, "
                " prompt_tokens, completion_tokens, cost_usd_micros) "
                "SELECT ?, ?, role, content, created_at, "
                "       prompt_tokens, completion_tokens, cost_usd_micros "
                "FROM session_turns WHERE session_id = ? ORDER BY created_at, rowid",
                (f"turn-{uuid4().hex[:12]}-", forked.session_id, parent.session_id),
            )
        return forked

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd_micros: int = 0,
    ) -> SessionTurn:
        """Append one turn with usage accounting."""
        session = self.get(session_id)
        if session.archived:
            msg = f"session {session_id} is archived and immutable"
            raise SessionError(msg)
        now = datetime.now(UTC).isoformat()
        turn = SessionTurn(
            turn_id=f"turn-{uuid4().hex[:14]}",
            session_id=session_id,
            role=role,
            content=content,
            created_at=now,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd_micros=cost_usd_micros,
        )
        with self._conn:
            self._conn.execute(
                "INSERT INTO session_turns "
                "(turn_id, session_id, role, content, created_at, "
                " prompt_tokens, completion_tokens, cost_usd_micros) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn.turn_id,
                    turn.session_id,
                    turn.role,
                    turn.content,
                    turn.created_at,
                    turn.prompt_tokens,
                    turn.completion_tokens,
                    turn.cost_usd_micros,
                ),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
        return turn

    def turns(self, session_id: str) -> list[SessionTurn]:
        """All turns of one session in chronological (append) order."""
        self.get(session_id)  # existence check
        rows = self._conn.execute(
            "SELECT turn_id, session_id, role, content, created_at, "
            "       prompt_tokens, completion_tokens, cost_usd_micros "
            "FROM session_turns WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        ).fetchall()
        return [
            SessionTurn(
                turn_id=row[0],
                session_id=row[1],
                role=row[2],
                content=row[3],
                created_at=row[4],
                prompt_tokens=row[5],
                completion_tokens=row[6],
                cost_usd_micros=row[7],
            )
            for row in rows
        ]

    def lineage(self, session_id: str) -> list[Session]:
        """Root-to-session ancestry chain (lineage for fork trees)."""
        chain: list[Session] = []
        current: Session | None = self.get(session_id)
        while current is not None:
            chain.append(current)
            if current.parent_session_id is None:
                break
            current = self.get(current.parent_session_id)
        chain.reverse()  # root first
        return chain

    def search(self, query: str) -> list[Session]:
        """Search non-archived session titles, newest first.

        FTS5 prefix matching when available (tokens quoted —
        untrusted strings can never alter query semantics,
        ADR-028); LIKE substring otherwise. A malformed FTS
        expression falls back to LIKE for this call — search
        never surfaces a syntax error.
        """
        if not query.strip():
            msg = "search query must be non-empty"
            raise SessionError(msg)
        if self._fts:
            fts_query = _quote_tokens(query)
            if fts_query:
                try:
                    rows = self._conn.execute(
                        "SELECT s.session_id, s.title, s.parent_session_id, "
                        "       s.created_at, s.updated_at, s.archived "
                        "FROM sessions_fts f JOIN sessions s ON s.rowid = f.rowid "
                        "WHERE s.archived = 0 AND sessions_fts MATCH ? "
                        "ORDER BY s.updated_at DESC",
                        (fts_query,),
                    ).fetchall()
                    return [self._row_to_session(row) for row in rows]
                except sqlite3.OperationalError:
                    pass  # malformed expression despite quoting: fall back
        rows = self._conn.execute(
            "SELECT session_id, title, parent_session_id, created_at, updated_at, archived "
            "FROM sessions WHERE archived = 0 AND title LIKE ? "
            "ORDER BY updated_at DESC",
            (f"%{query.strip()}%",),
        ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def archive(self, session_id: str) -> Session:
        """Archive a session; archived sessions are immutable."""
        session = self.get(session_id)
        if session.archived:
            return session
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET archived = 1 WHERE session_id = ?",
                (session_id,),
            )
        return self.get(session_id)

    def get(self, session_id: str) -> Session:
        """Fetch one session; SessionError when unknown."""
        row = self._conn.execute(
            "SELECT session_id, title, parent_session_id, created_at, updated_at, archived "
            "FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            msg = f"unknown session: {session_id!r}"
            raise SessionError(msg)
        return self._row_to_session(row)

    def usage_totals(self, session_id: str) -> tuple[int, int, int]:
        """(prompt_tokens, completion_tokens, cost_usd_micros) totals."""
        self.get(session_id)
        row = self._conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0), "
            "       COALESCE(SUM(completion_tokens), 0), "
            "       COALESCE(SUM(cost_usd_micros), 0) "
            "FROM session_turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return (row[0], row[1], row[2])

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    @staticmethod
    def _row_to_session(row: tuple[str, str, str | None, str, str, int]) -> Session:
        """Map a sessions-table row to a Session."""
        return Session(
            session_id=row[0],
            title=row[1],
            parent_session_id=row[2],
            created_at=row[3],
            updated_at=row[4],
            archived=bool(row[5]),
        )
