"""Configuration tests: defaults, environment override, and failure paths."""

from pathlib import Path

import pytest

from xenopus.config import (
    ENV_XENOPUS_HOME,
    REQUIRED_SUBDIRS,
    XenopusConfig,
    load_config,
)


def test_default_home_is_dotxenopus_in_user_home() -> None:
    config = XenopusConfig()
    assert config.home == Path.home() / ".xenopus"


def test_load_config_env_override(tmp_xenopus_home: Path) -> None:
    config = load_config()
    assert config.home == tmp_xenopus_home


def test_load_config_rejects_empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_XENOPUS_HOME, "   ")
    with pytest.raises(ValueError, match="empty"):
        load_config()


def test_subdir_rejects_unknown_name() -> None:
    config = XenopusConfig()
    with pytest.raises(ValueError, match="Unknown subdirectory"):
        config.subdir("not-a-valid-subdir")


def test_subdir_valid_for_all_required(tmp_xenopus_home: Path) -> None:
    config = load_config()
    for name in REQUIRED_SUBDIRS:
        assert config.subdir(name) == tmp_xenopus_home / name


def test_to_dict_exposes_home() -> None:
    config = XenopusConfig()
    assert config.to_dict() == {"home": str(config.home)}
