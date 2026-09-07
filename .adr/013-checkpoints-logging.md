# ADR-013: Checkpoints + Structured Logging with Mandatory Redaction

## Status
Accepted

## Context
Recovery needs durable state snapshots (master prompt 55), and every
log line is a potential secret leak (master prompt 101, 140; Protocol
v9 12.1) — the runtime must be debuggable without ever printing
credentials.

## Decision
1. `CheckpointManager` persists append-only snapshot rows per
   correlation id: label, FSM state, and a JSON-validated snapshot of
   state references. latest_for/list_for give chronological recovery
   anchors; rewind/fork semantics arrive with the Phase 6 durable task
   runtime (documented substrate, not dead scope).
2. `StructuredLogger` emits one JSON object per line: UTC ISO timestamp,
   level (DEBUG/INFO/WARN/ERROR), service, correlation_id, message,
   plus redacted extras. Level filtering drops DEBUG below minimum.
3. **Redaction runs before any sink sees data**: credential-shaped
   strings (GitHub/OAuth tokens, sk- keys, AKIA ids, PEM blocks,
   key=value secrets) and values under sensitive-looking keys are
   replaced with [REDACTED] recursively.
4. Logging never raises: a broken sink degrades to stderr, then drops
   silently — observability must not take the runtime down.

## Reversal Criteria
If secret shapes evolve (new token formats), extend the pattern table —
the redaction-before-sink invariant is not negotiable.

## Sunset Review
Phase 6 (checkpoints feed crash recovery), Phase 18 (log-volume
benchmarks; rotation policy).

## Consequences
### Positive
- Crash recovery has durable anchors; secrets cannot reach sinks by
  construction (tests pin tokens never appear in output).
### Negative
- Pattern-based redaction can over-redact (false positives) — accepted:
  over-redaction is safe, under-redaction is not.
### Neutral
- Metrics/traces arrive Phase 6+ on the same correlation spine.

## Alternatives Considered
- stdlib logging with filters — rejected: filter ordering is fragile;
  redaction belongs inside the emit path, not bolted on.
- File-only sinks — rejected for now: injectable sink keeps tests
  hermetic and future channels (Phase 12) pluggable.

## References
- Master prompt 55, 101, 140; Protocol v9 12.1, 11.2.
