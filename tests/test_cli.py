"""CLI tests: version visibility, doctor success, and reported error paths."""

from pathlib import Path

import pytest

import xenopus
from xenopus.cli import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """argparse action="version" exits via SystemExit(0) after printing."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert f"xenopus {xenopus.__version__}" in out


def test_doctor_succeeds_with_temporary_home(
    tmp_xenopus_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert str(tmp_xenopus_home) in out
    assert "FAILED" not in out


def test_doctor_fails_on_empty_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XENOPUS_HOME", " ")
    assert main(["doctor"]) == 1
    err = capsys.readouterr().err
    assert "FAILED" in err


def test_no_subcommand_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage:" in capsys.readouterr().out
