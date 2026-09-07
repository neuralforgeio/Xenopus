"""Test import paket — memastikan seluruh boundary modul termuat tanpa error."""

import importlib

MODULES = [
    "xenopus",
    "xenopus.config",
    "xenopus.bootstrap",
    "xenopus.cli",
    "xenopus.runtime",
    "xenopus.gateway",
    "xenopus.tools",
    "xenopus.memory",
    "xenopus.skills",
    "xenopus.provider",
    "xenopus.observability",
]


def test_import_semua_modul() -> None:
    """Seluruh modul boundary harus dapat diimpor tanpa error."""
    for name in MODULES:
        importlib.import_module(name)


def test_version_tersedia_dan_bukan_unknown() -> None:
    """Versi paket terbaca dari metadata (bukan fallback 'unknown')."""
    import xenopus

    assert xenopus.__version__ != "unknown"
