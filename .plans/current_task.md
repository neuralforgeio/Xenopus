# Current Task — Phase 3: Provider Layer · Context Engine · Sessions

## Objective
Ship the model provider seam (protocol, echo, OpenAI-compatible adapter,
registry, health, router), the budgeted context engine, and the session
store with fork lineage — offline-testable end to end.

## Scope
- provider/{types,protocol,echo,openai_compat,registry,health,router}.py
- runtime/context.py, persistence/sessions.py, events v2
- Tests incl. MockTransport HTTP paths + property invariants
- ADR-005/006/007, README/CHANGELOG updates, version 1.0.0.dev2

## Non-Goals
No tool execution (Phase 4), memory/skills (5), durable task runtime (6),
TUI/Web (10+), no live network calls anywhere in tests.

## Baseline Snapshot
- Pre-phase HEAD: 43f2f5b (remote == local, verified)
- Tests: 71 passed (Phase 2 exit) -> 126 target
- Version: 1.0.0.dev1 -> 1.0.0.dev2

## Impact Radius
Additive. First runtime dependency added (httpx) — governed (ADR-005).

## Test Strategy
Unit + async (pytest-asyncio, MockTransport) + Hypothesis property
(context budget invariants). Determinism tests for router/health.

## Rollback Strategy
git revert of the phase commit; < 5 min; no data migrations.

## Release Strategy Preview
Internal snapshot 1.0.0.dev2 — no tag/release.

## Knowledge Artifacts
ADR-005, ADR-006, ADR-007; README provider/testing sections; CHANGELOG.
