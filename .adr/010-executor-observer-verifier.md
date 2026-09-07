# ADR-010: Executor, Observer, Verifier — Evidence Chain

## Status
Accepted

## Context
Execution must be bounded (retry limits), observable (journal + raw
observations), and verified with evidence (PASS/FAIL/UNCERTAIN where
UNCERTAIN is never PASS — master prompt 18-20, 109).

## Decision
1. `Executor.execute()` runs one invocation with the declared retry
   policy; permanent error codes (permission_denied, not_found,
   approval_required, risk_denied, unknown_tool, validation errors)
   are never retried — only transient failures retry with backoff.
   Every attempt journals TOOL_STARTED/COMPLETED/FAILED and records an
   observation. Gateway dispatch runs in a worker thread so the event
   loop stays responsive.
2. `ObservationLog`/`Observation` capture raw facts (tool, attempt, ok,
   error, data) — observation is data, never interpretation.
3. `Verifier` evaluates declarative checks against the observation
   index. Verdict rules: FAIL on counter-evidence; PASS only on positive
   evidence; UNCERTAIN when evidence is absent. Aggregation is
   worst-first (FAIL > UNCERTAIN > PASS): a PASS claim requires every
   check to pass.
4. `adversarial_recheck` implements the red-team pass (master prompt 20):
   it scans ALL attempts of a tool for hidden failures behind retries —
   "how could this result be wrong?" is a check, not a vibe.

## Reversal Criteria
If observation volume makes the in-memory log a bottleneck (measured),
spool to the journal — the Observation shape stays.

## Sunset Review
Phase 6 (durable task runtime adopts the executor), Phase 8 (verifier
quality metrics).

## Consequences
### Positive
- The evidence chain (journal + observations + verdicts) is complete:
  any PASS claim can be re-derived from recorded data.
- Hidden-failure-behind-retry is mechanically caught.
### Negative
- Checks are declarative predicates authored per task today; an LLM
  check-writer arrives with the full agent loop (Phase 6+).
### Neutral
- The verifier consumes observations; it never calls tools.

## Alternatives Considered
- LLM-as-judge only — rejected as sole mechanism: unverifiable,
  hallucination-prone; kept as a future check type with evidence.
- Retry-all-errors — rejected: blind retry of permanent failures is a
  Protocol v9 4.3 violation.

## References
- Master prompt 17-20, 109; addendum 12-14; Protocol v9 0.2, 15.1.
