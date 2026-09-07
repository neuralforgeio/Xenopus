# ADR-006: Session Store in SQLite with Fork Lineage

## Status
Accepted

## Context
Sessions must be persistent, searchable, forkable, and archivable
(master prompt 53-54), with usage accounting for the future cost manager
(66). Same storage rationale as ADR-004 applies: local-first, stdlib
sqlite3, WAL mode.

## Decision
1. `SessionStore` persists sessions + turns in SQLite (WAL) under the
   Xenopus home directory, colocated with (but separate from) the event
   journal.
2. Fork semantics: `fork()` creates a new session row with
   `parent_session_id` lineage AND copies the parent's turns — a fork is
   a content-bearing branch, not just an id pointer.
3. `lineage()` walks the parent chain root-first (arbitrary depth is
   bounded by the chain length; no cycles are possible because parents
   must already exist at fork time).
4. Archived sessions are immutable: `append_turn` on an archived session
   raises. Archiving is idempotent.
5. Usage accounting: each turn stores prompt/completion tokens and cost
   in micro-USD integers (float-for-money is forbidden — Protocol v9
   4.4; micro-integer arithmetic avoids representation drift).
6. Search: title LIKE query over non-archived sessions, newest first.
   FTS5 full-text search over turn content is a Phase 9+ enhancement
   when volume justifies it.

## Reversal Criteria
If turn volume or search requirements outgrow LIKE, migrate to FTS5
behind the same `search()` interface — callers unaffected.

## Sunset Review
Phase 6 (durable task runtime) when sessions link to task records.

## Consequences
### Positive
- Fork lineage is auditable and cheap; usage totals sum in O(n) per session.
- Integer micro-USD: no floating-point money bugs.
### Negative
- LIKE search is substring-only (documented; FTS5 upgrade path known).
### Neutral
- Sessions and events are separate stores linked by correlation ids.

## Alternatives Considered
- JSONL session files — rejected: no indexed search, no transactions.
- One mega-DB with journal — deferred: separate schema lifecycles.

## References
- Master prompt 53-54, 66; Protocol v9 4.4.
