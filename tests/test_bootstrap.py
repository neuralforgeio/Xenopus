"""Test bootstrap runtime: idempotensi dan laporan yang benar."""

from pathlib import Path

import pytest

from xenopus.bootstrap import bootstrap_runtime
from xenopus.config import load_config


def test_bootstrap_pertama_membuat_semua_subdirektori(tmp_xenopus_home: Path) -> None:
    report = bootstrap_runtime(load_config())
    assert report.ok is True
    assert report.existed == []
    assert sorted(report.created) == sorted(["logs", "sessions", "memory", "skills", "checkpoints"])
    for name in report.created:
        assert (tmp_xenopus_home / name).is_dir()


def test_bootstrap_kedua_idempoten(tmp_xenopus_home: Path) -> None:
    first = bootstrap_runtime(load_config())
    second = bootstrap_runtime(load_config())
    assert second.ok is True
    assert second.created == []
    assert sorted(second.existed) == sorted(first.created)


def test_bootstrap_mkdir_parent_otomatis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deep = tmp_path / "a" / "b" / "c"
    monkeypatch.setenv("XENOPUS_HOME", str(deep))
    report = bootstrap_runtime(load_config())
    assert report.ok is True
    assert (deep / "logs").is_dir()
