# ADR-022: Local Web Dashboard = Starlette + Uvicorn (localhost-only)

## Status
Accepted (2026-09-10, Phase 11)

## Context
Phase 11 requires the first browser surface: a local web dashboard
mirroring the TUI panels (tasks, approvals, schedules, agent health)
over the SAME engine bundle — no new runtime logic (ADR-001 boundary).
Candidates evaluated against dependency governance (Protocol v9
Section 9) and the local-first posture:

1. stdlib `http.server` + hand-rolled routing — rejected: no async
   scheduler hosting, no ASGI test seam, hand-rolled routing is an
   attack-surface liability (Protocol 11.5).
2. FastAPI — rejected: large transitive tree (pydantic-core, typer,
   starlette) for zero benefit over raw Starlette on a server-rendered
   local dashboard; fails the 9.3 proportionality test.
3. Flask — rejected: sync WSGI cannot host the async scheduler tick
   loop without a second thread; more surface than needed.
4. Starlette (1.6.0, BSD-3) + Uvicorn (0.52.4, BSD-3) — accepted:
   minimal async ASGI stack, lifespan hosting for the scheduler tick,
   `TestClient` gives in-process ASGI integration tests with no port
   binding, and routing/redirects/forms are library-owned.

## Decision
Starlette + Uvicorn serve the dashboard. The server:

- Binds `127.0.0.1` ONLY (hard default; the host parameter exists
  for tests but production entry always passes loopback). The runtime
  is single-user local-first; exposing it to the LAN is a Phase 15+
  decision requiring its own ADR (auth boundary).
- Renders server-side HTML from stdlib `html.escape`d engine data —
  no client JS framework, no template engine dependency; task titles
  are UNTRUSTED content and are escaped at every interpolation
  (XSS defense at the only trust boundary).
- Hosts the scheduler tick loop in the ASGI lifespan (same duty the
  TUI refresh interval performs); tick failures are surfaced, never
  fatal.
- Mutations (approval grant/deny, killswitch) require an explicit
  confirmation step mirroring the TUI ConfirmScreen: a POST form on a
  GET-rendered confirm page; no state change happens on GET (safe
  method discipline, OWASP A01/A04).
- CSRF defense: every mutating POST must carry the per-boot token
  issued in the confirm page form; token mismatch -> 403. Combined
  with localhost-only binding this closes the drive-by mutation
  vector from other origins.
- A web notification sink renders router-approved notifications into
  an in-memory recent-events list (bounded); the SAME
  NotificationRouter policy (quiet hours, digests, rate limits)
  governs delivery — the web UI is never a policy bypass (addendum
  87, same rule as the TUI sink).
- No secrets, no PII reflection: engine records only; error paths
  render generic messages.

## Reversal Criteria
If the dashboard needs client-side interactivity beyond forms (live
streaming panels), revisit with an ADR covering SSE/websockets and
the binding posture. If a LAN-exposed mode is ever requested, that
requires a new ADR with an authentication boundary first.

## Sunset Review
End of Phase 15 (before any remote-channel work), together with the
binding/auth review.

## Consequences
### Positive
- Async scheduler hosting reuses Phase 9/10 patterns (lifespan duty
  == TUI refresh duty); in-process ASGI tests run without ports.
- Two runtime deps (starlette, uvicorn) + one form-parsing dep
  (python-multipart), all permissive; no template language.
### Negative
- Server-rendered HTML means full-page refresh for updates; accepted
  for a local control surface (a meta-refresh keeps panels live).
### Neutral
- httpx2 enters the DEV group only (Starlette TestClient dep); the
  runtime dependency set stays starlette+uvicorn+python-multipart.

## Alternatives Considered
See Context items 1-3. FastAPI proportionality rejection recorded
for future channel phases (Phase 12-14 will revisit if a public API
surface appears — that is a different product surface with different
threats).

## References
- Master prompt Section 79 (web dashboard, local-only)
- ADR-001 (surfaces consume engines), ADR-021 (TUI precedent)
- Protocol v9 Section 9 (dependency governance), 11.1 (OWASP gate)
