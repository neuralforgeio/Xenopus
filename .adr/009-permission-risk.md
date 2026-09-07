# ADR-009: Permission + Risk as Data-Driven Tables

## Status
Accepted

## Context
Permissions (ALLOW/ASK/DENY, master prompt 68) and risk (AUTO/APPROVAL/
DENY, 69) must be deterministic, auditable, and safe against skill/
memory poisoning (addendum 96: no learned artifact may weaken them).

## Decision
1. `PermissionEngine` evaluates an ordered rule list, first-match-wins,
   with an absolute DENY-all fallback: an empty rule set denies
   everything (property-tested). Rules are immutable data rows the
   runtime can serialize and audit.
2. `RiskEngine` maps declared factors (destructiveness, external side
   effects, reversibility, sensitivity, blast radius) through a fixed
   weight table to AUTO (<4) / APPROVAL (>=4) / DENY (>=8), with one
   absolute row: destructive AND irreversible is always DENY regardless
   of score (master prompt 69: reversibility first).
3. Approvals are hash-bound (`action_hash` over tool + canonical-sorted
   arguments + subject + correlation), expiring (default 300s), and
   single-use semantics per request; resolve() rejects replays against
   different actions, expired grants, and wrong subjects (addendum 75-76).
4. No heuristic inference anywhere: same inputs produce the same outcome,
   always. Policy mutation by learned artifacts is structurally absent —
   the engines expose no mutation API beyond explicit rule additions.

## Reversal Criteria
If fine-grained path-based permissions are needed (per-file grants),
extend the rule schema additively — the fallback and first-match
semantics stay.

## Sunset Review
Phase 6 (durable approvals), Phase 12 (remote approval surfaces).

## Consequences
### Positive
- Auditable: the whole policy is printable data; property tests pin
  deny-by-default and the absolute DENY row.
- Replay-proof approvals: an old grant cannot authorize a new action.
### Negative
- Rule lists grow linearly; a future admin surface (Phase 11) will need
  rule search/UX.
### Neutral
- ASK maps to the approval pipeline: without a live approver the call
  fails closed.

## Alternatives Considered
- Heuristic scoring (fuzzy weights) — rejected: unauditable, drift-prone.
- Role-based access control — deferred: single-user local runtime today;
  RBAC lands with multi-user channels (Phase 12+).

## References
- Master prompt 68-69; addendum 75-76, 96; Protocol v9 0.3.
