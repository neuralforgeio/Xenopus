# ADR-001: Xenopus Runtime and Module Boundary

## Status
Accepted

## Context
Xenopus is a multi-surface agent runtime (CLI, TUI, Web, Desktop, Telegram,
Discord) whose largest architectural risk is a single "god module" owning
planning, execution, tools, memory, and UI at once. Protocol v9 forbids
this (Red Zone 4.1), and the orchestration addendum requires that channels
never anchor into the core.

## Decision
The package is divided into subpackages with exclusive ownership:

- `xenopus.runtime` — the core AgentRuntime (FSM, Goal, Plan, Orchestrator).
- `xenopus.gateway` — channel adapters + notification router; NEVER imported
  by `runtime`.
- `xenopus.tools` — the Tool Gateway: discovery, permission, risk, execution.
- `xenopus.memory` — provenance-based memory; promotion requires evidence.
- `xenopus.skills` — skill registry + 7-stage lifecycle, no auto-trust.
- `xenopus.provider` — model provider abstraction; the core stays
  provider-agnostic.
- `xenopus.observability` — structured logs, metrics, traces, correlation IDs.

One-way dependency rule: interface (cli/tui/web) → runtime → (tools, memory,
skills, provider) → observability. The gateway only consumes the
`RuntimeDriver` contract to be defined in Phase 2.

## Reversal Criteria
If any two subpackages ever import each other (cyclic dependency), or
`runtime` is found importing a channel module, this decision has failed and
the boundary must be redrawn.

## Sunset Review
Re-examined every time a new subpackage is added (earliest: Phase 9).

## Consequences
### Positive
- Blast radius of failure is contained per module; testable per boundary.
- A channel outage never takes down the core (addendum 82-84).
### Negative
- Requires import discipline; a linter must guard the dependency direction.
### Neutral
- More files, but each is small and single-owner.

## Alternatives Considered
- Monolithic `xenopus/core.py` — rejected: violates Red Zone 4.1 (god module).
- Feature-based packaging — rejected: features shift between phases;
  ownership boundaries stay stable.

## References
- Protocol v9 Section 4.1, Section 19
- Xenopus master prompt Sections 09, 75, 79, 113
