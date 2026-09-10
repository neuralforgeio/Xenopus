# Changelog

All significant changes to Xenopus are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org).

The first public release of Xenopus will be **1.0.0**. During development,
internal versions take the form `1.0.0.devN` (development snapshots, no
public tag/release).

## [Unreleased — 1.0.0.dev13]

### Added
- Webhook inbound surface (ADR-025, local-first): the third inbound
  channel, mounted on the EXISTING dashboard ASGI app. Reachability
  decided by the user: LAN/loopback + HMAC — NO public tunnel, NO
  relay service, NO third-party trust (closes the tunnel-vs-relay
  question). External SaaS providers can still deliver via a
  user-operated tunnel to a LAN endpoint — outside Xenopus's trust
  boundary by design.
- Authentication: HMAC-SHA256 over the raw request body
  (`Xenopus-Signature: sha256=<hexdigest>`), constant-time compare,
  stdlib only — zero new dependencies. Machine clients cannot hold
  CSRF tokens; HMAC replaces CSRF for this surface (the operator
  dashboard keeps CSRF unchanged).
- Replay defense, two signed layers: `Xenopus-Timestamp` bounded to
  a 300s clock-skew window, plus a per-source nonce registry with
  TTL sweep (409 on replay). A restart forgets nonces; the
  timestamp window bounds post-restart exposure (documented).
- Fail-closed boundaries: routes mount ONLY when
  XENOPUS_WEBHOOK_SECRETS configures sources (`source:secret`
  pairs); unknown sources get 404 (no existence oracle); bodies
  capped at 64KB (413); JSON-only (415); per-source rate limit
  30/min with journaled rejections (429); responses are status
  codes only — request content is never echoed.
- Event mapping: exactly ONE allow-listed event type,
  `task.create`, enqueued into the SAME durable TaskStore with
  correlation_id `webhook:{source}:{nonce}`. Webhooks NEVER execute
  tools, NEVER touch approvals, NEVER reach the killswitch —
  machine input holds a lower trust bar than operator commands.
- 20 ASGI integration tests: valid-signature round-trip, forged/
  missing/wrong-scheme signatures, nonce replay, stale/future/
  missing timestamps, oversize/non-JSON/disallowed-type/missing-
  field rejections, rate-limit trip, routes-absent-without-secrets,
  both journal outcomes, env loader parsing.

## [Unreleased — 1.0.0.dev12]

### Added
- Discord channel (ADR-024): the second external surface. OUTBOUND
  stays raw REST over the EXISTING httpx seam — the token travels
  only in the `Authorization: Bot <token>` header (never the URL),
  with explicit timeouts, a 2000-char clamp, and Discord 429s mapped
  into one bounded wait (retry_after, ceiling 30s) before surfacing
  — the router owns retry semantics, never a loop.
- Dependency: py-cord 2.8.1 (MIT; aiohttp + typing_extensions tree)
  for the INBOUND WebSocket gateway only — Discord exposes no REST
  polling for messages, and a raw gateway implementation (identify/
  resume, heartbeats, session limits) would blow past the ADR-023
  ~500-LOC raw-client threshold. discord.py 2.7.1 rejected:
  maintenance-mode 2.x line; py-cord is the actively developed
  successor. Verified on Python 3.13.3: install, import, pip check.
- Inbound (`xenopus discord` host, or `--no-listen` for
  outbound-only): py-cord message events dispatch through the SAME
  allow-listed command table as Telegram (`!start !tasks !task
  !new !approvals !grant !deny !killswitch` + two-step `!confirm`;
  `!` because Discord reserves `/` for registered slash commands,
  which we deliberately do not register). Unlisted channels are
  silently dropped (fail-closed); an optional guild allow-list
  (XENOPUS_DISCORD_GUILD_IDS) narrows further. Inbound NEVER
  executes tools; grant/deny go through the SAME persistent
  ApprovalStore; killswitch needs the explicit `!confirm` step.
- Outbound: DiscordSink implements the SAME Sink interface; the
  SAME NotificationRouter policy (priorities, quiet hours, digests,
  rate limits) governs delivery (addendum 87 parity).
- Token discipline: XENOPUS_DISCORD_TOKEN env var ONLY (missing =
  host refuses to start — fail-fast, live-verified exit 1). The
  token never appears in raised errors (redaction test-pinned) and
  never in the journal.
- 24 MockTransport/unit tests: send/clamp/429-bounded-wait/api-error
  paths, token header-auth + never-in-URL, sink multi-channel
  delivery + router digest parity, every command's dispatch
  round-trip through the real engines, channel + guild allow-list
  enforcement, killswitch two-step, responder-failure journaling.

