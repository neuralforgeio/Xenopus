# ADR-018: TUI Framework = Textual

## Status
Accepted

## Context
Xenopus needs a TUI that feels like a real terminal application: command
palette, task panels, approval prompts, diff viewer, scrollback. Two
candidates were evaluated: Textual and prompt_toolkit (already present in
the user's global environment). The decision was made during planning so
the Phase 10 roadmap is deterministic.

## Decision
Textual (8.2.8, MIT, verified Python 3.13-compatible via PyPI on 2026-09-07)
is chosen as the TUI framework. Installation happens only in Phase 10 — not
before. The TUI consumes the RuntimeDriver contract (ADR-001); no runtime
logic lives in TUI modules.

## Reversal Criteria
If Textual demonstrably blocks streaming renders for multi-agent output or
terminal performance on an i5-class/8 GB machine (Phase 10 benchmark), switch
to prompt_toolkit while keeping the view contract identical.

## Sunset Review
End of Phase 10, together with the TUI benchmark results (master prompt 119).

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
