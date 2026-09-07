# Changelog

All significant changes to Xenopus are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org).

The first public release of Xenopus will be **1.0.0**. During development,
internal versions take the form `1.0.0.devN` (development snapshots, no
public tag/release).

## [Unreleased — 1.0.0.dev1]

### Added
- Agent FSM: 20 canonical states with a data-driven legal-transition
  table; illegal transitions raise with named source/target; terminal
  states reject everything by construction; approval guards raise
  `PermissionError` instead of silently coercing (ADR-003).
- Goal model and manager: validated drafts (objective, success criteria,
  constraints, non-goals, priority, risk tolerance, budget), lifecycle
  enforcement (DRAFT -> ACTIVE -> ACHIEVED/ABANDONED), and an
  `require_active` gate so plans can only bind to active goals.
- Plan engine: `TaskNode` (dependencies, agent type, tool constraints,
  budget, retry policy, timeout, output schema), `Plan` DAG with acyclic
  validation (cycles, self-dependencies, unknown dependencies, orphans,
  duplicate ids all rejected), and deterministic topological ordering.
- Budget primitives: five bounded dimensions (tokens, time, cost, tool
  calls, child agents) plus a bounded retry policy with exponential
  backoff.
- Canonical event vocabulary (v1, 18 event types) and an immutable `Event`
  envelope carrying correlation id and schema version.
- Append-only event journal on SQLite (WAL mode) with per-correlation
  indexed reads and deterministic append-order replay (ADR-004).
- Correlation id generation for end-to-end tracing across boundaries.
- Hypothesis property tests: FSM legality matches the table exactly for
  any state pair, random walks never corrupt the FSM, any valid goal draft
  constructs deterministically, linear chains always validate and order,
  arbitrary edge sets either validate or name a precise error, and any
  journal append sequence round-trips in order.

## [Unreleased — 1.0.0.dev0]

### Added
- Repository foundation: the `xenopus` package structure (runtime, gateway,
  tools, memory, skills, provider, observability) with clear module
  ownership boundaries.
- Single version source in `pyproject.toml` (ADR-002).
- Local-first runtime configuration (`~/.xenopus/`, overridable via
  `XENOPUS_HOME`).
- Runtime bootstrap: idempotent home-directory creation + diagnostics.
- Basic CLI: `xenopus --version` and `xenopus doctor`.
- Basic test harness: package imports, version availability, configuration,
  bootstrap.
- Development toolchain: pytest, ruff, mypy (strict), hatchling (ADR-002).
- GitHub Actions CI: lint, format, typecheck, tests, wheel build,
  and a release tag-parity guard (Protocol v9 Section 17.9).
- Governance artifacts: `.plans/`, ADR-001/002/018, WORKLOG.

### Changed
- (2026-09-07) Permanent project language normalized to English-only across
  all project-authored content: source docstrings/comments, CLI strings,
  tests, README, CHANGELOG, ADRs, plans, and CI labels. No functional code
  changes — user-facing CLI strings (`FAILED`, `created`,
  `already present`) and error messages updated alongside their tests.
