#!/usr/bin/env python3
"""Unpack a candidate submission archive and run the container-based test suite.

The input archive is the ``.tar.gz`` produced by the assignment's
``package_submission.py`` (top-level ``trainee_assignment/`` folder containing
``server/`` and ``cli/``).

Usage:
    python validate.py path/to/trainee_assignment-john-doe-20260722-120000.tar.gz
    python validate.py submission.tar.gz --name john-doe
    python validate.py submission.tar.gz --extract-only
    python validate.py submission.tar.gz -- -k refresh   # pass args to pytest

What it does:
    1. Safely extracts the archive into ``submissions/<name>/``.
    2. Locates the submission root (the directory holding ``server/`` and ``cli/``).
    3. Runs pytest with ``SUBMISSION_DIR`` pointing at that root, which spins up
       the server and the candidate CLI in containers and exercises every
       documented and hidden scenario.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
SUBMISSIONS_DIR = REPO_ROOT / "submissions"
CURRENT_SUBMISSION_FILE = REPO_ROOT / ".current_submission"


def derive_name(archive: Path, override: str | None) -> str:
    """Pick a folder name for the extracted submission."""
    if override:
        return override
    name = archive.name
    for suffix in (".tar.gz", ".tgz", ".tar"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return archive.stem


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def safe_extract(archive: Path, dest: Path) -> None:
    """Extract a tar.gz, rejecting path traversal and unsafe members.

    Candidate archives are untrusted, so guard against tarbombs (absolute paths,
    ``..`` components, symlinks pointing outside ``dest``).
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            member_path = dest / member.name
            if not _is_within(dest, member_path):
                raise ValueError(f"unsafe path in archive: {member.name!r}")
            if member.issym() or member.islnk():
                link_target = dest / member.name
                resolved = link_target.parent / member.linkname
                if not _is_within(dest, resolved):
                    raise ValueError(
                        f"unsafe link in archive: {member.name!r} -> {member.linkname!r}"
                    )
        # ``filter='data'`` (Python 3.12+) strips setuid bits, blocks absolute
        # paths, etc. Fall back gracefully on older interpreters.
        try:
            tar.extractall(dest, filter="data")  # type: ignore[call-arg]
        except TypeError:
            tar.extractall(dest)


def find_submission_root(base: Path) -> Path:
    """Return the directory that contains both ``server/`` and ``cli/``."""
    candidates = [base, *[p for p in base.iterdir() if p.is_dir()]]
    for candidate in candidates:
        if (candidate / "server").is_dir() and (candidate / "cli").is_dir():
            return candidate
    raise SystemExit(
        f"error: could not locate a submission root (a folder with both "
        f"'server/' and 'cli/') under {base}"
    )


def run_pytest(submission_root: Path, pytest_args: list[str]) -> int:
    env = dict(os.environ)
    env["SUBMISSION_DIR"] = str(submission_root)
    cmd = [sys.executable, "-m", "pytest", str(REPO_ROOT / "tests"), *pytest_args]
    print(f"\nRunning tests against: {submission_root}")
    print(f"  $ SUBMISSION_DIR={submission_root} {' '.join(cmd)}\n")
    return subprocess.call(cmd, cwd=str(REPO_ROOT), env=env)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="validate.py",
        description="Extract a candidate submission archive and run the test suite.",
    )
    parser.add_argument("archive", type=Path, help="Path to the submission .tar.gz")
    parser.add_argument(
        "--name",
        default=None,
        help="Folder name under submissions/ (default: derived from the archive name).",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only extract the archive; do not run the tests.",
    )
    # Everything argparse doesn't recognise (e.g. `-k refresh`, or anything
    # after a `--`) is forwarded to pytest.
    args, extra = parser.parse_known_args(argv)
    args.pytest_args = [a for a in extra if a != "--"]
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    archive: Path = args.archive.expanduser()
    if not archive.is_file():
        print(f"error: archive not found: {archive}", file=sys.stderr)
        return 2

    name = derive_name(archive, args.name)
    dest = SUBMISSIONS_DIR / name

    if dest.exists():
        print(f"Removing existing extraction at {dest}")
        import shutil

        shutil.rmtree(dest)

    print(f"Extracting {archive.name} -> {dest}")
    safe_extract(archive, dest)

    submission_root = find_submission_root(dest)
    print(f"Submission root: {submission_root}")

    # Record the selected submission so a bare `pytest` run targets it.
    CURRENT_SUBMISSION_FILE.write_text(f"{submission_root}\n")
    print(f"Recorded current submission in {CURRENT_SUBMISSION_FILE.name}")

    if args.extract_only:
        print("Extraction complete (--extract-only).")
        return 0

    return run_pytest(submission_root, args.pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
