# ADR-014: Durable Task Runtime — Legal Transitions, Kill Switch, Recovery

## Status
Accepted

## Context
Tasks must survive process restarts (addendum 20-22): the user closes
the CLI mid-task and resumes later; a crash must never lose or silently
restart work. The kill switch (addendum 118) and crash recovery (master
prompt 127) both depend on durable state.

## Decision
1. `TaskStore` persists task rows (id, title, goal/plan refs, state,
   correlation id, attempt, max_attempts, timestamps) in SQLite WAL
   under the Xenopus home directory, colocated with the journal.
2. States: QUEUED/RUNNING/WAITING/PAUSED/RESUMABLE/COMPLETED/FAILED/
   CANCELLED with a legal-transition table (ADR-003 pattern): FAILED ->
   QUEUED only via `retry()` (attempt-bumped, budget-checked);
   COMPLETED/CANCELLED are terminal. Illegal moves raise TaskError.
   A Hypothesis-style property test pins table exhaustiveness.
3. Every transition journals a TASK_* event with the task's correlation
   id — the durable state and the audit trail move atomically in the
   same method.
4. `cancel_all` implements the kill-switch data path: cancels every
   live state, returns affected records, idempotent.
5. `mark_interrupted` is the crash-detection entry: RUNNING/WAITING ->
   RESUMABLE with TASK_INTERRUPTED journaled. Recovery NEVER restarts
   tasks — it surfaces them; the operator decides (remote control).
6. `KillSwitch.trigger()` cancels-all + journals KILLSWITCH_TRIGGERED
   (reason + affected ids) — state is preserved for post-incident
   review, never deleted.
7. `CrashRecovery.run()` wraps mark_interrupted idempotently; a clean
   restart finds nothing.

## Reversal Criteria
If Phase 7 orchestration needs WAITING→WAITING (re-wait), extend the
table with tests in the same commit — never a runtime bypass.

## Sunset Review
Phase 7 (orchestrator drives tasks), Phase 9 (scheduler enqueues via
the same store), Phase 18 (durable-write benchmarks).

## Consequences
### Positive
- Crash-safe: in-flight work is recoverable by construction; audit
  trail and state cannot diverge.
- Kill switch is one auditable operation.
### Negative
- CLI processes open/close stores per invocation (fine for control
  commands; long-running daemon arrives with TUI/Web phases).
### Neutral
- Task execution itself (agent loop) is Phase 7+; this is the state plane.

## Alternatives Considered
- Restart tasks automatically on recovery — rejected: surprising,
  unsafe (side effects may have partially landed).
- In-memory + journal replay — rejected: two sources of truth.

## References
- Master prompt 55-56, 127; addendum 20-22, 118; Protocol v9 15.4.
