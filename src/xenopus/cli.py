"""Xenopus command-line interface.

The thinnest surface: reads the version from package metadata and runs
diagnostics. No runtime logic lives here (ADR-001).
"""

import argparse
import sys

import xenopus
from xenopus.bootstrap import bootstrap_runtime
from xenopus.config import load_config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xenopus",
        description="Xenopus — self-improving autonomous AI agent runtime.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {xenopus.__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="Run environment and local-state diagnostics")
    return parser


def _run_doctor() -> int:
    """Run diagnostics; return exit code 0 on success, 1 on failure."""
    print(f"Xenopus {xenopus.__version__}")
    try:
        config = load_config()
        report = bootstrap_runtime(config)
    except (ValueError, OSError) as err:
        print(f"FAILED: {err}", file=sys.stderr)
        return 1
    print(f"home: {config.home}")
    print(f"created: {report.created}")
    print(f"already present: {report.existed}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _run_doctor()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
