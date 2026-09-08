# ADR-020: Notification Router — Policy-Gated Local Delivery

## Status
Accepted

## Context
The runtime must notify without spamming (addendum 41-42, 96-99):
priorities, quiet hours, digests, rate limits. Channels arrive in
Phase 12+; the ROUTING POLICY must exist first so channels only add
sinks, not brains.

## Decision
1. Event -> Priority mapping is a data table (default SILENT for
   unmapped events — the router is never noisy by accident).
   CRITICAL: task failures, approval requests, kill switch. NORMAL:
   completions, verifications, schedules.
2. Per-subscriber policy: rate limit (per-minute window), quiet hours
   (supports midnight-crossing windows), digest preference.
3. Rules:
   - SILENT never delivers.
   - CRITICAL bypasses quiet hours and digests (always immediate).
   - NORMAL in quiet hours: suppressed (or held for digest when the
     subscriber prefers digests).
   - Rate limit overruns are SUPPRESSED, not queued (anti-spam wins,
     addendum 42); journal evidence records every outcome.
4. Digest flush collapses held notifications into one summary line
   ("digest: 3 held: first; second; third...").
5. Sinks are local-only in Phase 9 (stdout for CLI/TUI-local, journal
   evidence for both delivered AND suppressed). The Sink interface is
   the Phase 12 channel-adapter seam.
6. Events v6: NOTIFICATION_DELIVERED / NOTIFICATION_SUPPRESSED
   journaled with subscriber, priority, and reason.

## Reversal Criteria
If Phase 11 adds per-event preferences (users muting one event
class), extend SubscriberPolicy additively — the priority table and
anti-spam invariants stay.

## Sunset Review
Phase 12 (channel sinks subscribe via the same interface), Phase 11
(preferences UI), Phase 18 (router throughput under event storms).

## Consequences
### Positive
- Policy is data: auditable table, testable matrix without mocks.
- Every suppression has journal evidence — "why wasn't I notified"
  is answerable from data.
### Negative
- Rate limiting is per-router-instance (in-memory window); a
  multi-process daemon would need a shared store (documented; single
  process is the current deployment shape).
### Neutral
- Observation-based content routing (addendum 101 artifact delivery)
  arrives with channels.

## Alternatives Considered
- Always-deliver + client-side filtering — rejected: client cannot
  filter what was never sent.
- Queuing overruns — rejected: queues turn spam into delayed spam.

## References
- Addendum 41-42, 96-99, 101; Protocol v9 4.5 (alert fatigue).
