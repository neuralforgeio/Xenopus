"""Xenopus runtime configuration.

The single version source is ``pyproject.toml``; this module only reads
metadata and never defines its own version (ADR-002).
"""

from dataclasses import dataclass, field
from os import environ
from pathlib import Path

ENV_XENOPUS_HOME = "XENOPUS_HOME"
DEFAULT_HOME_DIR_NAME = ".xenopus"

SUBDIR_LOGS = "logs"
SUBDIR_SESSIONS = "sessions"
SUBDIR_MEMORY = "memory"
SUBDIR_SKILLS = "skills"
SUBDIR_CHECKPOINTS = "checkpoints"

REQUIRED_SUBDIRS: tuple[str, ...] = (
    SUBDIR_LOGS,
    SUBDIR_SESSIONS,
    SUBDIR_MEMORY,
    SUBDIR_SKILLS,
    SUBDIR_CHECKPOINTS,
)


@dataclass(frozen=True, slots=True)
class XenopusConfig:
    """Base runtime configuration.

    Contract:
        home: root directory of local Xenopus state (not created here;
              creation is the responsibility of ``xenopus.bootstrap``).
        Immutable dataclass; safe to share across threads.
    """

    home: Path = field(default_factory=lambda: Path.home() / DEFAULT_HOME_DIR_NAME)

    def subdir(self, name: str) -> Path:
        """Return the path of subdirectory ``name`` under home.

        Does not create the directory; path computation only.
        Raises ValueError for unknown subdirectory names.
        """
        if name not in REQUIRED_SUBDIRS:
            msg = f"Unknown subdirectory: {name!r}"
            raise ValueError(msg)
        return self.home / name

    def to_dict(self) -> dict[str, str]:
        """Dict representation for diagnostics/logging (no sensitive data)."""
        return {"home": str(self.home)}


def load_config() -> XenopusConfig:
    """Load runtime configuration from the environment.

    Precedence: ``XENOPUS_HOME`` > default ``~/.xenopus``.
    Failure modes: raises ``ValueError`` when XENOPUS_HOME is set but empty.
    """
    env_home = environ.get(ENV_XENOPUS_HOME)
    if env_home is not None and not env_home.strip():
        msg = f"{ENV_XENOPUS_HOME} is set but empty"
        raise ValueError(msg)
    if env_home:
        return XenopusConfig(home=Path(env_home).expanduser())
    return XenopusConfig()
