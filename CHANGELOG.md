# Changelog

All significant changes to Xenopus are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org).

The first public release of Xenopus will be **1.0.0**. During development,
internal versions take the form `1.0.0.devN` (development snapshots, no
public tag/release).

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
