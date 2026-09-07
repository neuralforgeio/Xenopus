"""Xenopus — self-improving autonomous AI agent runtime.

Lokal-first, verifiable, model-agnostic. Paket ini adalah fondasi runtime;
setiap subpaket memiliki batas kepemilikan yang dijelaskan pada ADR-001.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("xenopus")
except PackageNotFoundError:  # pragma: no cover - hanya saat belum ter-install
    __version__ = "unknown"
