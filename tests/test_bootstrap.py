"""Bootstrap runtime tests: idempotency and correct reporting."""

from pathlib import Path

import pytest

from xenopus.bootstrap import bootstrap_runtime
from xenopus.config import load_config


def test_first_bootstrap_creates_all_subdirectories(tmp_xenopus_home: Path) -> None:
    report = bootstrap_runtime(load_config())
    assert report.ok is True
    assert report.existed == []
    assert sorted(report.created) == sorted(["logs", "sessions", "memory", "skills", "checkpoints"])
    for name in report.created:
        assert (tmp_xenopus_home / name).is_dir()


def test_second_bootstrap_is_idempotent(tmp_xenopus_home: Path) -> None:
    first = bootstrap_runtime(load_config())
    second = bootstrap_runtime(load_config())
    assert second.ok is True
    assert second.created == []
    assert sorted(second.existed) == sorted(first.created)


def test_bootstrap_creates_parent_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deep = tmp_path / "a" / "b" / "c"
    monkeypatch.setenv("XENOPUS_HOME", str(deep))
    report = bootstrap_runtime(load_config())
    assert report.ok is True
    assert (deep / "logs").is_dir()
