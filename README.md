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
| Durable task runtime (persisted lifecycle: QUEUED→…→COMPLETED/FAILED/CANCELLED) | IMPLEMENTED (Phase 6) |
| Remote control: `xenopus task create/list/inspect/start/pause/resume/cancel/retry/recover` | IMPLEMENTED (Phase 6) |
| Kill switch: `xenopus killswitch` cancels all live tasks with audit trail | IMPLEMENTED (Phase 6) |
| Crash recovery (interrupted tasks flagged RESUMABLE, never auto-restarted) | IMPLEMENTED (Phase 6) |
| Persistent approvals (hash-bound, expiring, crash-surviving) | IMPLEMENTED (Phase 6) |
| Recycle bin: reversible deletes (file_delete DENY→APPROVAL, ADR-008 amendment) | IMPLEMENTED (Phase 6) |
| Agent model: profiles, structured contracts + results (never raw prompts/text) | IMPLEMENTED (Phase 7) |
| Agent pool: admission control (MAX_CONCURRENT_AGENTS=4), depth/child/total limits | IMPLEMENTED (Phase 7) |
| Heartbeat watchdog with stuck detection (never kills silently) | IMPLEMENTED (Phase 7) |
| Orchestrator: parallel DAG layers with failure isolation + partial-failure reports | IMPLEMENTED (Phase 7) |
| Pre-parallelization cost gate (overhead/benefit model) | IMPLEMENTED (Phase 7) |
| Tool quarantine (fails closed; lift after review) | IMPLEMENTED (Phase 7) |
| Result aggregator: evidence-first ranking, no naive voting (anti-voting property-tested) | IMPLEMENTED (Phase 8) |
| Conflict detection with explicit uncertainty + confidence degradation | IMPLEMENTED (Phase 8) |
| Verified synthesis: aggregate → verifier checks → TRUSTED/UNCERTAIN/REJECTED | IMPLEMENTED (Phase 8) |
| Team coordinator: orchestrate → aggregate → synthesize with audit ledger | IMPLEMENTED (Phase 8) |
| Reliability records: empirical per-tool/per-role statistics for routing | IMPLEMENTED (Phase 8) |
| Scheduler: ONCE/INTERVAL/DAILY/WEEKLY, one execution engine (fires into the durable task store) | IMPLEMENTED (Phase 9) |
| Self-maintenance jobs: bounded, failure-isolated, journaled | IMPLEMENTED (Phase 9) |
| Notification router: priorities, quiet hours, digests, rate limits, local sinks | IMPLEMENTED (Phase 9) |
| TUI (Textual): tasks/approvals/schedules/agents panels, command palette, keyboard-only flows | IMPLEMENTED (Phase 10) |
| TUI notification sink behind the same router policy (quiet hours/digest respected) | IMPLEMENTED (Phase 10) |
| In-app scheduler hosting (tick loop) + confirmed killswitch command | IMPLEMENTED (Phase 10) |
| Local web dashboard (Starlette, 127.0.0.1-only): tasks/approvals/schedules/agents/events panels | IMPLEMENTED (Phase 11) |
| Web CSRF-guarded mutations (new task, grant/deny, killswitch) + escaped HTML (XSS-safe) | IMPLEMENTED (Phase 11) |
| Web notification sink behind the same router policy; scheduler hosting via ASGI lifespan | IMPLEMENTED (Phase 11) |
| Telegram channel (raw Bot API over httpx, zero new deps): outbound sink behind the same router policy | IMPLEMENTED (Phase 12) |
| Telegram inbound: allow-listed commands (tasks/approvals/grant/deny/killswitch+confirm), chat allow-list, no tool execution | IMPLEMENTED (Phase 12) |
| Token via XENOPUS_TELEGRAM_TOKEN env var only — never logged, never in error text (redaction-tested) | IMPLEMENTED (Phase 12) |
| Discord channel: outbound sink (raw REST over httpx) behind the same router policy | IMPLEMENTED (Phase 13) |
| Discord inbound: py-cord gateway (ADR-024), allow-listed `!`-commands, channel + optional guild allow-lists, no tool execution | IMPLEMENTED (Phase 13) |
| Token via XENOPUS_DISCORD_TOKEN env var only — Authorization-header auth, never in URLs or logs (redaction-tested) | IMPLEMENTED (Phase 13) |
| Webhook inbound (local-first, ADR-025): HMAC-SHA256-verified machine events on the dashboard ASGI seam | IMPLEMENTED (Phase 14) |
| Webhook replay defense (timestamp window + signed nonce registry), source allow-list fail-closed, per-source rate limits | IMPLEMENTED (Phase 14) |
| Webhook event mapping: allow-listed `task.create` into the same durable TaskStore — no tool execution, no approvals, no killswitch | IMPLEMENTED (Phase 14) |
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

459 tests cover package imports, configuration, bootstrap, CLI, the
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
sinks), the durable task lifecycle (legal transitions, retry budgets, kill-switch cancel-all, crash-recovery flagging, interruption journaling), persistent approvals (replay-proof, expiry sweeps), recycle-bin deletes with restore, the CLI control surface, both external channels end-to-end against mock transports (Telegram raw Bot API, Discord raw REST + py-cord command dispatch), and the HMAC-verified webhook inbound surface (signature/replay/size/rate-limit rejections, task-create round-trip) — no live-network tests.

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
| 6 | Durable task runtime · remote control | ✅ complete |
| 7 | Orchestrator · agent pool · failure isolation | ✅ complete |
| 8 | Aggregation · synthesizer · teams · reliability | ✅ complete |
| 9 | Scheduler · notification router | ✅ complete |
| 10 | TUI (Textual) | ✅ complete |
| 11 | Local web dashboard (Starlette) | ✅ complete |
| 12 | Telegram channel | ✅ complete |
| 13 | Discord channel | ✅ complete |
| 14 | Webhooks | ✅ complete |
| 16 | Desktop shell | not started |
| 19 | First public release 1.0.0 | gated |

## Version

- Source of truth: [`pyproject.toml`](pyproject.toml) (ADR-002).
- Development snapshot: `1.0.0.dev13`.
- First public release: **1.0.0**.
- Policy: Semantic Versioning — no digit rollover at 10.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © 2026 Dearly Febriano Irwansyah (neuralforgeio)
