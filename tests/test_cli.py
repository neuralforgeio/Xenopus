"""Test CLI: versi terlihat, doctor sukses, dan error path terlapor."""

from pathlib import Path

import pytest

import xenopus
from xenopus.cli import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """argparse action="version" keluar via SystemExit(0) setelah mencetak."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert f"xenopus {xenopus.__version__}" in out


def test_doctor_sukses_dengan_home_sementara(
    tmp_xenopus_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert str(tmp_xenopus_home) in out
    assert "GAGAL" not in out


def test_doctor_gagal_env_kosong(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XENOPUS_HOME", " ")
    assert main(["doctor"]) == 1
    err = capsys.readouterr().err
    assert "GAGAL" in err


def test_tanpa_subcommand_menampilkan_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage:" in capsys.readouterr().out
