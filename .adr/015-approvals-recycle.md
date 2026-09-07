# ADR-015: Persistent Approvals + Recycle-Bin Deletes

## Status
Accepted

## Context
Phase 4's in-memory approval ledger loses pending approvals on restart,
and file_delete was wired destructive+irreversible -> DENY (ADR-008
noted the reversal condition). Phase 6 supplies both prerequisites.

## Decision
1. `ApprovalStore` persists approval requests in SQLite with identical
   semantics to the runtime ledger: hash-bound (via
   `persistent_action_hash` — binds tool + canonical arguments +
   subject + TASK id, because correlation ids may be regenerated
   across restarts), expiring, single-grant, replay-proof.
   `reap_expired()` is the maintenance sweep; history rows are kept
   (append-only audit).
2. `RecycleBin` moves deleted workspace content into
   `.xenopus-recycle/` under the workspace root with origin records;
   `restore()` refuses overwrite; `purge()` is explicit and manual.
   Nothing ever leaves the workspace boundary.
3. Gateway rewiring (ADR-008 reversal criteria satisfied): with a
   recycle bin attached, file_delete is destructive-but-REVERSIBLE ->
   the risk engine routes APPROVAL (not DENY); without one, it remains
   DENY. `plan_invocation` reflects the same wiring.
4. The recycle-backed delete handler returns the recycle entry id —
   restorability is a first-class result, not an afterthought.

## Reversal Criteria
If the bin grows unbounded in practice, add retention policy to the
Phase 9 scheduler — the restore-refuses-overwrite invariant stays.

## Sunset Review
Phase 9 (scheduled bin cleanup), Phase 11 (approval surface UI).

## Consequences
### Positive
- Destructive actions that once hard-failed are now approvable AND
  reversible; approvals survive crashes mid-flow.
### Negative
- Restore can be blocked by a new file at the origin path — explicit
  operator decision, by design.
### Neutral
- The runtime in-memory ledger remains for hot-path use; the store is
  the durable record.

## Alternatives Considered
- Copy-on-write snapshots per delete — rejected: heavier than a move;
  restore-refuses-overwrite covers the integrity case.
- Soft-delete flag rows — rejected: content must leave the tree for
  tools to observe deletion correctly.

## References
- Master prompt 55, 101; addendum 75-76; ADR-008 reversal criteria.
