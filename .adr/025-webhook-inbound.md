# ADR-025: Webhook Inbound = Local-First HMAC Endpoints on the ASGI Seam

## Status
Accepted (2026-09-10, Phase 14)

## Context
Phase 14 adds the third inbound surface: webhooks. The reachability
question (assumption #8: "inbound webhooks need a tunnel/relay")
was escalated to the user as a HIGH-risk decision before design, per
the assumption ledger. Decision recorded 2026-09-10:

**Local-first LAN/loopback + HMAC.** No public tunnel, no relay
service, no third-party trust. The webhook endpoints ride the
EXISTING dashboard ASGI app (ADR-022 seam), bind loopback by
default (LAN binding remains a user-run choice, same as the
dashboard), and authenticate every request with HMAC-SHA256.
External SaaS providers can still deliver webhooks to a LAN endpoint
through a tunnel the USER runs themselves — that is outside
Xenopus's scope and outside its trust boundary.

## Decision
Implement inbound webhooks as Starlette routes mounted ONLY when
per-source secrets are configured (`XENOPUS_WEBHOOK_SECRETS`, env
var; `source:secret` pairs, comma-separated). Rules:

- **Authentication — HMAC-SHA256 over the RAW request body** with
  the source's secret, presented as `Xenopus-Signature:
  sha256=<hexdigest>`. Compared with `hmac.compare_digest`
  (constant time). Stdlib only — no new dependencies. Machine
  clients cannot hold CSRF tokens; HMAC replaces CSRF for this
  surface (the operator dashboard keeps CSRF unchanged).
- **Replay protection** — two layers: (a) `Xenopus-Timestamp` must
  fall within a bounded clock-skew window (300s default, the
  industry-tolerant bound; GitHub uses tighter); (b) the event's
  nonce (id field inside the signed body) is remembered per source
  with a TTL sweep — a replayed nonce is rejected. Both layers are
  signed content: the timestamp travels inside the signed envelope
  header set and the nonce inside the body, so an attacker cannot
  refresh either without breaking the signature.
- **Source allow-list — fail-closed.** The route is
  `/webhooks/{source}`; unknown sources get 404 (existence hiding,
  no oracle). Known sources with bad signatures get 401.
- **Event mapping — allow-listed and minimal.** This phase accepts
  exactly ONE event type: `task.create` (a title, an optional goal
  ref). Events enqueue into the SAME durable TaskStore every other
  surface uses, with correlation_id `webhook:{source}:{nonce}`.
  Webhooks NEVER trigger tool execution, NEVER touch approvals,
  NEVER reach the killswitch — machine input holds a lower trust
  bar than operator commands (the command surfaces are
  human-in-the-loop by design; webhooks are not).
- **DoS bounds at the boundary.** Request body capped (64KB
  default -> 413); per-source in-memory token bucket rate limit
  (default 30/min) with journaled rejections; JSON-only bodies
  (415 otherwise); strict schema validation (400 on mismatch).
- **Observable, silently-safe.** Every inbound attempt journals an
  event (accepted or rejected with reason). Responses are status
  codes only — request content is never echoed (no reflection
  surface, no injection vector).

## Reversal Criteria
Revisit when (a) outbound webhook delivery is needed (a router-sink
ADR would follow), (b) per-source routing policies beyond the
allow-list emerge, or (c) a user formally requests first-party
tunnel integration — which would require its own ADR and an
authentication-boundary review against the local-first posture.

## Sunset Review
Phase 16 planning (desktop shell) — the inbound-surface security
review covering ADR-022/023/024/025 together.

## Consequences
### Positive
- Zero new dependencies (hmac/hashlib/json are stdlib); reuses the
  hardened ASGI app, its loopback binding, and its journal.
- Machine inputs are provably below the tool/approval trust bar.
- Assumption #8 CLOSED by explicit user decision with this ADR as
  evidence.
### Negative
- Public SaaS webhooks need a user-operated tunnel to reach a LAN
  endpoint (documented cost of the local-first posture).
- The nonce registry is in-memory (per-process): a restart forgets
  nonces; the timestamp window bounds replay exposure after a
  restart (accepted, noted).
### Neutral
- Webhook routes are absent (not merely disabled) when secrets are
  unconfigured — attack surface appears only on opt-in.

## Alternatives Considered
- Public tunnel (cloudflared/ngrok) integration — rejected by user
  decision (2026-09-10): violates local-first posture; an always-on
  tunnel process is an unattended exposure.
- Relay service (poll-and-push) — rejected by user decision: adds a
  second inbound policy surface and third-party trust.
- Raw TCP/UDP listeners — rejected: the ASGI seam already provides
  the HTTP parse/limits/lifecycle; a second server would duplicate
  ADR-022 machinery.

## References
- Master prompt addendum 5 (channels), 87 (policy parity)
- ADR-022 (web dashboard/ASGI seam — the host), ADR-008 (gateway
  invariants), ADR-023/024 (sibling inbound surfaces)
- Protocol v9 §11 (security compliance; STRIDE noted in the task
  file), §11.2 (secrets discipline — env-var-only)
- User decision log 2026-09-10: "Local-first LAN/loopback + HMAC"
  (assumption #8 closure evidence)