### Changed
- README stale rows repaired: roadmap (phases 10-11 previously
  listed as "not started"), testing count, and version snapshot.

## [Unreleased — 1.0.0.dev11]

### Added
- Telegram channel (ADR-023): the first external surface, built on
  the raw Bot API over the EXISTING httpx seam — ZERO new
  dependencies. python-telegram-bot 22.8 rejected (LGPL-3.0-only +
  httpx<0.29 pin); aiogram 3.31 rejected on §9 proportionality (an
  aiohttp+pydantic stack for two JSON endpoints).
- Outbound: TelegramSink implements the SAME Sink interface; the
  SAME NotificationRouter policy (priorities, quiet hours, digests,
  rate limits) governs delivery — the channel can never bypass
  policy (addendum 87). Telegram 429s are honored with a bounded
  single wait (Retry-After, ceiling 30s), then surfaced — no retry
  loops that would fight the router.
- Inbound (`xenopus telegram` host, or `--no-poll` for
  outbound-only): long-polling getUpdates with an allow-listed
  command table — /start /tasks /task /new /approvals /grant /deny
  /killswitch + two-step /confirm. Unlisted chats are silently
  dropped (fail-closed, no oracle). Inbound NEVER executes tools;
  grant/deny go through the SAME persistent ApprovalStore as every
  other surface; killswitch needs the explicit /confirm step.
- Token discipline: XENOPUS_TELEGRAM_TOKEN env var ONLY (missing =
  host refuses to start — fail-fast). The token never appears in
  raised errors (redaction test-pinned) and never in the journal.
- 21 MockTransport tests: send/clamp/429/not-ok paths, sink
  multi-chat delivery + router digest parity, every command's
  dispatch round-trip through the real engines, chat allow-list
  enforcement, offset advancement, poll-error journal-and-continue.

## [Unreleased — 1.0.0.dev10]

### Added
- Local web dashboard (ADR-022, Starlette 1.6 + Uvicorn): served on
  127.0.0.1 ONLY via `xenopus web [--port]`. Server-rendered HTML
  panels mirroring the TUI — tasks (list/inspect/new), pending
  approvals (grant/deny), schedules, agent health, and a recent
  events feed — all over the EXISTING engine bundle; the web layer
  adds no runtime logic (ADR-001 boundary).
- Security posture (ADR-022): every mutating POST requires a per-boot
  CSRF token (constant-time compare; missing/invalid -> 403); GET is
  always safe (no state changes); all engine strings are
  `html.escape`d at every interpolation (task titles are untrusted
  content — XSS-tested); responses carry nosniff/no-store/no-referrer.
- Web notification sink: bounded newest-first feed of
  router-APPROVED notifications — the SAME NotificationRouter policy
  (quiet hours, digests, rate limits) governs web delivery (addendum
  87); the web UI is never a policy bypass.
- Scheduler hosting via the ASGI lifespan (same duty as the TUI
  refresh interval); a tick failure is journaled, never fatal.
- Engines gained an opt-in `cross_thread=True` constructor flag on
  EventJournal/TaskStore/ApprovalStore (sqlite thread-pin relaxation
  for server surfaces; default unchanged for CLI/TUI).
- Dependency additions (all permissive): starlette, uvicorn,
  python-multipart (runtime); httpx2 (dev — Starlette TestClient
  transport).
- 24 ASGI integration tests: panels, XSS escape, CSRF rejection on
  every mutation, grant/deny/double-grant flows, killswitch
  confirmed + aborted, lifespan tick firing, sink routing parity.
  Live-loopback smoke test verified end-to-end (create via form, bad
  token 403).

## [Unreleased — 1.0.0.dev9]

### Added
- TUI (ADR-021, Textual 8.2.8): the first interactive surface —
  four panels (tasks, approvals, schedules, agent health) rendered
  from the EXISTING engines (durable TaskStore, persistent
  ApprovalStore, Scheduler, AgentPool watchdog states); the TUI adds
  no runtime logic of its own (ADR-001 boundary).
- Keyboard-only control flows: new task (F2), refresh (F3),
  confirmed killswitch (F4), digest flush (F5); row selection opens
  task inspect, approval grant/deny, schedule detail, and agent
  health overlays — the full path start → create → inspect →
  approve → killswitch is drivable via keys alone (pilot-tested).
- Command palette (ctrl+p) with Xenopus commands (new task, refresh,
  killswitch, digest, panel focus) alongside the system commands.
