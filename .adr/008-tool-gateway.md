# ADR-008: Tool Gateway Enforcement Pipeline

## Status
Accepted

## Context
Every tool invocation must pass one enforcement point (master prompt 39):
capability discovery -> selection -> permission -> risk -> execute ->
observe -> verify. Nothing may call a tool handler directly, and the
pipeline must fail closed at every gate.

## Decision
1. `ToolContract` declares name, description, input schema, side effects,
   required permissions, risk, timeout, idempotency, and failure modes —
   the registry refuses under-specified tools (master prompt 41).
2. `ToolRegistry` is explicit-registration only (no ambient discovery);
   `search()` returns metadata without touching handlers (lazy discovery,
   master prompt 40).
3. `ToolGateway.invoke()` runs the pipeline: permission (deny-by-default)
   -> risk (AUTO/APPROVAL/DENY) -> approval validation (hash-bound) ->
   dispatch -> ToolResult. Handlers return `ToolResult` shapes for
   ordinary failures; crashes convert to `tool_crash` — no exception ever
   crosses the gateway boundary.
4. File tools enforce a `PathPolicy` workspace boundary: every path
   resolves and must remain inside the root (traversal defense,
   Protocol v9 18.4). The policy is injected by the gateway as internal
   wiring (`__path_policy__`), never derived from untrusted arguments.
5. Deletion is wired as destructive + irreversible at the gateway, so
   the risk engine DENIES it under the default table; the APPROVAL path
   is exercised via plan-time decisions and the ledger tests. A
   production-grade recycle bin (prerequisite for reversible deletes)
   arrives with the durable task runtime in Phase 6.

## Reversal Criteria
If Phase 6's recycle mechanism makes workspace deletes provably
reversible, file_delete's factor wiring changes to APPROVAL — table
change with tests in the same commit.

> **AMENDED 2026-09-07 (Phase 6, ADR-015):** reversal criteria met.
> With a RecycleBin attached, file_delete routes APPROVAL
> (destructive-but-reversible); without one it stays DENY. Tests
> updated in the same commit as required.

## Sunset Review
Phase 6 (durable runtime + recycle), Phase 8 (tool reliability learning).

## Consequences
### Positive
- One door: permissions, risk, approvals, and boundary checks cannot be
  bypassed by any caller.
- Tools report failures as data — verification can reason over them.
### Negative
- The gateway is synchronous dispatch; async orchestration lives in the
  executor (documented split).
### Neutral
- Approval UI surfaces arrive with the gateway channels (Phase 12+).

## Alternatives Considered
- Decorator-based interception — rejected: implicit, easy to skip.
- Per-tool policy modules — rejected: duplicated enforcement logic.

## References
- Master prompt 39-43; addendum 74-76; Protocol v9 18.4, 11.5.
