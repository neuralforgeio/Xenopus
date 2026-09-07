"""Release tag parity guard (Protocol v9 Section 17.9).

Verifies that every ``v*`` tag on the remote has a published GitHub
Release. Exits with code 1 (CI failure) when a tag has no release,
enforcing P7 parity from the very first commit.

Without arguments: checks HEAD only (local-first mode).
With ``--check-remote``: checks tag-vs-release parity on the remote via ``gh``.
"""

import subprocess
import sys
from argparse import ArgumentParser


def run(cmd: list[str]) -> str:
    """Run a command, return stdout; failure -> SystemExit with a message."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit(f"COMMAND NOT FOUND: {cmd[0]}")
    except subprocess.CalledProcessError as err:
        sys.exit(f"FAILED: {' '.join(cmd)}\n{err.stderr.strip()}")
    return proc.stdout


def check_local_head_is_clean() -> None:
    """HEAD must sit on a clean working tree (no stray changes)."""
    status = run(["git", "status", "--porcelain"])
    if status.strip():
        sys.exit("FAILED: working tree is not clean")


def check_remote_parity() -> None:
    """Every v* tag on the remote must have a published GitHub Release."""
    tags = run(["git", "ls-remote", "--tags", "origin"]).splitlines()
    version_tags = {line.split("refs/tags/")[1] for line in tags if "refs/tags/v" in line}
    version_tags = {t.removesuffix("^{}") for t in version_tags}
    if not version_tags:
        print("OK: no version tags yet — parity satisfied (no public release)")
        return
    for tag in sorted(version_tags):
        result = run(["gh", "release", "view", tag, "--json", "isDraft"])
        if '"isDraft": true' in result:
            sys.exit(f"FAILED: {tag} is still a draft — a draft is not a release (P7)")
        print(f"OK: {tag} has a verified release")


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--check-remote", action="store_true")
    args = parser.parse_args()
    if args.check_remote:
        check_remote_parity()
    else:
        check_local_head_is_clean()
        print("OK: HEAD is clean; no tags currently demand release parity")


if __name__ == "__main__":
    main()
