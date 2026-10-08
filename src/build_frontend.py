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
        # Reserve exclusively and hold the inode until cleanup, so a collision
        # or replacement at this name can never be mistaken for our own file.
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            owned = os.fstat(fd)

            def owns_temp():
                try:
                    current = temp.lstat()
                except FileNotFoundError:
                    return False
                return (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino)

            try:
                shutil.copy2(source, temp)
                if not owns_temp():
                    raise RuntimeError("Temporary publish file changed")
                os.replace(temp, dest)
            finally:
                try:
                    if owns_temp():
                        temp.unlink()
                except OSError:
                    pass  # Cleanup failure must not mask the original publish error.
        finally:
            os.close(fd)
    return len(files)


def validate_reader_bundle(staging: Path):
    """Reject a generated bundle that regresses reader-facing detail semantics."""
    javascript = "\n".join(
        path.read_text(errors="ignore")
        for path in (staging / "assets").glob("*.js")
        if path.is_file()
    )
    required = ("资源看板", "AI 设置 · 来源")
    forbidden = (
        "评分是模型判断",
        "依据抓取的原网页文本",
        "模型输入 ",
        "图片保留不代表模型理解了图片内容",
    )
    missing = [text for text in required if text not in javascript]
    stale = [text for text in forbidden if text in javascript]
    if missing or stale:
        raise ValueError(
            "Reader quality bundle validation failed: "
            + json.dumps({"missing": missing, "stale": stale}, ensure_ascii=False)
        )


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



def prepare_version_info(web: Path, node: Path, env):
    """Run the pinned reader's standard prebuild when invoking Vite directly."""
    subprocess.run(
        [str(node), str(web / "src/scripts/version-info.js")],
        cwd=web, env=env, check=True, timeout=60,
    )


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
    subprocess.run([str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_scope_ai_filters.py")], check=True, env=env, timeout=60)
    subprocess.run([str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_reader_detail_quality.py")], check=True, env=env, timeout=60)
    subprocess.run([str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_reader_entry_defaults.py")], check=True, env=env, timeout=60)
    subprocess.run([str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_interaction_review.py")], check=True, env=env, timeout=60)
    from install_agent_status import install as install_agent_status
    install_agent_status(ROOT)
    prepare_version_info(web, nodebin / "node", env)
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
    validate_reader_bundle(stage)
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