- Textual notification sink: renders router-approved notifications
  as toasts. The SAME NotificationRouter policy governs TUI delivery
  (priorities, quiet hours, digests, rate limits — addendum 87); the
  sink is a delivery surface, never a policy bypass. Notifications
  render with markup disabled (task titles are untrusted content).
- In-app scheduler hosting: the app's refresh interval ticks the
  scheduler (fires due entries into the shared TaskStore — one
  execution engine); a tick failure surfaces a toast and never kills
  the UI. `Scheduler.entries()/get()` and `AgentPool.live_handles`
  added as read-only observation accessors for hosting UIs.
- ADR-018 numbering collision resolved: the TUI ADR moved from 018
  to 021 (reliability records own 018); cross-references updated.
- Textual enters as a runtime dependency (verified: Python 3.13.3
  install clean, pip check clean; Phase 10 benchmark — ~74 ms full
  refresh at 200 rows vs 2 s cadence — reversal criteria not
  triggered, framework retained).

## [Unreleased — 1.0.0.dev8]

### Added
- Scheduler (ADR-019): four schedule kinds (ONCE, INTERVAL, DAILY,
  WEEKLY) with deterministic UTC-aware due-time math — no cron-parser
  dependency. Firing enqueues into the SAME durable TaskStore (one
  execution engine: the scheduler owns WHEN, the task runtime owns
  execution). INTERVAL entries fire the latest fully-elapsed slot —
  long-overdue entries fire once, never in bursts; the per-entry
  cooldown (>= 1s) paces backlogs. Bounds: per-tick execution budget
  (default 25) and per-entry max_executions retirement. A reference
  daemon loop (run_loop) is provided for Phase 10/11 hosts; everything
  is testable via tick() with an injected clock.
- Self-maintenance jobs (addendum 39): registered jobs run after fires
  each tick, must be idempotent, and a failing job never breaks the
  tick — errors are journaled, never swallowed.
- Notification router (ADR-020): event-to-priority data table
  (unmapped events default SILENT — never noisy by accident);
  per-subscriber policies with rate limits (suppressed, not queued —
  anti-spam), quiet hours (midnight-crossing windows; CRITICAL always
  exempt), and digest holding with summary flush; local sinks only
  (stdout + journal) — the Sink interface is the Phase 12 channel seam.
  Both deliveries AND suppressions are journaled with reasons.
- Event vocabulary extended additively to v6: SCHEDULE_FIRED,
  NOTIFICATION_DELIVERED, NOTIFICATION_SUPPRESSED.

## [Unreleased — 1.0.0.dev7]

### Added
- Result aggregator (ADR-017): claim extraction from agent artifacts;
  deterministic evidence-first ranking (evidence count > confidence >
  id — a high-confidence unevidenced claim never outranks a
  lower-confidence evidenced one; anti-voting invariant is
  property-tested); content deduplication; conflict resolution by
  evidence dominance (SUPERSEDED) with irreducible disagreements kept
  visible as CONFLICT — never merged, never voted; confidence degrades
  0.25 per unresolved conflict.
- Verified synthesis (ADR-017): parallel results are never shipped
  directly — the pipeline is aggregate -> verifier checks -> verdict.
  Conflicts and missing evidence degrade to an UNCERTAIN verdict with
  named uncertainties (explicit doubt, addendum 13/63); REJECTED is
  reserved for actively failing evidence; TRUSTED requires zero
  uncertainties and is enforced as a constructor invariant.
- Team coordination (addendum 16): TeamRecord (identity, roles,
  goal/plan binding) and TeamCoordinator composing orchestrator ->
  aggregator -> synthesizer with a run ledger for audit — composition
  only, no new execution engine.
- Reliability records (ADR-018): SQLite statistics of every real tool
  invocation (via the executor) and agent run (via the orchestrator) —
  success/failure/latency per tool and per role, with a deterministic
  success-rate-minus-slowness rank for future routing policies; the
  evidence base for Phase 12 learned routing, recorded under existing
  governance (recording is safe; acting on it is gated).

## [Unreleased — 1.0.0.dev6]

### Added
- Agent model (ADR-016): profiles with tool bounds, per-agent permission
  subjects, budgets, and depth/children limits (overlapping
  allowed/forbidden tool sets rejected); structured AgentContracts
  (mission, task, inputs, success criteria — never raw prompts) and
  AgentResults (status, artifacts, evidence, confidence — never raw
  text; COMPLETED/FAILED/TIMEOUT factories).
