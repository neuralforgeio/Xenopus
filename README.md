<div align="center">

<img src="docs/assets/xenopus.png" alt="Xenopus logo" width="180"/>

# Xenopus

**A self-improving autonomous AI agent runtime.**

Local-first · Verifiable · Model-agnostic · Python 3.13

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB.svg)](pyproject.toml)
[![CI](https://github.com/neuralforgeio/Xenopus/actions/workflows/ci.yml/badge.svg)](https://github.com/neuralforgeio/Xenopus/actions/workflows/ci.yml)

</div>

---

## Overview

Xenopus is a runtime for **autonomous AI agents that improve themselves in
verifiable ways**. An agent does not merely call tools — Xenopus runs a full
loop:

```
Perceive → Understand → Goal → Plan → Act → Observe → Verify →
Reflect → Learn → Consolidate → Improve → Reuse
```

Every claim requires evidence. Every destructive action requires
authorization, risk assessment, observability, and a recovery path. Every
learned behavior passes a governance gate before promotion.

## Vision

> A safe, verifiable agent runtime — running locally on your machine,
> growing more effective through validated experience, and never improving
> itself without evidence, evaluation, safety, and rollback.

## Current Status

**Pre-Alpha — Phase 1 (Foundation) complete.** Xenopus is being built in
strictly gated phases. Capabilities listed below as PLANNED are **not yet
available**.

| Capability | Status |
|---|---|
| Repository foundation, toolchain, CI | IMPLEMENTED |
| Local-first configuration (`~/.xenopus/`, `XENOPUS_HOME`) | IMPLEMENTED |
| Idempotent state bootstrap + `xenopus doctor` CLI | IMPLEMENTED |
| Agent FSM (20 states, legal-transition table) | IMPLEMENTED (Phase 2) |
| Goal manager (validation, lifecycle, plan-binding gate) | IMPLEMENTED (Phase 2) |
| Plan engine (task DAG model, acyclic validation, topological order) | IMPLEMENTED (Phase 2) |
| Append-only event journal (SQLite WAL, correlation IDs) | IMPLEMENTED (Phase 2) |
| Budget & retry policy primitives | IMPLEMENTED (Phase 2) |
| Provider layer · Context · Sessions | PLANNED (Phase 3) |
| Tool Gateway · Permission · Risk · Verifier | PLANNED (Phase 4) |
| Memory · Skills · Checkpoints · Observability | PLANNED (Phase 5) |
| Durable task runtime · Remote control | PLANNED (Phase 6) |
| Multi-agent orchestration · parallel agents | PLANNED (Phase 7-8) |
| Scheduler · Notification routing | PLANNED (Phase 9) |
| TUI (Textual) | PLANNED (Phase 10, ADR-018) |
| Local Web dashboard | PLANNED (Phase 11) |
| Telegram / Discord / webhooks | PLANNED (Phase 12-14) |
| Desktop shell | PLANNED (Phase 16) |
| Self-improvement / self-repair loops | PLANNED (Phase 12) |
| Fine-tuning pipeline | RESEARCH |

## Core Architecture (Design Direction)

```
Interfaces (CLI · TUI · Web · Desktop · Telegram · Discord)
        ↓ RuntimeDriver (stable contract)
Gateway Layer (channel adapters · notification router)
        ↓
Agent Runtime (FSM · Goal · Planner · Executor · Verifier)
        ↓
Tool Gateway · Permission Engine · Risk Engine · Sandbox
        ↓
Memory · Skills · Experience · Provider/Model Router
```

Principles: one core runtime, many surfaces; channels never enter the core;
every module owns an exclusive boundary ([ADR-001](.adr/001-runtime-boundary.md)).

## Security (Design Posture)

Deny-by-default permissions, risk-gated destructive actions, evidence-based
memory/skill promotion (poisoning defense), secret redaction in logs, and
sandboxed execution boundaries are foundational design rules — the runtime
is being built so that autonomy is only ever exercised inside verifiable
guardrails. Status: PLANNED (phases 4+); see the [ADR series](.adr/).

## Local / Hybrid / Cloud

- **LOCAL (default):** runtime, state, sessions, and tools stay on your
  machine. Status: foundation IMPLEMENTED.
- **HYBRID:** local runtime with optional cloud models/providers. PLANNED.
- **CLOUD:** remote deployment. RESEARCH.

## Providers

Model providers (OpenAI-compatible endpoints and others) connect through a
provider-agnostic abstraction with routing and fallback. Status: PLANNED
(Phase 3). The core never imports provider-specific code.

## Installation

Xenopus has not shipped a stable release yet. Development snapshot:

```bash
git clone https://github.com/neuralforgeio/Xenopus.git
cd Xenopus
python -m venv .venv
# Windows: .venv\Scripts\activate   |   POSIX: source .venv/bin/activate
pip install -e . --group dev
```

The first public release will be **1.0.0** (Semantic Versioning).

## Development

```bash
ruff check .            # lint
ruff format --check .   # format check
mypy                    # strict typecheck
pytest                  # test suite
python -m build --wheel # package validation
```

All gates run in CI on every push ([CI workflow](.github/workflows/ci.yml)),
including a release-parity guard that fails the build when a version tag
lacks a published GitHub Release.

## Testing

71 tests currently cover package imports, configuration, bootstrap
idempotency, CLI behavior, the agent FSM (including Hypothesis property
invariants — no illegal transition can exist outside the legal table),
goal lifecycle rules, plan DAG validation (cycles, orphans, deterministic
topological order), and the event journal (append order, correlation
isolation, schema versioning).

## Project Structure

```
src/xenopus/
├── runtime/        # core agent runtime — IMPLEMENTED (FSM, goal, plan, budget)
│   ├── fsm.py      #   20-state FSM with data-driven legal transitions
│   ├── goal.py     #   goal model + manager with plan-binding gate
│   ├── plan.py     #   task DAG: TaskNode, Plan, acyclic validator
│   ├── events.py   #   canonical event vocabulary (v1) + Event envelope
│   └── budget.py   #   budget dimensions + bounded retry policy
├── persistence/    # durable local state — IMPLEMENTED (event journal)
│   └── journal.py  #   append-only SQLite WAL journal
├── gateway/        # multi-channel transport adapters — Phase 12+
├── tools/          # tool gateway: discovery, permission, risk — Phase 4
├── memory/         # provenance-based memory — Phase 5
├── skills/         # skill registry + lifecycle — Phase 5
├── provider/       # model provider abstraction + router — Phase 3
├── observability/  # correlation IDs IMPLEMENTED; logs/metrics Phase 5
├── config.py       # local-first configuration
├── bootstrap.py    # idempotent state bootstrap
└── cli.py          # CLI surface
.adr/               # architecture decision records
.plans/             # engineering governance artifacts
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation: scaffold, toolchain, CI, governance | ✅ complete |
| 2 | Agent FSM · Goal · Plan (DAG) · event journal | ✅ complete |
| 3 | Provider layer · Context · Session | not started |
| 4 | Tool Gateway · Permission · Risk · Verifier | not started |
| 5 | Memory · Skills · Checkpoint · Observability | not started |
| 6 | Durable task runtime · remote control | not started |
| 7-8 | Orchestrator · agent pool · teams · aggregation | not started |
| 9 | Scheduler · notification router | not started |
| 10-11 | TUI · Local Web dashboard | not started |
| 12-14 | Telegram · Discord · webhooks | not started |
| 16 | Desktop shell | not started |
| 19 | First public release 1.0.0 | gated |

## Version

- Source of truth: [`pyproject.toml`](pyproject.toml) (ADR-002).
- Development snapshot: `1.0.0.dev1`.
- First public release: **1.0.0**.
- Policy: Semantic Versioning — no digit rollover at 10.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © 2026 Dearly Febriano Irwansyah (neuralforgeio)
