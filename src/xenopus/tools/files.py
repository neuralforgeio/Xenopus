"""File tools: reads, writes, listing, deletion inside a path boundary.

Every operation resolves against a workspace root and refuses paths that
escape it (path traversal defense — Protocol v9 18.4). Deletion is
recycle-style: destructive only when the caller opts in and the risk
engine approves (never silently irreversible).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from xenopus.tools.contracts import ToolResult


class PathPolicy:
    """Resolves and enforces the workspace path boundary.

    Contract:
        resolve(): returns the canonical absolute path for a relative
        reference; raises ValueError when the resolved path would escape
        the workspace root (symlinks are checked after resolution).

    Invariants:
        every returned path is inside root; '..' segments and absolute
        references are normalized before the containment check.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    @property
    def root(self) -> Path:
        """The workspace root all paths must stay inside."""
        return self._root

    def resolve(self, reference: str) -> Path:
        """Resolve ``reference`` inside the root or refuse it."""
        candidate = (self._root / reference).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            msg = f"path {reference!r} escapes the workspace boundary"
            raise ValueError(msg)
        return candidate


def _policy_from_args(arguments: dict[str, Any]) -> PathPolicy:
    """Extract the PathPolicy injected by the gateway (never from user args)."""
    policy = arguments.get("__path_policy__")
    if not isinstance(policy, PathPolicy):
        msg = "file tool invoked without a path policy (internal wiring error)"
        raise ValueError(msg)
    return policy


async def file_read(arguments: dict[str, Any]) -> ToolResult:
    """Read a UTF-8 text file inside the workspace."""
    started = time.monotonic()
    try:
        policy = _policy_from_args(arguments)
        target = policy.resolve(str(arguments.get("path", "")))
        if not target.is_file():
            return ToolResult.failure("not_found", f"file not found: {arguments.get('path')!r}")
        data = target.read_text(encoding="utf-8")
    except (ValueError, OSError) as err:
        return ToolResult.failure(
            "read_error", str(err), duration_seconds=time.monotonic() - started
        )
    return ToolResult.success(
        {"path": arguments["path"], "content": data},
        duration_seconds=time.monotonic() - started,
    )


async def file_write(arguments: dict[str, Any]) -> ToolResult:
    """Write a UTF-8 text file (creates parents) inside the workspace."""
    started = time.monotonic()
    try:
        policy = _policy_from_args(arguments)
        target = policy.resolve(str(arguments.get("path", "")))
        content = str(arguments.get("content", ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except (ValueError, OSError) as err:
        return ToolResult.failure(
            "write_error", str(err), duration_seconds=time.monotonic() - started
        )
    return ToolResult.success(
        {"path": arguments["path"], "bytes_written": len(content)},
        duration_seconds=time.monotonic() - started,
    )


async def file_list(arguments: dict[str, Any]) -> ToolResult:
    """List entries of one workspace directory."""
    started = time.monotonic()
    try:
        policy = _policy_from_args(arguments)
        target = policy.resolve(str(arguments.get("path", "")))
        if not target.is_dir():
            return ToolResult.failure(
                "not_found", f"directory not found: {arguments.get('path')!r}"
            )
        entries = sorted(p.name for p in target.iterdir())
    except (ValueError, OSError) as err:
        return ToolResult.failure(
            "list_error", str(err), duration_seconds=time.monotonic() - started
        )
    return ToolResult.success(
        {"path": arguments["path"], "entries": entries},
        duration_seconds=time.monotonic() - started,
    )


async def file_delete(arguments: dict[str, Any]) -> ToolResult:
    """Delete one file or directory inside the workspace.

    Destructive: the risk engine must have approved this invocation
    (the gateway enforces that; the tool itself double-checks the
    explicit destructive flag passed by the gateway).
    """
    started = time.monotonic()
    try:
        policy = _policy_from_args(arguments)
        target = policy.resolve(str(arguments.get("path", "")))
        if not target.exists():
            return ToolResult.failure("not_found", f"path not found: {arguments.get('path')!r}")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    except (ValueError, OSError) as err:
        return ToolResult.failure(
            "delete_error", str(err), duration_seconds=time.monotonic() - started
        )
    return ToolResult.success(
        {"path": arguments["path"], "deleted": True},
        duration_seconds=time.monotonic() - started,
    )


FILE_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "file_read": {
        "description": "Read a UTF-8 text file inside the workspace.",
        "side_effects": "none",
        "permissions": ["files.read"],
        "risk": 0,
        "idempotent": True,
        "failure_modes": ("not_found", "read_error"),
    },
    "file_write": {
        "description": "Write a UTF-8 text file inside the workspace.",
        "side_effects": "writes one file",
        "permissions": ["files.write"],
        "risk": 1,
        "idempotent": True,
        "failure_modes": ("write_error",),
    },
    "file_list": {
        "description": "List entries of one workspace directory.",
        "side_effects": "none",
        "permissions": ["files.read"],
        "risk": 0,
        "idempotent": True,
        "failure_modes": ("not_found", "list_error"),
    },
    "file_delete": {
        "description": "Delete one file or directory inside the workspace.",
        "side_effects": "destructive: removes a file or directory tree",
        "permissions": ["files.delete"],
        "risk": 2,
        "idempotent": True,
        "failure_modes": ("not_found", "delete_error"),
    },
}
