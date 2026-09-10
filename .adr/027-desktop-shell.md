# ADR-027: Desktop Shell = Zero-Dependency Resident Mode (Not a Tray/Webview Library)

## Status
Accepted (2026-09-10, Phase 16)

## Context
Phase 16 adds the desktop shell: a resident local surface over the
engine bundle. Library candidates were evaluated against dependency
governance (Protocol v9 §9) with PyPI evidence captured 2026-09-10:

1. pystray 0.19.5 — REJECTED: license is LGPLv3 — the §9.1
   FORBIDDEN row (copyleft in a permissive MIT project; the same
   disqualifier that eliminated python-telegram-bot in ADR-023).
   Also unmaintained (>2 years; last wheel 2023-09-17) and pulls a
   Pillow + six + platform-X11/objc tree.
2. pywebview 6.2.1 — REJECTED on proportionality (§9.3): BSD-3
   licensed and maintained (wheel 2026-04-15), but on Windows it
   drags pythonnet (a .NET runtime bridge), bottle, proxy_tools,
   and per-platform Qt/objc frameworks — an entire embedded-browser
   stack to render HTML that the user's system browser already
   renders against the EXISTING loopback dashboard (ADR-022:
   XSS-escaped, CSRF-guarded, 24 ASGI tests).
3. Zero-dependency resident mode — ACCEPTED: `xenopus resident`
   is ONE process hosting the engines: the scheduler tick, the
   self-improvement loop on a fixed cadence (register_job, the
   addendum 39/100 contract), and — for GUI interaction — the
   EXISTING web dashboard on loopback, opened in the system
   browser via the stdlib `webbrowser` module. The "desktop app"
   experience is delivered by platform primitives, not a
   framework.

## Decision
Implement the desktop shell as a ResidentHost (runtime/resident.py)
that owns process lifecycle and NOTHING else (ADR-001 boundary):

- **Hosting**: opens the durable stores (cross-thread), builds the
  Scheduler + ReflectionLoop over them, registers the reflection
  loop as a periodic scheduler job (`register_job` — bounded,
  failure-isolated by the existing tick contract), and runs the
  tick loop until stopped. One process, one tick, no second
  scheduler.
- **Cadence**: scheduler tick every `--interval` seconds
  (default 5; bounded 1-3600). Reflection cadence is a multiple
  of the interval (default: hourly — `--reflect-every 3600s`,
  also bounded). Deterministic, operator-controlled, journaled.
- **GUI interaction**: `--web` serves the existing dashboard
  (ADR-022 seam, loopback-only) and opens the system browser
  (stdlib webbrowser). No embedded browser, no new attack
  surface: the dashboard's CSRF/XSS/loopback posture carries
  over unchanged.
- **Graceful shutdown**: SIGINT/SIGTERM/Ctrl+C set the stop flag;
  the loop finishes its current tick, closes stores exactly once,
  journals RESIDENT_STOPPED. A crash mid-tick leaves SQLite WAL
  state consistent (same recovery contract as Phases 6/14).
- **No tray icon.** The tray-icon experience is what pystray was
  for; its rejection is recorded here with the reversal criteria
  below.

## Reversal Criteria
Revisit this ADR when (a) a permissively-licensed, maintained tray
library emerges (MIT/BSD, active within 6 months) AND the user
formally wants OS-native tray presence; (b) offline-first GUI
requirements make the system browser unacceptable (then pywebview
returns under a stricter proportionality re-check); (c) the
resident process needs OS service integration (launchd/Task
Scheduler/service managers) — a separate ADR for service
packaging.

## Sunset Review
Phase 18 (performance) — the resident cadence bounds are tuning
inputs; and the Phase 16+ security reviews sweep this surface
with the ADR-022..025 family.

## Consequences
### Positive
- Zero new dependencies; the entire desktop-shell delivery is
  ~150 lines over existing, tested engines.
- No new trust/attack surface: loopback dashboard + local
  process; no embedded browser, no tray IPC.
- Reflection gets a production hosting cadence (ADR-026's
  trigger policy completes: manual CLI OR periodic resident job).
### Negative
- No OS-native tray icon or window chrome — the shell is a
  console process + browser tab; accepted cost of the governance
  decision (documented for users in README).
- Browser convenience depends on the user having a browser
  (headless servers use the plain resident mode without --web).
### Neutral
- The TUI (ADR-021) remains the interactive terminal surface;
  resident mode is the daemon-shaped complement, not a
  replacement.

## Alternatives Considered
- pystray — rejected: LGPLv3 (§9.1 FORBIDDEN) + unmaintained.
- pywebview — rejected: §9.3 proportionality (pythonnet/bottle/
  proxy_tools + platform frameworks for a browser we already have).
- Electron/Tauri-class — rejected out of scope: non-Python
  toolchains, Node/rustc build deps; violates the stdlib-first
  toolchain posture (ADR-002) for zero marginal capability.

## References
- Master prompt addendum 5 (channels/surfaces), 39/100 (bounded
  jobs), ADR-001 (boundary), ADR-002 (toolchain), ADR-021 (TUI),
  ADR-022 (dashboard), ADR-026 (reflection loop trigger)
- Protocol v9 §9 (dependency governance)
- PyPI evidence (2026-09-10): pystray 0.19.5 LGPLv3, wheel
  2023-09-17; pywebview 6.2.1 BSD-3, wheel 2026-04-15, Windows
  deps pythonnet/bottle/proxy_tools
