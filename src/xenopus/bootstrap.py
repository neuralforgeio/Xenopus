"""Xenopus runtime bootstrap.

Prepares the local state directories idempotently; safe to call repeatedly.
"""

from dataclasses import dataclass

from xenopus.config import REQUIRED_SUBDIRS, XenopusConfig


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    """Bootstrap outcome.

    Contract:
        ok: True when every directory is available (pre-existing or created).
        created: subdirectories created by this call.
        existed: subdirectories that already existed.
    """

    ok: bool
    created: list[str]
    existed: list[str]


def bootstrap_runtime(config: XenopusConfig) -> BootstrapReport:
    """Create Xenopus state directories when missing (idempotent).

    Side effects: filesystem operations (mkdir) under ``config.home``.
    Failure modes: ``OSError`` from the filesystem propagates to the caller,
    which is responsible for reporting its context.
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
