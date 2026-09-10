# ADR-002: Python 3.13.3, Toolchain, and Single Version Source

## Status
Accepted

## Context
The master prompt sets Python 3.13.3 as the runtime baseline and forbids
introducing additional language runtimes for the core. No runtime dependency
is needed for the foundation (stdlib-first principle). Versioning must be
singular — no two manifests may contradict each other (Protocol v9 Gate 8).

## Decision
1. `requires-python = ">=3.13,<3.14"` — the baseline is locked to 3.13.x.
2. **Single version source: `pyproject.toml`** (`version = "1.0.0.dev0"`).
   Python modules read it via `importlib.metadata`; no duplicated constants.
3. Dev toolchain (installed in a venv, never global): pytest 9.1.1,
   ruff 0.16.6, mypy 2.3.1 (strict), build backend hatchling. All MIT,
   verified compatible with Python 3.13 via PyPI metadata on 2026-09-07.
4. Two-tier version policy: internal `1.0.0.devN` without public tag/release;
   first public release = `1.0.0` + tag `v1.0.0` + GitHub Release (Phase 19).
   SemVer with no digit rollover at 10.
5. Textual (8.2.8, MIT) is chosen for the TUI (ADR-021) but is NOT installed
   in Phase 1 — installation is deferred to the TUI phase (Phase 10).

## Reversal Criteria
If a runtime dependency emerges that is incompatible with 3.13 with no
reasonable alternative, the dependency decision is escalated — NOT the
Python version (master prompt Section 00). The build backend may be replaced
if hatchling demonstrably gets in the way (with evidence).

## Sunset Review
When Python 3.13.3 stops receiving security fixes (EOL evaluation), or at
the first public release.

## Consequences
### Positive
- Reproducible builds, one version source, zero runtime dependencies in
  Phase 1.
### Negative
- Any feature needing an external library must pass the dependency approval
  matrix (Protocol v9 Section 9) each time.
### Neutral
- `pip install -e` is required so `importlib.metadata` can find the version.

## Alternatives Considered
- setuptools — rejected: hatchling is more declarative, no setup.py boilerplate.
- poetry — rejected: PEP 735 dependency-groups are already covered by pip;
  the extra toolchain adds no Phase 1 value.
- pyproject.toml + separate VERSION file — rejected: two sources of truth.

## References
- Master prompt Sections 00, 11, 12, 154-156
- Protocol v9 Section 5 Gate 8