- Agent pool: admission control via a concurrency semaphore
  (MAX_CONCURRENT_AGENTS=4 default) plus session limits (depth 2,
  children 4, total 12) enforced at acquire with typed errors;
  heartbeat tracking and a stuck-detection watchdog (HEALTHY -> STUCK
  -> RECOVERING) that never kills silently.
- Orchestrator: deterministic Kahn-layer DAG execution; independent
  nodes run concurrently per layer; one agent's failure, crash, or
  timeout never cancels its siblings; downstream nodes with unmet
  dependencies are marked failed with a dependency-loss error instead
  of running on missing evidence; reports aggregate to COMPLETED /
  PARTIAL (confidence = coverage) / FAILED with explicit
  incomplete-evidence task lists.
- Pre-parallelization cost gate: deterministic overhead/benefit model
  that refuses all-fanout-1 plans and plans whose coordination
  overhead dominates the parallel benefit (callers may force
  sequential execution).
- Tool quarantine (addendum 120): quarantined tools remain registered
  for audit but fail closed through the gateway with a structured
  `tool_quarantined` error; lifting requires an explicit call; the
  executor never retries quarantined calls.
- Journal property test stabilized (hypothesis deadline removed,
  bounded examples) — eliminating a recurring suite-level flake.

## [Unreleased — 1.0.0.dev5]

### Added
- Durable task runtime (ADR-014): persisted task lifecycle
  (QUEUED/RUNNING/WAITING/PAUSED/RESUMABLE/COMPLETED/FAILED/CANCELLED)
  behind a legal-transition table (FAILED re-enters only via retry with
  attempt bumps and budget checks; terminal states are closed), with
  every transition journaled as TASK_* events under the task's
  correlation id.
- Remote-control CLI: `xenopus task create/list/inspect/start/pause/
  resume/cancel/retry/recover` — a thin control surface over the
  durable store (TUI/Web consume the same store in later phases).
- Kill switch: `xenopus killswitch` cancels every live task and
  journals KILLSWITCH_TRIGGERED (reason + affected ids) — state is
  preserved for post-incident review, never deleted.
- Crash recovery: startup pass flags RUNNING/WAITING tasks as RESUMABLE
  with TASK_INTERRUPTED journal entries — recovery surfaces, it never
  silently restarts work.
- Persistent approval store (ADR-015): SQLite ledger with hash-bound
  (tool + canonical arguments + subject + task id), expiring,
  single-grant, replay-proof approvals; expiry sweep included. Approvals
  now survive restarts mid-flow.
- Recycle bin (ADR-015): workspace-local reversible deletes with origin
  records, restore (overwrite-refusing), listing, and manual purge.
  Per the ADR-008 reversal criteria, file_delete now routes APPROVAL
  when a recycle bin is attached (destructive but reversible) and stays
  DENY without one.
- Event vocabulary extended additively to v5: TASK_INTERRUPTED,
  KILLSWITCH_TRIGGERED.

## [Unreleased — 1.0.0.dev4]

### Added
- Memory layer (ADR-011): 7-status lifecycle starting at CANDIDATE
  (auto-trust structurally impossible), evidence-gated promotion
  (evidence must reference an observation or journal correlation —
  bare assertions are rejected at construction), scope isolation by
  construction (GLOBAL/USER/WORKSPACE/PROJECT/TASK/TEMPORARY with
  mandatory scoped queries), supersession where replacements always
  re-enter as CANDIDATE, TTL expiry with injectable clock, reuse/
  correction counters, and computed (never stored) quality scores;
  persisted in a scoped SQLite store.
- Skills layer (ADR-012): skill schemas (trigger, procedure,
  constraints, failure modes, success criteria), the 7-stage lifecycle
  with a legal-transition table, deterministic trust gates (>= 3
  evaluations at >= 0.75 lifetime success for TRUSTED; >= 0.50 for
  EXPERIMENTAL), post-trust regression detection (>= 2 post-trust
  evaluations at <= 0.40 success rate triggers DEGRADED — judged on
  behavior since trust, not lifetime averages), append-only evaluation
  history, and a SQLite registry with duplicate-name protection.
- Checkpoint manager (ADR-013): append-only per-correlation snapshots
  (JSON-validated) with chronological listing and latest-anchor
  queries — the durable substrate for Phase 6 crash recovery.
- Structured logging (ADR-013): JSON-line records (UTC timestamp,
  level, service, correlation id, message + extras) with mandatory
  pre-sink secret redaction (token/key/PEM/key-value patterns and
  sensitive-keyed dict values), level filtering, and never-raise sink
  degradation.
