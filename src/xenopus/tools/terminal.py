"""Terminal tool: allow-listed, cwd-bounded subprocess execution (ADR-029).

The ONLY execution surface for commands — argv form (no shell, no
string parsing), a closed executable allow-list (node/npm/npx/git),
a workspace-bounded working directory, a hard timeout, and a merged
output cap. The child environment is rebuilt minimal: every
XENOPUS_* and secret-shaped variable is stripped so a child
process can never read runtime secrets. The gateway routes this
tool through permission -> risk (APPROVAL by the table) -> approval
like any other side effect.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from xenopus.tools.contracts import ToolResult

ALLOWED_COMMANDS = frozenset({"node", "npm", "npx", "git"})
DEFAULT_TIMEOUT_SECONDS = 600.0
MAX_OUTPUT_BYTES = 64 * 1024
TRUNCATION_MARKER = "\n...[output truncated]"
_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMFILES",
    "PROGRAMDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "HOME",
)
_ENV_PREFIXES_BLOCKED = ("XENOPUS_", "API_", "TOKEN", "SECRET", "KEY", "CREDENTIAL")


def sanitized_child_env(parent_env: dict[str, str]) -> dict[str, str]:
    """Minimal env for children: allowlist minus secret-shaped keys."""
    return {
        name: parent_env[name]
        for name in _ENV_ALLOWLIST
        if parent_env.get(name) and not name.upper().startswith(_ENV_PREFIXES_BLOCKED)
    }


def resolve_allowed_executable(command: str) -> str | None:
    """Resolve an allow-listed command to a full path (shim-aware).

    Windows ships npx as npx.cmd; shutil.which finds it, and the
    resolved stem is matched back against the allow-list so the
    allow-list stays the single source of truth.
    """
    if not command or Path(command).name != command:
        return None  # no paths, no separators: bare names only
    resolved = shutil.which(command)
    if resolved is None:
        return None
    stem = Path(resolved).stem.lower()
    if stem in ALLOWED_COMMANDS:
        return resolved
    return None


def _policy_from_args(arguments: dict[str, Any]) -> Any:
    """Extract the PathPolicy injected by the gateway."""
    policy = arguments.get("__path_policy__")
    if policy is None:
        msg = "terminal tool invoked without a path policy (internal wiring error)"
        raise ValueError(msg)
    return policy


def _relative_cwd(arguments: dict[str, Any]) -> str:
    """The caller's cwd reference ("" = workspace root)."""
    raw = arguments.get("cwd")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        msg = "cwd must be a string"
        raise ValueError(msg)
    return raw


async def terminal_run(arguments: dict[str, Any]) -> ToolResult:
    """Run one allow-listed command inside the workspace.

    Contract:
        arguments: command (allow-listed name), args (list of
        string tokens), cwd (workspace-relative, optional),
        timeout_seconds (optional, bounded).
        Returns returncode + merged output (capped) + duration.

    Failure modes:
        command_not_allowed / cwd_escapes_boundary / timeout /
        spawn_error — structured ToolResult failures, never
        exceptions.
    """
    started = time.monotonic()
    try:
        policy = _policy_from_args(arguments)
        root: Path = policy.root
        command = str(arguments.get("command", ""))
        args_raw = arguments.get("args", [])
        if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
            msg = "args must be a list of strings (argv form; no shell)"
            return ToolResult.failure("invalid_arguments", msg)
        timeout = float(arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        if not 1.0 <= timeout <= DEFAULT_TIMEOUT_SECONDS:
            msg = f"timeout_seconds must be within [1, {DEFAULT_TIMEOUT_SECONDS}]"
            return ToolResult.failure("invalid_arguments", msg)
        executable = resolve_allowed_executable(command)
        if executable is None:
            return ToolResult.failure(
                "command_not_allowed",
                f"command {command!r} is not on the allow-list {sorted(ALLOWED_COMMANDS)}",
            )
        cwd_ref = _relative_cwd(arguments)
        cwd = root if cwd_ref.strip() in (".", "") else policy.resolve(cwd_ref)
        if not cwd.is_dir():
            return ToolResult.failure("not_found", f"cwd does not exist: {cwd_ref!r}")
    except ValueError as err:
        return ToolResult.failure("invalid_arguments", str(err))

    argv = [executable, *args_raw]
    env = sanitized_child_env(dict(os.environ))
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, allow-listed executable
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult.failure(
            "timeout",
            f"command exceeded {timeout:.0f}s and was killed",
            duration_seconds=time.monotonic() - started,
        )
    except OSError as err:
        return ToolResult.failure(
            "spawn_error",
            f"failed to start {command!r}: {err}",
            duration_seconds=time.monotonic() - started,
        )

    merged = (completed.stdout or "") + (completed.stderr or "")
    truncated = len(merged.encode("utf-8", errors="replace")) > MAX_OUTPUT_BYTES
    if truncated:
        keep = merged.encode("utf-8", errors="replace")[:MAX_OUTPUT_BYTES]
        merged = keep.decode("utf-8", errors="replace") + TRUNCATION_MARKER
    ok = completed.returncode == 0
    return ToolResult(
        ok=ok,
        data={
            "command": command,
            "args": args_raw,
            "returncode": completed.returncode,
            "output": merged,
            "truncated": truncated,
        },
        error_code="" if ok else f"exit_{completed.returncode}",
        error_message="" if ok else merged[-500:],
        duration_seconds=time.monotonic() - started,
    )


TERMINAL_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "terminal_run": {
        "description": "Run one allow-listed command (node/npm/npx/git) in the workspace.",
        "side_effects": "external: runs a subprocess with network/install effects",
        "permissions": ["terminal.run"],
        "risk": 1,
        "idempotent": False,
        "failure_modes": (
            "command_not_allowed",
            "cwd_escapes_boundary",
            "not_found",
            "timeout",
            "spawn_error",
            "invalid_arguments",
            "exit_<code>",
        ),
    },
}
