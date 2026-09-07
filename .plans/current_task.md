# Current Task — Phase 4: Tool Gateway · Permission · Risk · Executor · Observer · Verifier

## Objective
Ship the enforcement spine: tool contracts + registry, path-bounded file
tools, permission/risk/approval engines, the gateway pipeline, the
bounded-retry executor with journaling, the observer, and the
evidence-based verifier with adversarial rechecks.

## Scope
- tools/{contracts,files,registry,gateway}.py
- runtime/{permission,risk,approval,observer,executor,verifier}.py
- events v3 (TOOL_* additive); tests incl. property invariants
- ADR-008/009/010; README/CHANGELOG; version 1.0.0.dev3

## Non-Goals
Memory/skills (Phase 5), durable task runtime/approval persistence (6),
multi-agent (7-8), scheduler (9), channels (12+). file_delete DENY-by-
table is intentional; recycle bin lands in Phase 6.

## Baseline Snapshot
- Pre-phase HEAD: 2a899c9 (remote == local verified)
- Tests: 126 passed (Phase 3 exit) -> 172
- Version: 1.0.0.dev2 -> 1.0.0.dev3

## Test Strategy
Unit (permission rules, risk table, approvals incl. replay), file tools
(traversal attacks), gateway pipeline (fail-closed matrix), executor
(retry semantics + journaling), verifier (UNCERTAIN≠PASS, adversarial).
Property: empty-ruleset always DENY; destructive+irreversible always DENY.

## Rollback Strategy
git revert of the phase commit; additive; < 5 min.

## Release Strategy Preview
Internal snapshot 1.0.0.dev3 — no tag/release.

## Knowledge Artifacts
ADR-008, ADR-009, ADR-010; README security/testing; CHANGELOG.
