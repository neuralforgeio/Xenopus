"""Fixture bersama test Xenopus."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_xenopus_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Arahkan XENOPUS_HOME ke direktori sementara yang terisolasi."""
    home = tmp_path / "xenopus-home"
    monkeypatch.setenv("XENOPUS_HOME", str(home))
    return home
