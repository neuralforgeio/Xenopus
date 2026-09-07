"""Xenopus — self-improving autonomous AI agent runtime.

Local-first, verifiable, model-agnostic. This package is the runtime
foundation; every subpackage owns an exclusive boundary documented in ADR-001.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("xenopus")
except PackageNotFoundError:  # pragma: no cover - only when not installed
    __version__ = "unknown"
