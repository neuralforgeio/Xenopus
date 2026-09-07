"""Antarmuka baris perintah Xenopus.

Surface paling tipis: membaca versi dari metadata paket dan menjalankan
diagnostik. Tidak ada logika runtime di sini (ADR-001).
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
    sub.add_parser("doctor", help="Diagnostik lingkungan dan state lokal")
    return parser


def _run_doctor() -> int:
    """Menjalankan diagnostik; mengembalikan exit code 0 sukses / 1 gagal."""
    print(f"Xenopus {xenopus.__version__}")
    try:
        config = load_config()
        report = bootstrap_runtime(config)
    except (ValueError, OSError) as err:
        print(f"GAGAL: {err}", file=sys.stderr)
        return 1
    print(f"home: {config.home}")
    print(f"dibuat: {report.created}")
    print(f"sudah ada: {report.existed}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI; mengembalikan exit code proses."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _run_doctor()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
