# ADR-024: Discord Channel = py-cord Inbound Gateway + Raw REST Outbound over httpx

## Status
Accepted (2026-09-10, Phase 13)

## Context
Phase 13 adds the second external surface: a Discord channel,
mirroring the Telegram pattern (ADR-023) — outbound sink behind the
SAME router policy, inbound commands that never execute tools.
Library candidates were evaluated against dependency governance
(Protocol v9 §9) with PyPI metadata captured 2026-09-10:

1. discord.py 2.7.1 — REJECTED: the 2.x line is in maintenance mode
   (successor 3.x unreleased); wheel 2026-03-03; core pulls
   `aiohttp` + `audioop-lts` (a stdlib-removed shim). MIT-licensed
   and Python 3.13-capable, but py-cord is the same design lineage
   under active development.
2. py-cord 2.8.1 — ACCEPTED for INBOUND: MIT (license_expression),
   wheel 2026-07-25, `requires_python <3.15,>=3.10` (3.13
   compatible), core dependencies `aiohttp` + `typing_extensions`
   ONLY. Verified THIS session: install clean, `pip check` clean,
   import OK on 3.13.3.
3. Raw WebSocket/REST over httpx — REJECTED for inbound, ACCEPTED
   for outbound. Discord exposes NO REST polling for messages (no
   getUpdates equivalent): inbound requires the WebSocket gateway —
   identify/resume, heartbeats, session limits, opcode machinery —
   well past ADR-023's ~500-LOC raw-client reversal criterion.
   Proportionality (§9.3) therefore FAVORS a library for the
   gateway, while outbound/replies are two REST endpoints
   (POST /channels/{id}/messages, GET /users/@me) that the EXISTING
   hardened httpx seam already addresses.

## Decision
Split the channel along the transport boundary:

- **Outbound (DiscordSink + replies): raw REST over httpx.**
  `DiscordClient` posts channel messages with the token in the
  `Authorization: Bot <token>` header (never the URL — Discord
  auth is header-native), explicit timeouts, 2000-char clamp, and
  429 handling that maps `retry_after` (body, seconds float) plus
  `X-RateLimit-Remaining: 0` into ONE bounded wait (ceiling 30s),
  then surfaces `DiscordError` — the router owns retry semantics,
  never a loop here. MockTransport-testable exactly like Telegram.
- **Inbound (DiscordCommander): py-cord gateway events.** A
  `discord.Client` subclass with the `message_content` intent maps
  ONLY the allow-listed command table (`!start !tasks !task <id>
  !new <title> !approvals !grant <id> !deny <id> !killswitch
  !confirm` — `!` prefix because `/` is reserved for Discord
  slash commands, which we deliberately do not register). Inbound
  NEVER triggers tool execution; grant/deny go through the SAME
  persistent ApprovalStore; killswitch needs the explicit two-step
  confirm. The commander is engine-wired identically to Telegram's
  (shared dispatch semantics), so the two surfaces are auditable
  side by side.
- **Token + allow-list discipline**: `XENOPUS_DISCORD_TOKEN` env
  var ONLY; missing = host refuses to start (fail-fast). Channel
  ids via `XENOPUS_DISCORD_CHANNEL_IDS` (outbound sink) and
  `XENOPUS_DISCORD_GUILD_IDS` (inbound, optional guild allow-list).
  Unlisted inbound channels are silently dropped (fail-closed, no
  oracle for strangers). The token is never logged and never in
  raised error text (redaction test-pinned), same as ADR-023.

## Reversal Criteria
Revisit if (a) a no-intent REST/WS polling path appears in the
Discord API for bots, (b) the outbound REST surface we need exceeds
~500 lines, or (c) py-cord's maintenance lapses > 6 months while
discord.py 3.x ships stable — swap the inbound seam then (it is
isolated behind one module).

## Sunset Review
Phase 14 inbound-security review (together with ADR-023's and the
webhook tunnel-vs-relay decision, ADR-037 candidate).

## Consequences
### Positive
- Inbound gets a maintained, spec-tracking gateway implementation
  (heartbeats, resumes, reconnects) for ~zero protocol code.
- Outbound stays dependency-free, MockTransport-testable, and
  token-safe on the proven httpx pattern.
- Assumption #9 (Discord lib compatibility) CLOSED by direct
  install/import verification.
### Negative
- ONE new runtime dependency tree (py-cord + aiohttp +
  typing_extensions); accepted under §9 proportionality because the
  alternative is ~1000 LOC of gateway protocol we would own.
- Message-content intent must be enabled in the Discord developer
  portal (bot prerequisite, documented in README).
### Neutral
- `!` command prefix instead of Telegram's `/` (Discord reserves
  `/` for registered slash commands).

## Alternatives Considered
- discord.py 2.7.1 — rejected: maintenance-mode line; py-cord is the
  actively developed successor of the same design.
- Fully raw (WS + REST) — rejected for inbound on §9.3
  proportionality (gateway protocol weight); kept for outbound.

## References
- Master prompt addendum 5 (channels), 87 (policy parity)
- ADR-005 (httpx provider seam), ADR-020 (notification router),
  ADR-023 (Telegram channel — the template)
- Protocol v9 §9 (dependency governance), §11.2 (secrets)
- PyPI evidence (2026-09-10): discord.py 2.7.1 (MIT, wheel
  2026-03-03, aiohttp+audioop-lts); py-cord 2.8.1 (MIT, wheel
  2026-07-25, requires_python <3.15,>=3.10, aiohttp+
  typing_extensions)
- Session verification: `pip install py-cord>=2.8.1,<3` clean;
  `pip check` — "No broken requirements found."; import OK on
  3.13.3

## Sunset Review Outcome (2026-09-10, Phase 16)
Reviewed — no findings requiring change. Surfaces remain within their ADR bounds; security posture (loopback/CSRF/XSS, token redaction, allow-lists, HMAC+replay) re-verified by the current test suites (459→472 passing).

