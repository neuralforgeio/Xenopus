# ADR-023: Telegram Channel = Raw Bot API over the httpx Seam

## Status
Accepted (2026-09-10, Phase 12)

## Context
Phase 12 adds the FIRST external surface: a Telegram channel. The
handoff scoped it outbound-first (router deliveries -> sendMessage),
then inbound long-polling with allow-listed commands. Library
candidates were evaluated against dependency governance (Protocol v9
Section 9) with PyPI metadata captured 2026-09-10:

1. python-telegram-bot 22.8 — REJECTED: license is LGPL-3.0-only.
   A copyleft library in a permissive (MIT) project violates the §9.1
   license-compatibility rule (FORBIDDEN row). Also pins
   `httpx<0.29` (conflicts with the provider seam's upgrade path).
2. aiogram 3.31.0 — REJECTED on proportionality (§9.3): MIT-licensed
   and Python 3.13-compatible, but its core pulls aiohttp + pydantic
   + aiofiles + magic-filter — an entire second async HTTP stack and
   validation framework to call two JSON endpoints (sendMessage,
   getUpdates) that the EXISTING httpx provider seam already
   addresses (ADR-005).
3. Raw Bot API over httpx — ACCEPTED: Telegram's Bot API is a plain
   JSON/HTTPS API. We need ~150 lines: sendMessage, getMe,
   getUpdates with offset-based long polling. Zero new dependencies;
   the hardened httpx client discipline (explicit timeouts, no
   redirects to follow, error surfaces) is reused as-is.

## Decision
Implement the Telegram channel directly on the Bot API via httpx
(the provider layer's HTTP seam, ADR-005). Rules:

- **Token handling**: the bot token comes ONLY from the
  `XENOPUS_TELEGRAM_TOKEN` environment variable. It is never a
  constructor default, never logged, never stored in config files;
  the client redacts it from any error surface (last 4 hex chars at
  most, per log-redaction discipline). Missing token = the host
  command refuses to start (fail-fast, no silent no-op channel).
- **Outbound is router-governed**: TelegramSink implements the SAME
  `Sink` interface as StdoutSink/WebSink/TextualSink. The
  NotificationRouter policy (priorities, quiet hours, digests, rate
  limits) decides BEFORE delivery — the channel can never become a
  policy bypass (addendum 87). Telegram's own 429 Retry-After is
  respected inside the client (bounded wait + journaled skip), never
  by weakening router policy.
- **Inbound is command-allow-listed**: long-polling getUpdates maps
  ONLY these commands — `/start` (greeting), `/tasks`, `/task <id>`,
  `/new <title>`, `/approvals`, `/grant <id>`, `/deny <id>`,
  `/killswitch` (asks confirmation), `/confirm` (executes the
  pending killswitch confirmation). Everything else is ignored with
  a help hint. Inbound NEVER triggers tool execution and NEVER
  bypasses the approval engines (grant/deny go through the SAME
  persistent ApprovalStore the CLI/TUI/Web use).
- **Chat allow-list**: only configured chat ids may interact
  (`XENOPUS_TELEGRAM_CHAT_IDS`, comma-separated). Messages from
  other chats are dropped without response (no oracle for
  strangers; fail-closed).
- **Egress bounds**: send timeouts are explicit; a send failure is
  journaled and surfaced to the operator, never retried in a loop
  (the router already owns delivery semantics).

## Reversal Criteria
If a richer Telegram integration becomes necessary (inline keyboards,
media pipelines, webhook mode with signature verification), revisit
this ADR — aiogram becomes competitive at that feature scale, and the
license issue that disqualified PTB does not apply to it. Revisit also
if the Bot API surface we need exceeds ~500 lines of client code.

## Sunset Review
Phase 14 (webhook inbound decision — tunnel vs relay, ADR-037
candidate), together with the inbound-security review.

## Consequences
### Positive
- Zero new dependencies; assumption #9 (lib compat) is CLOSED BY
  ELIMINATION for Telegram — there is no library to be incompatible.
- Outbound delivery inherits every existing router policy test.
- Inbound surface is minimal and enumerable (audit-friendly).
### Negative
- Bot API changes (if any) are on us to follow; mitigated by pinning
  the endpoints we call and validating response shapes defensively.
### Neutral
- Discord (Phase 13) will face the same decision pattern; this ADR's
  proportionality analysis is the template.

## References
- Master prompt addendum 5 (channels), 87 (policy parity)
- ADR-005 (httpx provider seam), ADR-020 (notification router)
- Protocol v9 §9 (dependency governance), §11.2 (secrets discipline)
- PyPI evidence (2026-09-10): PTB 22.8 LGPL-3.0-only, httpx<0.29 pin;
  aiogram 3.31.0 MIT, core deps aiohttp/pydantic/aiofiles/magic-filter

## Sunset Review Outcome (2026-09-10, Phase 16)
Reviewed — no findings requiring change. Surfaces remain within their ADR bounds; security posture (loopback/CSRF/XSS, token redaction, allow-lists, HMAC+replay) re-verified by the current test suites (459→472 passing).

