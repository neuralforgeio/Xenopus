"""Bootstrap runtime Xenopus.

Menyiapkan direktori state lokal secara idempoten; aman dipanggil berulang.
"""

from dataclasses import dataclass

from xenopus.config import REQUIRED_SUBDIRS, XenopusConfig


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    """Hasil bootstrap.

    Contract:
        ok: True bila semua direktori tersedia (sudah ada maupun baru dibuat).
        created: subdirektori yang baru dibuat.
        existed: subdirektori yang sudah ada sebelumnya.
    """

    ok: bool
    created: list[str]
    existed: list[str]


def bootstrap_runtime(config: XenopusConfig) -> BootstrapReport:
    """Membuat direktori state Xenopus bila belum ada (idempoten).

    Side effects: operasi filesystem (mkdir) di bawah ``config.home``.
    Failure modes: membiarkan ``OSError`` dari filesystem merambat —
    pemanggil menangkap dan melaporkan konteksnya.
    """
    created: list[str] = []
    existed: list[str] = []
    for name in REQUIRED_SUBDIRS:
        target = config.subdir(name)
        if target.is_dir():
            existed.append(name)
        else:
            target.mkdir(parents=True, exist_ok=True)
            created.append(name)
    return BootstrapReport(ok=True, created=created, existed=existed)
