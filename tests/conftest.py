"""Shared test fixtures for Xenopus."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_xenopus_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XENOPUS_HOME at an isolated temporary directory."""
    home = tmp_path / "xenopus-home"
    monkeypatch.setenv("XENOPUS_HOME", str(home))
    return home
