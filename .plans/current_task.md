# Current Task — Corrective Remediation: English-Only Policy + Release Governance

## Objective
Permanently normalize all Xenopus-authored content to English, repair
documentation/metadata, validate SemVer wiring, and verify tag/release
integrity — without implementing any new product feature.

## Scope
- Translate all project-authored content to English (source, docstrings,
  comments, CLI strings, tests, README, CHANGELOG, ADRs, .plans, WORKLOG,
  pyproject comments, CI labels).
- Rebuild README as one coherent professional English document with honest
  status labels (IMPLEMENTED / EXPERIMENTAL / PLANNED / RESEARCH).
- Update GitHub description/topics (English, accuracy-only claims).
- Tag/release existence check + release-readiness assessment.
- Full gates + secret scan + hygiene + push + remote verification.

## Non-Goals
No Phase 2 features (FSM/Goal/Plan), no architecture changes, no new
dependencies, no TUI/Web/Desktop, no Vercel, no fabricated v1.0.0 release.

## Baseline Snapshot
- Pre-remediation HEAD: 46706d7 (remote == local, verified)
- Tests: 15 passed; lint/format/typecheck green (Phase 1 evidence)
- Tags: zero local, zero remote; Releases: zero (verified 2026-09-07)

## Impact Radius
Documentation-only + user-facing CLI strings + their tests. No behavioral
logic change. Consumers: repository readers, CI.

## Contract Stability
`xenopus --version` contract unchanged. CLI `doctor` output strings changed
(Indonesian → English) — documented in CHANGELOG; no external consumers yet.

## Test Strategy
Existing suite must remain green; assertions updated for new English
message strings (`FAILED`, `created`, `already present`, `empty`,
`Unknown subdirectory`).

## Rollback Strategy
`git revert <remediation-commit>` — single documentation commit, under
5 minutes, no data loss.

## Risk Assessment
- Missed Indonesian content remaining after audit — mitigated by
  repository-wide grep audit (evidence below).
- Test/message drift — mitigated by full gate re-run.

## Blast Radius Hypothesis
Worst case: CI red due to string mismatch — caught locally before push.

## Abortion Criteria
Secret found, identity mismatch, CI failure unresolved after 3 attempts.

## Release Strategy Preview
No tag, no release in this task. v1.0.0 requires the Section 59 checklist —
currently NOT satisfied (runtime is pre-alpha). Outcome: RELEASE BLOCKED
with a documented gate list.

## Knowledge Artifacts
CHANGELOG entry (Changed — language normalization); this plan; WORKLOG.

## Decision Ledger
- D-06: Permanent English-only policy for communication + all
  project-authored content (supersedes the Indonesian policy).
