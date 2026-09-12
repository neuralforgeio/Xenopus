# ADR-029: Terminal Tool = Allow-Listed, Cwd-Bounded Subprocess Execution

## Status
Accepted (2026-09-11, post-1.0.0 user-directed work)

## Context
The live E2E directive — "the agent must build a Next.js website
via npx" — exposed a real capability gap: the runtime's tool set is
file I/O only; nothing can execute a build/install command. A
terminal tool is the classic agent-security quagmire (arbitrary
code execution by definition), so it enters through the SAME
governance as every tool (ADR-008/009) with additional structural
bounds.

Trigger context: the llama.cpp live test (Ornith-1.5-9B) hit VRAM
pinned-memory allocation failures (Vulkan ErrorOutOfDeviceMemory)
and degraded to ~0.5 tok/s CPU inference until the slot was
cancelled. The user directed all further live testing to the
tokenrouter endpoint (z-ai/glm-5.3-free) — recorded in the task
file; this ADR covers the terminal tool that the E2E requires.

## Decision
Add `terminal_run` to the built-in tool set with these bounds:

- **Argv form, no shell.** The caller supplies a LIST of argument
  tokens; the handler uses `subprocess` with `shell=False`. There
  is no string-parsing path, so no shell-injection surface exists
  by construction.
- **Executable allow-list (closed set).** Only these program
  names resolve: `node`, `npx`, `npm`, `git`, plus their Windows
  resolution forms (`npx.cmd` etc., resolved via shutil.which and
  matched back to the allow-list). Anything else fails with
  `command_not_allowed` — deny-by-default, like the permission
  engine.
- **Cwd containment.** The working directory is resolved against
  the injected workspace root (the same PathPolicy object the file
  tools use); a cwd that escapes the root is refused. The child
  process runs with that bounded cwd.
- **Hard timeout.** Default 600s (npx installs are slow); on
  expiry the process is killed and the result is `timeout`
  (permanent for that invocation).
- **Output cap.** Combined stdout+stderr is capped at 64KB; when
  truncated, a `[truncated]` marker is appended. ToolResult data
  carries returncode, duration, and the (possibly truncated)
  output — journal-safe sizes.
- **Environment hygiene.** The child env inherits a minimal set
  (PATH, SYSTEMROOT, TEMP, COMSPEC, PATHEXT, USERPROFILE, APPDATA,
  LOCALAPPDATA, PROGRAMFILES, HOMEDRIVE, HOMEPATH) — enough for
  node/npm/git to function on Windows/POSIX. ALL `XENOPUS_*` and
  secret-shaped variables are stripped: a child process can never
  read webhook secrets or provider keys.
- **Gateway classification.** required_permissions:
  `terminal.run`; side_effects declare `external: runs a
  subprocess`; risk = MEDIUM; the gateway's dynamic factors set
  external_side_effects=True, blast_radius=MEDIUM, reversible=
  True, destructive=False -> risk score 3+1+MEDIUM(1)+declared(1)
  = 5 >= APPROVAL_THRESHOLD(4) -> **APPROVAL required**. The E2E
  harness grants through the REAL approval ledger (create ->
  grant -> resolve is hash-bound to the exact argv), proving the
  human-in-the-loop path works for terminal actions.
- **No TTY, no interactivity.** stdin is closed; commands that
  require interactive prompts fail fast (their problem, reported
  honestly). npm/npx non-interactive flags (`--yes`, `--no-fund`)
  are the caller's responsibility.

## Reversal Criteria
Revisit when (a) a wider command set is genuinely needed — then
the allow-list becomes data (config-driven) with its own ADR;
(b) long-running dev servers need session management (background
process handles) — a separate tool with lifecycle contracts;
(c) a real shell-with-parsing is requested — it must NOT be added
without a dedicated security review (the injection class this
design exists to prevent).

## Sunset Review
Post-1.0.0 hardening cycle: first review after real E2E usage
data accumulates (failure rates, timeout rates, truncation
frequency feed the reliability store automatically).

## Consequences
### Positive
- The agent can genuinely build software (npm/npx/git/node), which
  is the point of an agent runtime — through the same governance
  pipeline as every other side effect.
- Structural injection defense (argv-only + allow-list + env
  strip) rather than pattern-matching filters.
### Negative
- Allow-list friction: any tooling outside the set needs a code
  change (accepted; that is the gate working).
- Windows resolution quirks (.cmd shims) live in the allow-list
  matcher (tested).
### Neutral
- The E2E harness becomes the reference implementation for
  granting APPROVAL-path tool calls programmatically under
  supervision.

## Alternatives Considered
- Generic `shell(command: str)` — REJECTED: string parsing is the
  injection class; no pattern filter is complete.
- Docker/sandbox isolation — REJECTED for now: adds a dependency +
  platform surface disproportionate to a local-first runtime that
  ALREADY has permission/risk/approval governance (§9
  proportionality).
- Denying terminal entirely — REJECTED: an agent runtime that
  cannot run builds cannot verify its own work; the verifier
  story (P1: evidence-based) needs execution.

## References
- ADR-008 (tool gateway), ADR-009 (permission/risk), ADR-002
  (dependency restraint — zero new deps: subprocess/shutil stdlib)
- Protocol v9 §4.2 (injection classes), §11.4 STRIDE (noted in the
  task file)
- User directive 2026-09-11 (E2E via tokenrouter; terminal required)
