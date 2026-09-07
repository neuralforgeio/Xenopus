# ADR-012: Skills — 7-Stage Lifecycle with Deterministic Trust Gates

## Status
Accepted

## Context
Skills alter future agent behavior, making them the highest-value
poisoning target (master prompt 134). Skill quality must be measurable
(30, 93) and regression must be detectable (37).

## Decision
1. Lifecycle is a closed 7-stage graph with a legal-transition table
   (mirroring ADR-003's approach): CANDIDATE -> STAGED -> EVALUATED ->
   EXPERIMENTAL -> TRUSTED, plus DEGRADED (re-entry to EVALUATED) and
   terminal SUPERSEDED. Illegal moves raise — no coercion.
2. Deterministic gate thresholds (no heuristics):
   - EVALUATED: >= 1 recorded evaluation.
   - EXPERIMENTAL: success rate >= 0.50.
   - TRUSTED: >= 3 evaluations AND lifetime success rate >= 0.75.
   - DEGRADED: >= 2 post-trust evaluations AND post-trust success rate
     <= 0.40 — regression judges behavior SINCE trust was granted, not
     the lifetime average (a 0.87 lifetime rate collapsing to 0.10
     recently IS a regression).
3. `trusted_at` is recorded at the TRUSTED transition; post-trust
   metrics are computed from evaluations after that timestamp.
4. Evaluations are append-only data (immutable history); metrics are
   computed, never edited. Provenance is mandatory at registration.
5. Registration deduplicates by name; duplicate registration raises.

## Reversal Criteria
If evaluation volume makes thresholds too slow to reach, tune the
constants — never bypass the gates. Constant changes ship with tests.

## Sunset Review
Phase 8 (learning controller drives evaluations), Phase 12 (skill
consolidation/review scheduler).

## Consequences
### Positive
- Trust is earned through repeated measured success; a poisoned skill
  cannot skip the evidence ladder.
- Regression semantics match the master prompt exactly (recent behavior).
### Negative
- 3-evaluation minimum slows adoption of genuinely good skills —
  accepted cost (safety > convenience).
### Neutral
- Suggested-skill flows (Phase 8) call the same gates as agents.

## Alternatives Considered
- LLM-judged skill quality — rejected as sole gate: unverifiable;
  kept as a future evaluation SOURCE feeding the same thresholds.
- Two-stage (candidate -> trusted) — rejected: no experimentation
  band where failures are cheap but contained.

## References
- Master prompt 28-30, 37, 90, 93, 134; Protocol v9 4.4.
