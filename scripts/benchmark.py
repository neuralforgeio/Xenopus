"""Benchmark runner: on-demand performance evidence (Phase 17).

Usage: python scripts/benchmark.py
Runs the pytest benchmark suite and prints the summary. The
standard CI gate is unaffected (benchmarks are excluded by
addopts); this script is the discoverable entry point.
"""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    """Run the benchmark suite and propagate its exit code.

    ``-o addopts=`` clears the default-run exclusion (addopts
    precede CLI args, so a plain `-m benchmark` would be overridden
    by the repo's `-m 'not benchmark'` default).
    """
    print("Xenopus benchmarks (on-demand; not part of the CI gate)")
    print("Target hardware for assumption ledger #6/#7: i5-8350U/8GB")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "benchmark", "-v", "--no-header", "-o", "addopts="],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
