# ADR-028: Session Search = FTS5 External-Content Index with LIKE Fallback

## Status
Accepted (2026-09-11, Phase 18)

## Context
ADR-006 shipped title search as a LIKE substring query and named
FTS5 as the known upgrade path ("if turn volume or search
requirements outgrow LIKE"). Two gaps motivated the migration now:
LIKE cannot do prefix/token matching ("deplo" finds nothing), and
LIKE scans every row (fine at dozens of sessions, not thousands).
Environment evidence: SQLite 3.49.1 with FTS5 available (ADR-004
Phase 1 audit; re-probed this session via a CREATE VIRTUAL TABLE
probe — available in the target build).

This is a schema change: Protocol v9 Section 14 treats migrations
as Ω-class when destructive. This one is purely ADDITIVE (new
virtual table + triggers; no existing table rewritten, no row
touched), with a tested fallback and a documented rollback.

## Decision
Add an FTS5 external-content index over session TITLES:

- **Structure**: `sessions_fts` as an external-content virtual
  table (`content='sessions', content_rowid='rowid'`) indexing
  `title` only. Turn-content search stays out of scope (ADR-006's
  search semantics are title-based; extending to content is a
  future decision with its own size/cost profile).
- **Sync**: INSERT/UPDATE/DELETE triggers on `sessions` keep the
  index consistent with the source table automatically.
- **Migration (idempotent)**: at store-open, `CREATE VIRTUAL TABLE
  IF NOT EXISTS` + triggers, then `INSERT INTO sessions_fts
  (sessions_fts) VALUES('rebuild')` ONLY when the table was just
  created (the rebuild command re-indexes from the content table —
  cheap at session scale, safe to repeat, but skipped when the
  table already exists to keep opens cheap).
- **Query safety**: search strings are UNTRUSTED input. Every
  token is double-quoted before assembly (`"tok"*` for prefix
  matching), which neutralizes FTS5 query syntax (OR/AND/NOT/NEAR
  and column filters become literals). A query that still fails
  to parse falls back to LIKE for that call — search never raises
  a syntax error at the user.
- **Fallback**: the store probes FTS5 availability at open. When
  the build lacks FTS5 (exotic SQLite compiles), the store opens
  in LIKE mode and every contract (including all tests) still
  holds. `force_like=True` constructor flag exercises the same
  path deterministically (the tested rollback).
- **Rollback**: `DROP TRIGGER IF EXISTS` x3 +
  `DROP TABLE IF EXISTS sessions_fts` restores the exact pre-phase
  schema. No data is lost — the index is derived data.

## Reversal Criteria
Revisit when (a) full-text search over turn content is requested
(size the index against real volumes first); (b) a downstream
reporting tool needs weighted/relevance ranking (bm25() tuning);
(c) SQLite builds in common deployment targets start shipping
without FTS5 (unlikely; the fallback absorbs it regardless).

## Sunset Review
Phase 19 (release) — the migration ships before the 1.0.0 freeze
so the first public release carries the indexed store.

## Consequences
### Positive
- Prefix/token search ("deplo" -> "deploy the app") with indexed
  lookups; ordering and archived-exclusion semantics unchanged.
- Additive-only migration with a tested fallback — no data risk.
- Untrusted queries are structurally injection-safe (quoted tokens).
### Negative
- Three triggers + one virtual table per sessions DB (schema
  surface grows; documented here).
- The rebuild-on-create pass scans titles once (cheap; bounded by
  session count).
### Neutral
- LIKE remains the executable fallback path, not dead code.

## Alternatives Considered
- Keep LIKE — rejected: the known prefix/token gap remains and
  ADR-006 already named this migration.
- Full-content FTS5 (titles + turns) — rejected for scope: the
  index size/cost profile needs real volume data first (reversal
  criterion (a)).
- Application-side tokenization cache — rejected: a second
  hand-rolled index beside SQLite duplicates platform machinery
  (the ADR-027 zero-dep lesson inverts: use what the platform has).

## References
- ADR-004 (journal; SQLite 3.49.1 FTS5 evidence), ADR-006 (the
  LIKE decision + named upgrade path)
- Protocol v9 §14 (migration care), §11.5 (input validation:
  untrusted query strings)
- Session probe evidence 2026-09-11: `CREATE VIRTUAL TABLE ... fts5`
  succeeds in the target build
