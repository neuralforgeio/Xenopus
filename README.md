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
| Provider protocol + offline echo provider (local-first default) | IMPLEMENTED (Phase 3) |
| OpenAI-compatible HTTP adapter (timeout-enforced, schema-validated) | IMPLEMENTED (Phase 3) |
| Provider capability registry + circuit-breaker health tracking | IMPLEMENTED (Phase 3) |
| Deterministic policy router (quality/cost/latency/local/privacy) | IMPLEMENTED (Phase 3) |
| Context engine (budgeted assembly, protected segments, untrusted-content boundaries) | IMPLEMENTED (Phase 3) |
| Session store (persistent, fork lineage, search, archive, usage accounting) | IMPLEMENTED (Phase 3) |
| Tool contracts + registry (lazy discovery, schema-enforced) | IMPLEMENTED (Phase 4) |
| File tools with workspace path-boundary enforcement (traversal-proof) | IMPLEMENTED (Phase 4) |
| Permission engine (rule-ordered, deny-by-default, ALLOW/ASK/DENY) | IMPLEMENTED (Phase 4) |
| Risk engine (deterministic AUTO/APPROVAL/DENY decision table) | IMPLEMENTED (Phase 4) |
| Approval requests (hash-bound, expiring, replay-proof) | IMPLEMENTED (Phase 4) |
| Tool gateway pipeline (permission → risk → approval → execute → observe) | IMPLEMENTED (Phase 4) |
| Executor (bounded retries, permanent-failure short-circuit, journaling) | IMPLEMENTED (Phase 4) |
| Observer (raw evidence capture, never interpretation) | IMPLEMENTED (Phase 4) |
| Verifier (PASS/FAIL/UNCERTAIN, UNCERTAIN ≠ PASS, adversarial rechecks) | IMPLEMENTED (Phase 4) |
| Memory (provenance, evidence-gated promotion, scope isolation, TTL, supersession) | IMPLEMENTED (Phase 5) |
| Skills (7-stage lifecycle, deterministic trust gates, regression detection) | IMPLEMENTED (Phase 5) |
| Checkpoints (append-only snapshots per correlation) | IMPLEMENTED (Phase 5) |
| Structured logging with mandatory secret redaction | IMPLEMENTED (Phase 5) |
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
guardrails. Status: permission engine, risk engine, replay-proof approvals,
and the path-boundary-enforcing tool gateway are IMPLEMENTED (Phase 4,
[ADR-008](.adr/008-tool-gateway.md)/[009](.adr/009-permission-risk.md));
memory/skill governance arrives in Phase 5. See the [ADR series](.adr/).

## Local / Hybrid / Cloud

- **LOCAL (default):** runtime, state, sessions, and tools stay on your
  machine. Status: foundation IMPLEMENTED.
- **HYBRID:** local runtime with optional cloud models/providers. PLANNED.
- **CLOUD:** remote deployment. RESEARCH.

## Providers

The provider layer is provider-agnostic ([ADR-005](.adr/005-provider-layer.md)):
any OpenAI-compatible endpoint (OpenAI, OpenRouter, z.ai, LM Studio,
llama.cpp, Ollama, vLLM) works through one hardened adapter with
mandatory timeouts and strict response validation, plus an offline
`echo` provider that makes the runtime usable with zero keys and zero
network. Status: adapter, capability registry, circuit-breaker health
tracking, and the deterministic policy router (quality/cost/latency/
local/privacy-first) — IMPLEMENTED (Phase 3).

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

222 tests cover package imports, configuration, bootstrap, CLI, the
agent FSM (Hypothesis property invariants), goal lifecycle, plan DAG
validation, the event journal, budget/retry primitives, the provider
stack (mock-transport HTTP, no network), registry/health/router
determinism, the context engine, the session store, the permission
engine (deny-by-default property), the risk table (absolute-DENY
property), replay-proof approvals, file tools (path traversal
rejected), the full gateway pipeline, executor retry semantics, the
verifier (adversarial rechecks), memory (promotion gate, scope
isolation, supersession, TTL expiry, quality scoring), skills (full
trust ladder, threshold refusals, post-trust regression detection),
checkpoints, and the redacting structured logger (tokens never reach
sinks).

## Project Structure

```
src/xenopus/
├── runtime/        # core agent runtime — IMPLEMENTED (FSM, goal, plan, budget, context)
│   ├── fsm.py      #   20-state FSM with data-driven legal transitions
│   ├── goal.py     #   goal model + manager with plan-binding gate
│   ├── plan.py     #   task DAG: TaskNode, Plan, acyclic validator
│   ├── context.py  #   budgeted context assembly + untrusted-content boundaries
│   ├── events.py   #   canonical event vocabulary (v2) + Event envelope
│   └── budget.py   #   budget dimensions + bounded retry policy
├── persistence/    # durable local state — IMPLEMENTED
│   ├── journal.py  #   append-only SQLite WAL event journal
│   └── sessions.py #   session store with fork lineage + usage accounting
├── provider/       # model provider layer — IMPLEMENTED (ADR-005)
│   ├── protocol.py     #   ModelProvider protocol (the single seam)
│   ├── echo.py         #   offline local-first reference provider
│   ├── openai_compat.py#   hardened OpenAI-compatible HTTP adapter
│   ├── registry.py     #   capability catalog
│   ├── health.py       #   circuit-breaker health tracking
│   └── router.py       #   deterministic policy router
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
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Foundation: scaffold, toolchain, CI, governance | ✅ complete |
| 2 | Agent FSM · Goal · Plan (DAG) · event journal | ✅ complete |
| 3 | Provider layer · Context · Session | ✅ complete |
| 4 | Tool Gateway · Permission · Risk · Verifier | ✅ complete |
| 5 | Memory · Skills · Checkpoint · Observability | ✅ complete |
| 6 | Durable task runtime · remote control | not started |
| 7-8 | Orchestrator · agent pool · teams · aggregation | not started |
| 9 | Scheduler · notification router | not started |
| 10-11 | TUI · Local Web dashboard | not started |
| 12-14 | Telegram · Discord · webhooks | not started |
| 16 | Desktop shell | not started |
| 19 | First public release 1.0.0 | gated |

## Version

- Source of truth: [`pyproject.toml`](pyproject.toml) (ADR-002).
- Development snapshot: `1.0.0.dev4`.
- First public release: **1.0.0**.
- Policy: Semantic Versioning — no digit rollover at 10.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © 2026 Dearly Febriano Irwansyah (neuralforgeio)
