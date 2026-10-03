"""Coordinated private backups; isolated restore checks never overwrite production."""

# Parse before application imports, locks, backup creation or service calls.
if __name__ == '__main__':
    import argparse
    _parser = argparse.ArgumentParser(allow_abbrev=False, description='Create a coordinated private backup or verify the latest one.')
    _parser.add_argument('--verify-latest', action='store_true')
    _parser.parse_args()

import json, os, sqlite3, subprocess, time, hashlib, secrets, shutil, re
from datetime import datetime, timezone
from pathlib import Path
from initialize_secrets import ROOT, ENV, read_secret

BIN = ROOT / "runtime/pg/usr/lib/postgresql/16/bin"
BACKUPS = ROOT / "backups"


def run(args, env=ENV, timeout=120):
    r = subprocess.run(
        [str(x) for x in args], env=env, capture_output=True, timeout=timeout
    )
    if r.returncode:
        raise RuntimeError("Operation failed: " + Path(str(args[0])).name)
    return r.stdout.decode().strip()


def pg_env(owner=False):
    return {
        **ENV,
        "PGPASSWORD": read_secret(
            "postgres.password" if owner else "database.password"
        ),
    }


def query(sql, database="news", owner=False):
    return run(
        [
            BIN / "psql",
            "-h",
            "127.0.0.1",
            "-p",
            "55432",
            "-U",
            "newsowner" if owner else "news_app",
            "-d",
            database,
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        env=pg_env(owner),
    )


def counts(database="news", owner=False):
    return {
        table: int(query(f"SELECT COUNT(*) FROM {table}", database, owner))
        for table in ["feeds", "entries", "users"]
    }


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def create_backup():
    os.umask(0o077)
    BACKUPS.mkdir(exist_ok=True)
    BACKUPS.chmod(0o700)
    target = BACKUPS / (
        "snapshot-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + secrets.token_hex(3)
    )
    target.mkdir(mode=0o700)
    names = ["ai-news-web.service", "ai-news-miniflux.service"]
    active = []
    for name in names:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", name], env=ENV, capture_output=True
        )
        if r.returncode == 0:
            active.append(name)
    started = time.time()
    try:
        if active:
            run(["systemctl", "--user", "stop", *active])
        before = counts()
        run(
            [
                BIN / "pg_dump",
                "-h",
                "127.0.0.1",
                "-p",
                "55432",
                "-U",
                "news_app",
                "-d",
                "news",
                "-Fc",
                "-f",
                target / "miniflux.dump",
            ],
            env=pg_env(),
        )
        with (
            sqlite3.connect(ROOT / "state/analysis.sqlite3") as source,
            sqlite3.connect(target / "analysis.sqlite3") as dest,
        ):
            source.backup(dest)
        manifest = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "postgres_counts": before,
            "git_commit": run(["git", "-C", ROOT, "rev-parse", "HEAD"]),
            "contains_credentials": "Database snapshot is private; never commit it.",
            "sha256": {
                n: digest(target / n) for n in ["miniflux.dump", "analysis.sqlite3"]
            },
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    finally:
        if active:
            run(["systemctl", "--user", "start", *reversed(active)])
    # Delete only this tool's own completed snapshots beyond retention.
    complete = sorted(
        p
        for p in BACKUPS.glob("snapshot-*")
        if p.is_dir() and not p.is_symlink() and (p / "manifest.json").exists()
    )
    for old in complete[:-14]:
        shutil.rmtree(old)
    return {
        "snapshot": target.name,
        "counts": before,
        "seconds": round(time.time() - started, 2),
        "path": str(target),
    }


def verify_restore(snapshot):
    target = Path(snapshot).resolve()
    if not target.is_relative_to(BACKUPS.resolve()) or target.is_symlink():
        raise ValueError("Not a project backup")
    manifest = json.loads((target / "manifest.json").read_text())
    for name, expected in manifest["sha256"].items():
        if (
            name not in ["miniflux.dump", "analysis.sqlite3"]
            or digest(target / name) != expected
        ):
            raise ValueError("Backup checksum mismatch")
    database = "acceptance_restore_" + secrets.token_hex(5)
    assert re.fullmatch("acceptance_restore_[a-f0-9]{10}", database)
    created = False
    try:
        query("CREATE DATABASE " + database, database="postgres", owner=True)
        created = True
        run(
            [
                BIN / "pg_restore",
                "-h",
                "127.0.0.1",
                "-p",
                "55432",
                "-U",
                "newsowner",
                "-d",
                database,
                "--no-owner",
                "--no-acl",
                "--exit-on-error",
                target / "miniflux.dump",
            ],
            env=pg_env(True),
        )
        restored = counts(database, True)
        if restored != manifest["postgres_counts"]:
            raise ValueError("PostgreSQL row counts differ")
        with sqlite3.connect(
            "file:" + str(target / "analysis.sqlite3") + "?mode=ro", uri=True
        ) as c:
            integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
            states = dict(
                c.execute(
                    "SELECT state,COUNT(*) FROM analyses GROUP BY state"
                ).fetchall()
            )
        if integrity != "ok":
            raise ValueError("SQLite integrity check failed")
        return {
            "passed": True,
            "snapshot": target.name,
            "restored_counts": restored,
            "sqlite_integrity": integrity,
            "analysis_states": states,
            "production_overwritten": False,
        }
    finally:
        if created:
            query("DROP DATABASE " + database, database="postgres", owner=True)


if __name__ == "__main__":
    import sys, fcntl

    os.umask(0o077)
    BACKUPS.mkdir(exist_ok=True)
    lock = (BACKUPS / ".backup.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another backup or restore verification is running")
        sys.exit(2)
    try:
        if "--verify-latest" in sys.argv:
            snapshots = sorted(
                p for p in BACKUPS.glob("snapshot-*") if (p / "manifest.json").exists()
            )
            if not snapshots:
                raise RuntimeError("No completed backup")
            report = verify_restore(snapshots[-1])
            (ROOT / "artifacts/restore-test.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
        else:
            report = create_backup()
        print(json.dumps(report, ensure_ascii=False))
    except Exception as exc:
        print(
            json.dumps({"passed": False, "error": type(exc).__name__ + ": " + str(exc)})
        )
        sys.exit(1)
