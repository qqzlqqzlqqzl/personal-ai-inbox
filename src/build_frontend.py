"""Build away from the live tree; retain old hashed chunks for existing browser tabs."""

import json, os, re, shutil, subprocess, time, uuid, gzip
from pathlib import Path

ROOT = Path("/home/ubuntu/ai-news")
PUBLIC_BASE = "/inbox/"


def publish(staging: Path, live: Path, base_path: str = "/"):
    if not re.fullmatch(r"/(?:[A-Za-z0-9._~-]+/)*", base_path) or any(
        part in (".", "..") for part in base_path.split("/")
    ):
        raise ValueError("Invalid frontend base path")
    index = staging / "index.html"
    if not index.is_file():
        raise ValueError("Build is missing index.html")
    refs = re.findall(
        r'(?:src|href)="(' + re.escape(base_path) + r'assets/[^"?]+)',
        index.read_text(),
    )
    if not refs:
        raise ValueError("No frontend assets referenced")
    for ref in refs:
        p = (staging / ref[len(base_path):]).resolve()
        if not p.is_relative_to(staging.resolve()) or not p.is_file():
            raise ValueError("Build references a missing asset")
    files = [p for p in staging.rglob("*") if p.is_file()]
    if any(p.is_symlink() for p in files):
        raise ValueError("Build symlinks are not permitted")
    live.mkdir(parents=True, exist_ok=True)
    # Publish hashed chunks first, HTML next, service worker last.
    files.sort(
        key=lambda p: (
            2 if p.name == "sw.js" else 1 if p.name == "index.html" else 0,
            str(p),
        )
    )
    for source in files:
        dest = live / source.relative_to(staging)
        dest.parent.mkdir(parents=True, exist_ok=True)
        temp = dest.with_name(dest.name + ".new-" + uuid.uuid4().hex)
        shutil.copy2(source, temp)
        os.replace(temp, dest)
    return len(files)


def precompress(staging: Path):
    """Create deterministic gzip siblings for text assets served by the gateway."""
    count = 0
    for source in staging.rglob("*"):
        if (
            source.is_file()
            and source.suffix in {".js", ".css", ".json", ".webmanifest", ".html", ".svg"}
            and source.stat().st_size >= 512
            and source.name not in {"sw.js", "registerSW.js"}
        ):
            compressed = gzip.compress(source.read_bytes(), compresslevel=6, mtime=0)
            target = source.with_name(source.name + ".gz")
            target.write_bytes(compressed)
            count += 1
    return count


def main():
    stage = ROOT / "runtime" / ("web-build-" + uuid.uuid4().hex)
    node = ROOT / "runtime/node/bin/node"
    nodebin = node.parent if node.exists() else Path(shutil.which("node")).parent
    env = {
        **os.environ,
        "VITE_BASE_PATH": PUBLIC_BASE,
        "NODE_OPTIONS": "--max-old-space-size=700",
        "PATH": str(nodebin) + os.pathsep + os.environ["PATH"],
    }
    web = ROOT / "upstream/reactflux"
    subprocess.run(
        [str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_frontend.py")],
        check=True,
        env=env,
        timeout=60,
    )
    subprocess.run(
        [str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/polish_frontend.py")],
        check=True,
        env=env,
        timeout=60,
    )
    subprocess.run(
        [str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/specialize_login.py")],
        check=True,
        env=env,
        timeout=60,
    )
    subprocess.run([str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_reading_session.py")], check=True, env=env, timeout=60)
    subprocess.run([str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_ui_review.py")], check=True, env=env, timeout=60)
    subprocess.run([str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_article_notes.py")], check=True, env=env, timeout=60)
    subprocess.run([str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_reading_telemetry.py")], check=True, env=env, timeout=60)
    cmd = [
        str(ROOT / "runtime/build-tools/node_modules/.bin/pnpm"),
        "exec",
        "vite",
        "build",
        "--outDir",
        str(stage),
        "--emptyOutDir",
    ]
    subprocess.run(cmd, cwd=web, env=env, check=True, timeout=600)
    compressed = precompress(stage)
    count = publish(stage, web / "build", PUBLIC_BASE)
    report = {
        "at": time.time(),
        "files_published": count,
        "precompressed_assets": compressed,
        "base_path": PUBLIC_BASE,
        "old_hashed_assets_retained": True,
        "upstream": "534eeb97723ac11025de4ec1ac56335072e3be52",
    }
    (ROOT / "artifacts/frontend-build.json").write_text(json.dumps(report, indent=2))
    # Retain staging for rollback; no destructive cleanup during deployment.
    print(json.dumps(report))


if __name__ == "__main__":
    main()
