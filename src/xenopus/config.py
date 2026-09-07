"""Konfigurasi runtime Xenopus.

Sumber versi tunggal adalah ``pyproject.toml``; modul ini hanya membaca
metadata, tidak pernah mendefinisikan versi sendiri (ADR-002).
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
    """Konfigurasi dasar runtime.

    Contract:
        home: direktori root state lokal Xenopus (tidak dibuat di sini;
              pembuatan adalah tanggung jawab ``xenopus.bootstrap``).
        Menyalin config aman untuk thread (immutable dataclass).
    """

    home: Path = field(default_factory=lambda: Path.home() / DEFAULT_HOME_DIR_NAME)

    def subdir(self, name: str) -> Path:
        """Mengembalikan path subdirektori bernama ``name`` di bawah home.

        Tidak membuat direktori; hanya komputasi path.
        """
        if name not in REQUIRED_SUBDIRS:
            msg = f"Subdirektori tidak dikenal: {name!r}"
            raise ValueError(msg)
        return self.home / name

    def to_dict(self) -> dict[str, str]:
        """Representasi dict untuk diagnostik/logging (tanpa data sensitif)."""
        return {"home": str(self.home)}


def load_config() -> XenopusConfig:
    """Memuat konfigurasi runtime dari environment.

    Precedence: ``XENOPUS_HOME`` > default ``~/.xenopus``.
    Failure modes: melempar ``ValueError`` bila nilai XENOPUS_HOME kosong.
    """
    env_home = environ.get(ENV_XENOPUS_HOME)
    if env_home is not None and not env_home.strip():
        msg = f"{ENV_XENOPUS_HOME} di-set tetapi kosong"
        raise ValueError(msg)
    if env_home:
        return XenopusConfig(home=Path(env_home).expanduser())
    return XenopusConfig()
