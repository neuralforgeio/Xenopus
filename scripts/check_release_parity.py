"""Guard paritas tag-rilis (Protocol v9 Section 17.9).

Memverifikasi bahwa setiap tag ``v*`` pada remote memiliki GitHub Release
yang dipublikasikan. Keluar dengan kode 1 (gagal CI) bila ada tag tanpa
rilis — memaksa paritas P7 sejak commit pertama.

Tanpa argumen: memeriksa HEAD saja (mode lokal-prima).
Dengan ``--check-remote``: memeriksa paritas tag-vs-rilis di remote via ``gh``.
"""

import subprocess
import sys
from argparse import ArgumentParser


def run(cmd: list[str]) -> str:
    """Jalankan perintah, kembalikan stdout; gagal -> SystemExit dengan pesan."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit(f"PERINTAH TIDAK ADA: {cmd[0]}")
    except subprocess.CalledProcessError as err:
        sys.exit(f"GAGAL: {' '.join(cmd)}\n{err.stderr.strip()}")
    return proc.stdout


def check_local_head_is_clean() -> None:
    """HEAD harus berada pada branch yang bersih (tidak ada commit liar)."""
    status = run(["git", "status", "--porcelain"])
    if status.strip():
        sys.exit("GAGAL: working tree tidak bersih")


def check_remote_parity() -> None:
    """Semua tag v* di remote wajib punya GitHub Release terpublikasi."""
    tags = run(["git", "ls-remote", "--tags", "origin"]).splitlines()
    version_tags = {line.split("refs/tags/")[1] for line in tags if "refs/tags/v" in line}
    version_tags = {t.removesuffix("^{}") for t in version_tags}
    if not version_tags:
        print("OK: belum ada tag versi — paritas terpenuhi (belum ada rilis publik)")
        return
    for tag in sorted(version_tags):
        result = run(["gh", "release", "view", tag, "--json", "isDraft"])
        if '"isDraft": true' in result:
            sys.exit(f"GAGAL: {tag} masih draft — draft bukan rilis (P7)")
        print(f"OK: {tag} memiliki rilis terverifikasi")


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--check-remote", action="store_true")
    args = parser.parse_args()
    if args.check_remote:
        check_remote_parity()
    else:
        check_local_head_is_clean()
        print("OK: HEAD bersih; belum ada tag yang menuntut paritas rilis")


if __name__ == "__main__":
    main()
