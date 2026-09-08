# ADR-017: Aggregation by Evidence + Verified Synthesis

## Status
Accepted

## Context
Parallel agents overlap and contradict (addendum 12, 63-65). Naive
merging hides disagreement; naive voting lets N weak claims outvote
one strong one. The final answer must pass verification, never ship
straight from parallel results.

## Decision
1. **Claims:** AgentResults emit claim sets (key + content) through
   their artifacts; the aggregator extracts them from COMPLETED
   results only. Claims carry the agent's evidence references.
2. **Ranking is evidence-first** (deterministic): evidence count desc,
   then confidence desc, then claim id. A 0.9-confidence unevidenced
   claim NEVER outranks a 0.5-confidence claim with more evidence —
   property-tested (anti-voting invariant).
3. **Conflict resolution (addendum 63):** content-identical claims
   deduplicate to the top-ranked member. Distinct content on one key:
   - SUPERSEDED when the winner's evidence strictly dominates every
     alternative (>= 1 more ref).
   - CONFLICT otherwise: nothing merged, nothing voted; the strongest
     claim stays visible and the CONFLICT record names the disagreement.
   - Confidence degrades 0.25 per unresolved conflict (floored at 0) —
     disagreement never reports as high trust.
4. **Synthesizer (addendum 64-65):** pipeline is aggregate -> verifier
   checks -> verdict. Conflicts and missing evidence are EXPLICIT
   UNCERTAINTY (UNCERTAIN verdict with named doubts); REJECTED is
   reserved for checks that actively fail on present evidence. TRUSTED
   requires zero uncertainties — enforced as a constructor invariant.
5. **Teams (addendum 16):** TeamCoordinator composes orchestrator ->
   aggregator -> synthesizer; TeamRecord binds identity/roles/budget;
   TeamRunLedger keeps the audit chain. NO new execution engine —
   the team is composition over the Phase 7 plane.

## Reversal Criteria
If Phase 12 (learning) needs weighted evidence (source quality per
master prompt 150), extend Claim with source-quality scoring — the
anti-voting invariant stays untouched.

## Sunset Review
Phase 12 (orchestration memory consumes AggregationReports; conflict
statistics feed strategy learning).

## Consequences
### Positive
- Disagreement is always visible; confidence tracks evidence reality.
- TRUSTED synthesis is mechanically trustworthy (invariant-tested).
### Negative
- Ties on evidence+confidence resolve by claim id (arbitrary but
  deterministic) — documented; real systems rarely tie.
### Neutral
- COEXIST resolution defined but unused until scope-bearing keys exist
  (addendum 63 narrow-scope option; deferred with intent).

## Alternatives Considered
- Majority voting — rejected: violates the anti-voting requirement.
- LLM-judged resolution — rejected as sole arbiter: unverifiable; may
  join as a future evidence SOURCE, never the ranking rule.

## References
- Addendum 12, 63-65, 16; Protocol v9 0.2 (Channel B/C).
