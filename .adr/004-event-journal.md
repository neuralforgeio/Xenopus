# ADR-004: Append-Only Event Journal in SQLite (WAL)

## Status
Accepted

## Context
Xenopus needs a canonical event source for audit trails, cross-surface
consistency (addendum 89), and future durable task runtime replay. The
environment audit (Phase 1) verified SQLite 3.49.1 with FTS5 available
in the standard library — zero runtime dependencies required.

## Decision
1. `EventJournal` persists `Event` envelopes into a single SQLite table
   `event_journal` on disk under the Xenopus home directory.
2. WAL mode: readers never block writers (future dashboard reads while
   the agent writes).
3. Append-only: rows are inserted, never updated or deleted. Corrections
   are new events (compensating records), preserving the audit trail.
4. The event vocabulary (`EventType`) is a closed StrEnum, versioned by
   `EVENT_SCHEMA_VERSION = 1`. Adding types = minor schema change;
   renaming/removing = major (journal carries the version per row).
5. Correlation ids are first-class: every event carries one; an index on
   `(correlation_id, occurred_at)` makes per-trail reads O(log n).
6. Ordering determinism: same-second events tie-break on `rowid` (append
   order) — discovered via a real test failure where random UUID event ids
   made same-second order non-deterministic.

## Reversal Criteria
If write throughput or concurrency requirements outgrow SQLite (measured,
not assumed), migrate to an embedded queue DB — the Event/EventType
contract and journal interface stay; only storage changes.

## Sunset Review
Phase 6 (durable task runtime) — validate that journal replay meets
recovery requirements; Phase 18 (performance benchmarks).

## Consequences
### Positive
- Zero new dependencies; proven local-first storage; time-travel reads.
- Deterministic replay for tests and crash recovery.
### Negative
- Unbounded growth requires a future compaction/retention policy
  (planned with Phase 6; tracked in the roadmap).
### Neutral
- Payloads are JSON text: schema validation stays at the type level.

## Alternatives Considered
- JSONL file append — rejected: no atomicity under crash, no indexed reads,
  no concurrent readers.
- Postgres — rejected: violates local-first single-binary deployment.
- sqlite-vec journal — rejected for events: vector search is for memory
  retrieval (Phase 5), not audit trails.

## References
- Master prompt 102-103, addendum 43-44; Protocol v9 Section 12.
