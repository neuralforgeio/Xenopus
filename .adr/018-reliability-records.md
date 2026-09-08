# ADR-018: Reliability Records — Empirical Tool/Agent Statistics

## Status
Accepted

## Context
Tool selection and agent routing must improve from experience (master
prompt 88, addendum 107) — but only on actual measured performance,
never on assumptions, and never bypassing governance.

## Decision
1. `ReliabilityStore` (SQLite, WAL) records one row per real
   invocation: kind (tool/agent), subject (tool name / agent role),
   success, duration. Appended by the Executor (tools) and the
   Orchestrator (agents) when a store is attached.
2. `ReliabilityStats` aggregates: invocations, successes, failures,
   success_rate, avg duration. `rank` = success_rate − slowness penalty
   — deterministic, pure function of recorded data.
3. `best_for(kind)` ranks candidates deterministically (ties by
   subject name asc). Consumers ORDER BY rank; nothing is inferred.
4. This is the evidence base for Phase 12's learned routing: promotion
   of any routing preference still passes the existing gates (memory/
   skill promotion discipline). Recording statistics is safe;
   acting on them goes through governance.

## Reversal Criteria
If latency distributions need percentiles (p95) rather than means,
extend the schema additively — rank remains a pure function.

## Sunset Review
Phase 12 (routing policies consume best_for), Phase 18 (statistical
significance thresholds — small samples must not flip routing).

## Consequences
### Positive
- Reliability claims become evidence-backed, not vibes.
- The recording seam is injectable — tests and phases before 12 run
  unaffected without a store.
### Negative
- Two more writes per invocation (small SQLite inserts).
### Neutral
- Agent-role granularity (not per-run) matches the routing question.

## Alternatives Considered
- In-memory counters — rejected: survive-restart evidence is the point.
- Full metrics stack (prometheus-style) — deferred to observability
  Phase 18; this table is selection input, not dashboards.

## References
- Master prompt 88, 107-109; addendum 88.
