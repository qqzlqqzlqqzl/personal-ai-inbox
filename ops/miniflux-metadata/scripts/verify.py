#!/usr/bin/env python3
"""Fail-closed official-source compatibility and patch-integrity gate (stdlib only)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PINS = json.loads((ROOT / "pins.json").read_text())
PATCH = ROOT / "miniflux-2.3.3-entry-metadata.patch"


def run(*args, cwd=None):
    result = subprocess.run(args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    return result.stdout.strip()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_package():
    require(digest(PATCH) == PINS["patch_sha256"], "patch digest drift")
    expected = PINS["test_sha256"]
    actual = {str(p.relative_to(ROOT / "tests")) for p in (ROOT / "tests").rglob("*") if p.is_file()}
    require(actual == set(expected), "test overlay inventory drift")
    for name, sha in expected.items():
        require(digest(ROOT / "tests" / name) == sha, f"test digest drift: {name}")


def verify_source(source, patched=False):
    require(source.is_dir() and not source.is_symlink(), "source directory missing or symlinked")
    require(run("git", "rev-parse", "HEAD", cwd=source) == PINS["upstream_commit"], "unsupported upstream commit; review/rebase required")
    require(run("git", "remote", "get-url", "origin", cwd=source) == PINS["upstream_repository"], "unapproved source origin")
    for name, sha in PINS["upstream_blobs"].items():
        require(run("git", "rev-parse", "HEAD:" + name, cwd=source) == sha, f"upstream blob drift: {name}")
    require("\ngo 1.26.0\n" in (source / "go.mod").read_text(), "upstream Go requirement drift")
    if not patched:
        require(not run("git", "status", "--porcelain", "--untracked-files=all", cwd=source), "source tree must be pristine before application")
        require(not run("git", "ls-files", "--others", cwd=source), "source tree contains additional files, including ignored build inputs")
        return
    files = {**PINS["patched_sha256"], **PINS["test_sha256"]}
    for name, sha in files.items():
        require(digest(source / name) == sha, f"patched source or test drift: {name}")
    tracked = set(filter(None, run("git", "diff", "--name-only", cwd=source).splitlines()))
    require(tracked == {"internal/api/api.go"}, f"unexpected tracked edits: {tracked}")
    require(not run("git", "diff", "--cached", "--name-only", cwd=source), "unexpected staged changes")
    untracked = set(filter(None, run("git", "ls-files", "--others", cwd=source).splitlines()))
    require(untracked == set(files) - {"internal/api/api.go"}, f"unexpected source additions: {untracked}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["package", "fetch", "apply", "pristine", "patched"])
    parser.add_argument("source", nargs="?", type=Path)
    args = parser.parse_args()
    verify_package()
    if args.command == "package":
        print("PASS: patch and test inventory/digests")
        return
    require(args.source is not None, "source argument required")
    source = args.source.absolute()
    if args.command == "fetch":
        require(not source.exists(), "fetch destination must not exist")
        source.mkdir(parents=True)
        run("git", "init", "--quiet", str(source))
        run("git", "remote", "add", "origin", PINS["upstream_repository"], cwd=source)
        run("git", "-c", "core.hooksPath=/dev/null", "fetch", "--depth=1", "origin", PINS["upstream_commit"], cwd=source)
        run("git", "-c", "core.hooksPath=/dev/null", "checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=source)
        verify_source(source)
    elif args.command in {"pristine", "patched"}:
        verify_source(source, patched=args.command == "patched")
    else:
        verify_source(source)
        run("git", "apply", "--check", "--whitespace=error-all", str(PATCH), cwd=source)
        run("git", "apply", "--whitespace=error-all", str(PATCH), cwd=source)
        for name in PINS["test_sha256"]:
            dest = source / name
            require(not dest.exists(), f"test would overwrite upstream source: {name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "tests" / name, dest)
        verify_source(source, patched=True)
    print(f"PASS: {args.command} at pinned official commit {PINS['upstream_commit']}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"FAIL CLOSED: {exc}", file=sys.stderr)
        sys.exit(1)
