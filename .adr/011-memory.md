# ADR-011: Memory — Provenance, Promotion Gate, Scope Isolation

## Status
Accepted

## Context
Memory is the substrate of experience reuse (master prompt 22-27, 92)
and the primary poisoning target (master prompt 133): a fabricated
"memory" that reaches ACTIVE status can steer every future decision.

## Decision
1. Every memory item is created as CANDIDATE with a closed 7-status
   lifecycle (CANDIDATE/ACTIVE/STALE/SUPERSEDED/EXPIRED/ARCHIVED/
   REJECTED). `create()` physically cannot produce ACTIVE items —
   auto-trust is structurally impossible.
2. `PromotionGate` is deterministic: promotion requires attached
   Evidence (observation id or journal correlation — never a bare
   assertion) AND confidence >= 0.5. Same inputs -> same decision.
3. Evidence that references nothing is rejected at construction —
   claims without verification paths cannot exist (P1 alignment).
4. Scope isolation by construction: every query carries scope + scope_ref;
   GLOBAL/USER/WORKSPACE/PROJECT/TASK/TEMPORARY never leak across.
   Cross-scope supersession is refused.
5. Supersession stores the replacement as a fresh CANDIDATE pointing
   back via superseded_by — corrections never inherit trust.
6. TTL expiry transitions ACTIVE -> EXPIRED (injectable clock, no sleeps);
   staleness is explicit (mark_stale), never silent.
7. Quality is COMPUTED (provenance, freshness, reuse, corrections),
   never stored — it cannot drift from the underlying counters.

## Reversal Criteria
If Phase 8 (learning) needs contradiction resolution beyond
supersession, add a merge strategy as a NEW operation — never weaken
the promotion gate or scope isolation.

## Sunset Review
Phase 8 (learning promotion consumes memory), Phase 12 (consolidation
pipeline), Phase 18 (retrieval benchmarks).

## Consequences
### Positive
- Poisoning requires forging evidence references, which the journal/
  observation stores independently record.
- Scope leaks are structurally impossible (query API shape).
### Negative
- Every memory needs explicit promotion — no convenience auto-accept.
### Neutral
- Working memory stays in-context (not this store) per master prompt 22.

## Alternatives Considered
- Auto-promote high-confidence items — rejected: confidence is
  self-reported; evidence is not.
- One global namespace — rejected: cross-project leakage is a P1-class
  integrity failure (master prompt 139).

## References
- Master prompt 22-27, 92, 133, 139; Protocol v9 4.4, 15.1.
