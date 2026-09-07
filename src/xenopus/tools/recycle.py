"""Recycle bin: reversible deletes inside the workspace.

Implements the ADR-008 reversal criteria: with a durable recycle
mechanism, file_delete moves from DENY (destructive+irreversible) to
APPROVAL (destructive but reversible). Deleted entries move into
`.xenopus-recycle/` under the workspace root, retaining content and
origin path for restore.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xenopus.tools.contracts import ToolResult
from xenopus.tools.files import PathPolicy

RECYCLE_DIR_NAME = ".xenopus-recycle"
DELETED_MARKER = "deleted-from"


@dataclass(frozen=True, slots=True)
class RecycleEntry:
    """One recycled path record."""

    entry_id: str
    original_path: str
    stored_path: Path


class RecycleBin:
    """Workspace-local reversible delete store.

    Contract:
        delete(): moves a file/dir into the recycle directory with a
            unique entry id (never destroys content).
        restore(): moves an entry back to its original path (refuses
            overwrite: existing targets block restore).
        entries()/purge(): inspection and manual cleanup.

    Invariants:
        recycled content lives INSIDE the workspace root; nothing ever
        leaves the boundary.
    """

    def __init__(self, policy: PathPolicy) -> None:
        self._policy = policy
        self._root = policy.root / RECYCLE_DIR_NAME
        self._root.mkdir(parents=True, exist_ok=True)

    def delete(self, target: Path, original_ref: str) -> RecycleEntry:
        """Move ``target`` into the bin; returns the entry record."""
        entry_id = f"rcy-{time.monotonic_ns()}"
        stored = self._root / entry_id
        shutil.move(str(target), str(stored))
        (stored / DELETED_MARKER).write_text(
            original_ref, encoding="utf-8"
        ) if stored.is_dir() else None
        if stored.is_file():
            # File case: marker travels as a sibling record.
            record = self._root / f"{entry_id}.from"
            record.write_text(original_ref, encoding="utf-8")
        return RecycleEntry(entry_id=entry_id, original_path=original_ref, stored_path=stored)

    def restore(self, entry_id: str) -> Path:
        """Restore an entry to its original path; refuses overwrite."""
        stored = self._root / entry_id
        if not stored.exists():
            msg = f"unknown recycle entry: {entry_id!r}"
            raise ValueError(msg)
        marker = self._root / f"{entry_id}.from"
        original_ref = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
        if not original_ref:
            # Directory case: marker inside the entry.
            inner = stored / DELETED_MARKER
            original_ref = inner.read_text(encoding="utf-8").strip() if inner.exists() else ""
        if not original_ref:
            msg = f"recycle entry {entry_id!r} has no origin record"
            raise ValueError(msg)
        target = self._policy.resolve(original_ref)
        if target.exists():
            msg = f"restore target already exists: {original_ref!r}"
            raise ValueError(msg)
        marker.unlink(missing_ok=True)
        shutil.move(str(stored), str(target))
        return target

    def entries(self) -> list[RecycleEntry]:
        """List binned entries (id + original path)."""
        result: list[RecycleEntry] = []
        for stored in sorted(self._root.iterdir()):
            if stored.is_dir() and stored.name.startswith("rcy-"):
                inner = stored / DELETED_MARKER
                original = (
                    inner.read_text(encoding="utf-8").strip() if inner.exists() else "(unknown)"
                )
                result.append(RecycleEntry(stored.name, original, stored))
            elif stored.is_file() and stored.suffix == ".from":
                original = stored.read_text(encoding="utf-8").strip()
                entry_id = stored.stem
                result.append(RecycleEntry(entry_id, original, self._root / entry_id))
        return result

    def purge(self) -> int:
        """Permanently remove all binned entries; returns count."""
        count = 0
        for entry in self.entries():
            shutil.rmtree(entry.stored_path, ignore_errors=True)
            (self._root / f"{entry.entry_id}.from").unlink(missing_ok=True)
            count += 1
        return count


async def file_delete_recycle(arguments: dict[str, Any]) -> ToolResult:
    """Delete via recycle bin (reversible; gateway requires approval)."""
    started = time.monotonic()
    policy_obj = arguments.get("__path_policy__")
    bin_obj = arguments.get("__recycle_bin__")
    if not isinstance(policy_obj, PathPolicy) or not isinstance(bin_obj, RecycleBin):
        return ToolResult.failure(
            "delete_error",
            "recycle delete invoked without policy/bin wiring (internal error)",
            duration_seconds=time.monotonic() - started,
        )
    try:
        target = policy_obj.resolve(str(arguments.get("path", "")))
        if not target.exists():
            return ToolResult.failure("not_found", f"path not found: {arguments.get('path')!r}")
        entry = bin_obj.delete(target, str(arguments.get("path", "")))
    except (ValueError, OSError) as err:
        return ToolResult.failure(
            "delete_error", str(err), duration_seconds=time.monotonic() - started
        )
    return ToolResult.success(
        {
            "path": arguments.get("path"),
            "deleted": True,
            "recycle_entry": entry.entry_id,
            "restorable": True,
        },
        duration_seconds=time.monotonic() - started,
    )
