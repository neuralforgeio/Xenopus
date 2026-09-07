"""File tools + path policy tests: boundaries, traversal, operations."""

from pathlib import Path

import pytest

from xenopus.tools.files import PathPolicy, file_delete, file_list, file_read, file_write


class TestPathPolicy:
    def test_resolve_inside_root(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        assert policy.resolve("sub/file.txt") == (tmp_path / "sub" / "file.txt").resolve()

    def test_traversal_escape_rejected(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        with pytest.raises(ValueError, match="escapes the workspace boundary"):
            policy.resolve("../outside.txt")

    def test_deep_traversal_rejected(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        with pytest.raises(ValueError, match="escapes"):
            policy.resolve("a/b/../../../escape.txt")

    def test_absolute_path_inside_root_allowed(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        target = tmp_path / "direct.txt"
        assert policy.resolve(str(target)) == target.resolve()

    def test_root_itself_resolves(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        assert policy.resolve(".") == policy.root


class TestFileTools:
    async def test_write_then_read_round_trip(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        write = await file_write(
            {"path": "note.txt", "content": "hello", "__path_policy__": policy}
        )
        assert write.ok is True
        read = await file_read({"path": "note.txt", "__path_policy__": policy})
        assert read.ok is True
        assert read.data["content"] == "hello"

    async def test_read_missing_file_is_failure_not_crash(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        result = await file_read({"path": "ghost.txt", "__path_policy__": policy})
        assert result.ok is False
        assert result.error_code == "not_found"

    async def test_write_creates_parent_dirs(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        result = await file_write({"path": "a/b/c.txt", "content": "x", "__path_policy__": policy})
        assert result.ok is True
        assert (tmp_path / "a" / "b" / "c.txt").exists()

    async def test_list_sorted_entries(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        for name in ("b.txt", "a.txt", "c.txt"):
            await file_write({"path": name, "content": "x", "__path_policy__": policy})
        result = await file_list({"path": ".", "__path_policy__": policy})
        assert result.data["entries"] == ["a.txt", "b.txt", "c.txt"]

    async def test_delete_removes_file(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        await file_write({"path": "doomed.txt", "content": "x", "__path_policy__": policy})
        result = await file_delete({"path": "doomed.txt", "__path_policy__": policy})
        assert result.ok is True
        assert not (tmp_path / "doomed.txt").exists()

    async def test_delete_missing_is_not_found(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        result = await file_delete({"path": "ghost.txt", "__path_policy__": policy})
        assert result.ok is False
        assert result.error_code == "not_found"

    async def test_operation_outside_boundary_fails_closed(self, tmp_path: Path) -> None:
        policy = PathPolicy(tmp_path)
        result = await file_read({"path": "../../secret.txt", "__path_policy__": policy})
        assert result.ok is False
        assert result.error_code == "read_error"
        assert "escapes the workspace boundary" in result.error_message

    async def test_tool_without_policy_is_wiring_error(self) -> None:
        result = await file_read({"path": "x.txt"})
        assert result.ok is False
        assert "path policy" in result.error_message
