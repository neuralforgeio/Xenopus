"""Package import tests — every module boundary must load without errors."""

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


def test_import_all_modules() -> None:
    """Every boundary module must be importable without errors."""
    for name in MODULES:
        importlib.import_module(name)


def test_version_available_and_not_unknown() -> None:
    """The package version must be read from metadata (not the 'unknown' fallback)."""
    import xenopus

    assert xenopus.__version__ != "unknown"
