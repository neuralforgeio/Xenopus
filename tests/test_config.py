"""Test konfigurasi: default, override env, dan jalur kegagalan."""

from pathlib import Path

import pytest

from xenopus.config import (
    ENV_XENOPUS_HOME,
    REQUIRED_SUBDIRS,
    XenopusConfig,
    load_config,
)


def test_default_home_adalah_dotxenopus_di_home_user() -> None:
    config = XenopusConfig()
    assert config.home == Path.home() / ".xenopus"


def test_load_config_override_env(tmp_xenopus_home: Path) -> None:
    config = load_config()
    assert config.home == tmp_xenopus_home


def test_load_config_env_kosong_ditolak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_XENOPUS_HOME, "   ")
    with pytest.raises(ValueError, match="kosong"):
        load_config()


def test_subdir_menolak_nama_tidak_dikenal() -> None:
    config = XenopusConfig()
    with pytest.raises(ValueError, match="tidak dikenal"):
        config.subdir("bukan-subdir-sah")


def test_subdir_sah_untuk_semua_required(tmp_xenopus_home: Path) -> None:
    config = load_config()
    for name in REQUIRED_SUBDIRS:
        assert config.subdir(name) == tmp_xenopus_home / name


def test_to_dict_menampilkan_home() -> None:
    config = XenopusConfig()
    assert config.to_dict() == {"home": str(config.home)}