- Event vocabulary extended additively to v4: MEMORY_CREATED/PROMOTED/
  DEGRADED/SUPERSEDED, SKILL_CREATED/PROMOTED/DEGRADED.
- Private development control plane: engineering governance state
  (task/assumption/checkpoint/agent logs, WORKLOG) moved out of the
  public repository into the local-only `.plans/` directory (ignored
  and untracked by Git; verified via git check-ignore and
  git ls-files).

## [Unreleased — 1.0.0.dev3]

### Added
- Tool gateway (ADR-008): the single enforcement pipeline — permission ->
  risk -> approval -> dispatch -> observe. Unregistered tools fail closed;
  tool crashes convert to structured `tool_crash` results; no exception
  crosses the gateway boundary.
- Tool contracts: every tool declares name, schema, side effects,
  required permissions, risk level, timeout, idempotency, and failure
  modes; the registry refuses under-specified registrations.
- Lazy tool registry with metadata-only search (permission/risk filters)
  — planners consult catalogs without loading handlers.
- File tools (read/write/list/delete) with a `PathPolicy` workspace
  boundary: every path resolves inside the root; traversal attempts
  (`../`, deep escapes) are rejected. The policy is gateway-injected
  internal wiring, never derived from tool arguments.
- Permission engine (ADR-009): ordered rules, first-match-wins, absolute
  deny-by-default fallback (empty rule set denies everything — property
  tested); ALLOW/ASK/DENY across subject/action/resource/tool dimensions.
- Risk engine: deterministic weight table (destructiveness, external
  effects, reversibility, sensitivity, blast radius) mapping to
  AUTO/APPROVAL/DENY with an absolute row — destructive AND irreversible
  is always DENY (property tested).
- Approval requests (ADR-009): hash-bound to the exact action (tool +
  canonical argument JSON + subject + correlation), expiring (default
  5 minutes), single-grant semantics; resolve() rejects replay against
  different actions, expired grants, and wrong subjects.
- Executor (ADR-010): bounded retries with backoff; permanent error
  codes never retry; every attempt journals TOOL_STARTED/COMPLETED/FAILED
  and records an observation; gateway dispatch runs off the event loop.
- Observer: raw evidence capture (tool, attempt, ok, error, data) —
  observation is data, never interpretation.
- Verifier: declarative checks evaluated over observations with
  PASS/FAIL/UNCERTAIN where UNCERTAIN is never PASS; worst-first
  aggregation; adversarial rechecks scan all attempts for hidden
  failures behind retries.
- Event vocabulary extended additively to v3: TOOL_REQUESTED,
  TOOL_STARTED, TOOL_COMPLETED, TOOL_FAILED, TOOL_DENIED.

## [Unreleased — 1.0.0.dev2]

### Added
- Provider layer (ADR-005): `ModelProvider` protocol as the single seam
  between runtime and models; offline `EchoProvider` (local-first
  default — zero keys, zero network); hardened OpenAI-compatible HTTP
  adapter for any /chat/completions endpoint with mandatory timeouts,
  injected-only API keys (never read from the environment inside the
  adapter), and strict response validation (401/403 -> permanent
  AuthError, 429 -> RateLimitError honoring Retry-After, 5xx/network ->
  ProviderUnavailableError, malformed 200 -> SchemaError).
- Provider capability registry (explicit registration, no ambient
  discovery) and `ModelCapability` descriptors with cost/context/tags.
- Circuit-breaker health tracking (healthy / rate-limited / unavailable /
  cooldown) with injected monotonic clock, Retry-After support, and lazy
  self-healing cooldown expiry.
- Deterministic model router: quality/cost/latency/local/privacy-first
  policies over health-filtered candidates; same inputs always produce
  the same order.
- Context engine (ADR-007): budgeted assembly where protected segments
  (system, goal, constraints) are never dropped — exceeding budget with
  protected content fails loudly; droppable history fills newest-first;
  `wrap_untrusted` content boundaries for tool/web output.
- Session store (ADR-006): persistent SQLite sessions with fork lineage
  (forks copy parent turns), title search, immutable archived sessions,
  and per-turn usage accounting in integer micro-USD (no float money).
- Event vocabulary extended additively to v2: SESSION_CREATED,
  SESSION_FORKED, SESSION_ARCHIVED.
- First runtime dependency: httpx 0.28.1 (BSD-3-Clause), governed per
  Protocol v9 Section 9. All provider HTTP tests run on MockTransport —
  the suite never touches the network.

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
