# ADR-021: TUI Framework = Textual

> Renumbered from ADR-018 (2026-09-09) to resolve a numbering collision
> with ADR-018 (Reliability Records, Phase 8). Content unchanged except
> this note and the Decision addendum below.

## Status
Accepted

## Context
Xenopus needs a TUI that feels like a real terminal application: command
palette, task panels, approval prompts, diff viewer, scrollback. Two
candidates were evaluated: Textual and prompt_toolkit (already present in
the user's global environment). The decision was made during planning so
the Phase 10 roadmap is deterministic.

## Decision
Textual (8.2.8, MIT, verified Python 3.13-compatible via PyPI on 2026-09-07;
install-time compatibility re-verified 2026-09-09: `pip install textual==8.2.8`
clean on Python 3.13.3, `pip check` reports no broken requirements, imports OK)
is chosen as the TUI framework. Installation happens only in Phase 10 — not
before. The TUI consumes the RuntimeDriver contract (ADR-001); no runtime
logic lives in TUI modules. Phase 10 scope: `xenopus/tui/` package — app
skeleton, command palette, keybindings, panels over existing stores
(TaskStore, ApprovalStore, Scheduler, AgentPool watchdog states), a Textual
notification sink behind the SAME NotificationRouter policy (quiet hours and
digests respected — addendum 87), scheduler `run_loop` hosting, and a
killswitch command in-app. The TUI adds NO new runtime logic.

## Reversal Criteria
If Textual demonstrably blocks streaming renders for multi-agent output or
terminal performance on an i5-class/8 GB machine (Phase 10 benchmark), switch
to prompt_toolkit while keeping the view contract identical.

## Sunset Review
End of Phase 10, together with the TUI benchmark results (master prompt 119).

## Phase 10 Benchmark (2026-09-10, i5-8350U class, headless pilot)

- Engine baseline: TaskStore.list_tasks() x10 with 200 rows = 23.9 ms
  total (~2.4 ms/cycle) — the store itself is not the bottleneck.
- Full TUI refresh cycle (4 panels, 200-row tasks table) under a
  headless pilot: ~74 ms/cycle; boot + first refresh: ~329 ms.
- Verdict: a complete refresh occupies ~74 ms of the 2 s cadence —
  no event-loop blocking, streaming renders unaffected. Reversal
  criteria NOT triggered; Textual retained.

## Consequences
### Positive
- Ready-made components (palette, panels, trees) accelerate Phase 10.
### Negative
- A medium-sized dependency enters in Phase 10; footprint re-measured then.
### Neutral
- The TUI remains a thin surface; a framework change never touches the core.

## Alternatives Considered
- prompt_toolkit — rejected: components must be hand-assembled; Phase 10 cost
  exceeds the benefit of the smaller footprint.
- Rich alone (without full interactivity) — rejected: no command palette or
  input model as required (master prompt 76).

## References
- Master prompt Section 76
- User decision D-03 (planning session 2026-09-07)
