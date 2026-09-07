# ADR-003: Agent FSM — Data-Driven Transition Table

## Status
Accepted

## Context
The agent lifecycle (master prompt 14 + Rev.2) defines 20 states with a
strict set of legal transitions. An FSM implemented as scattered if/else
branches would make legality unauditable and drift-prone; Protocol v9
requires that illegal transitions be rejected mechanically and that every
state be nameable at any moment.

## Decision
1. `AgentState` is a StrEnum of the 20 canonical states.
2. `LEGAL_TRANSITIONS` is a module-level frozen mapping
   `{state -> frozenset(allowed targets)}` — the single source of legality.
   Exhaustiveness over all states is enforced by a property test.
3. `AgentFSM.transition()` raises `IllegalTransitionError` (with source and
   target names) for any transition absent from the table; terminal states
   map to empty sets, making "terminal -> anything" illegal by construction.
4. Optional guard predicates encode approval requirements; a denied guard
   raises `PermissionError` — guards never silently coerce state.
5. A Hypothesis property test asserts that for every (source, target) pair,
   observed legality equals the table — no illegal transition can exist
   outside the table, and no legal one can be missing.

## Reversal Criteria
If Phase 6+ execution requires a transition not in the table, the table
changes WITH a test update in the same commit — never a runtime bypass.

## Sunset Review
Phase 6 (durable task runtime) when pause/resume semantics gain
persistence-driven edge cases.

## Consequences
### Positive
- Legality is data, auditable in one place; property tests pin it.
- Illegal transitions fail loudly with greppable messages.
### Negative
- Adding states requires touching the table plus tests (intended friction).
### Neutral
- FSM does not emit events itself; callers journal via TransitionResult.

## Alternatives Considered
- Guard-clause ladder per state — rejected: unauditable, duplicates legality.
- Fully generic graph with runtime-configurable rules — rejected for now:
  configuration-driven state machines are a security risk surface (skill
  poisoning could rewrite agent behavior); table-in-code is safer.

## References
- Master prompt Section 14; Protocol v9 Section 0.1 (FSM states S0-S12 are
  the session protocol; this FSM is the agent lifecycle).
