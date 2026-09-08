# ADR-019: Scheduler — One Execution Engine, Deterministic Time

## Status
Accepted

## Context
Scheduled work (addendum 38-39) must not create a second execution
engine (master prompt 57), must be bounded (addendum 39: budget,
cooldown), and must be testable without real sleeps.

## Decision
1. Four schedule kinds — ONCE, INTERVAL, DAILY, WEEKLY — cover P0/P1
   needs deterministically. NO cron-parser dependency (Section 9
   governance: the four kinds are ~40 lines of stdlib math; a parser
   is unjustified weight today).
2. **Firing enqueues into the shared TaskStore** — the scheduler owns
   WHEN, the durable task runtime owns execution. Exactly one engine.
3. Due-time math is pure and UTC-aware (naive input rejected):
   INTERVAL fires the LATEST FULLY-ELAPSED slot (a long-overdue entry
   fires once, not a burst — the per-entry cooldown paces the
   backlog); before the first slot it waits for slot 1.
4. Bounds: minimum cooldown 1s between fires per entry
   (rate-of-change control), per-tick execution budget (default 25),
   and per-entry max_executions (retires the entry; unbounded allowed
   only explicitly).
5. Self-maintenance jobs (addendum 39/100: expiry sweeps, reapers)
   run AFTER fires each tick, must be idempotent, own their budgets,
   and a failing job never breaks the tick — errors are journaled
   (SCHEDULE_FIRED payload), never swallowed.
6. `run_loop` is a reference daemon loop (tick + sleep until
   cancelled) for the Phase 10/11 hosts; the scheduler itself is fully
   testable through `tick()` with an injected clock.
7. Events v6: SCHEDULE_FIRED journaled per fire with correlation id
   `sched-<id>-<execution>`.

## Reversal Criteria
If a genuine cron-expression need appears (user-facing schedules,
Phase 11), add parsing AT THE ENTRY BOUNDARY producing the same four
kinds — the engine stays.

## Sunset Review
Phase 10 (TUI hosts run_loop), Phase 11 (Web surfaces schedule CRUD),
Phase 18 (tick-cost benchmarks at schedule volume).

## Consequences
### Positive
- Zero new dependencies; one engine; sleep-free tests.
- Backlog can never burst (cooldown pacing).
### Negative
- DAILY/WEEKLY use local wall-clock of the tz-aware datetime provided
  by the clock — the daemon decides tz policy (documented).
### Neutral
- Cron UI niceties (humanize "every Monday 9am") belong to Phase 11.

## Alternatives Considered
- APScheduler — rejected: heavy, own executor model (second engine).
- Celery/RQ — rejected: external broker violates local-first.

## References
- Master prompt 57; addendum 38-39, 57, 100; ADR-014 (TaskStore).
