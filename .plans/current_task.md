# Current Task — Phase 2: Agent Core (FSM · Goal · Plan DAG · Event Journal)

## Objective
Ship the runtime data plane: agent FSM with mechanical legality, goal
model with plan-binding gate, task DAG with acyclic validation, budget
primitives, canonical event vocabulary, and an append-only SQLite journal
with correlation ids — all property-tested.

## Scope
- `runtime/budget.py` (Budget, RetryPolicy), `runtime/events.py` (v1
  vocabulary), `runtime/fsm.py` (20 states, table), `runtime/goal.py`,
  `runtime/plan.py` (TaskNode/Plan/PlanEngine), `persistence/journal.py`,
  `observability/correlation.py`
- Tests: unit + Hypothesis property invariants for each
- ADR-003 (FSM table), ADR-004 (journal), README/CHANGELOG updates

## Non-Goals
No executor/tool execution (Phase 4+), no provider calls (Phase 3), no
memory/skills (Phase 5), no TUI/Web (10+), no scheduler (9), no
parallel execution (7 — data plane only).

## Baseline Snapshot
- Pre-phase HEAD: badf7ce (remote == local, verified)
- Tests: 15 passed (Phase 1 exit) → target > 15
- Version: 1.0.0.dev0 → 1.0.0.dev1

## Impact Radius
Additive only: no existing behavior touched except MODULES test list and
subpackage docstrings. Consumers: future phases only.

## Contract Stability
New public contracts (AgentFSM, GoalManager, PlanEngine, EventJournal).
Freezing them now; breaking changes require MAJOR per policy.

## Test Strategy
Unit per component + property invariants (FSM table exhaustive, random
walks, goal determinism, DAG validation, journal order preservation).

## Rollback Strategy
`git revert <phase2-commit>` — additive change, < 5 minutes.

## Risk Assessment
- [E] Hypothesis 6.167.1 (MPL-2.0) compatible with 3.13 (PyPI verified)
- [I] SQLite WAL sufficient at current scale — validated by Phase 18 bench

## Abortion Criteria
Secret found; CI failure unresolved after 3 remediation attempts.

## Release Strategy Preview
Internal snapshot 1.0.0.dev1 — no tag, no release. First public: 1.0.0.

## Knowledge Artifacts
ADR-003, ADR-004, README (status/structure), CHANGELOG, WORKLOG entry.
