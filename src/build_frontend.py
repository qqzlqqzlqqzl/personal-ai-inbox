"""Build away from the live tree; retain old hashed chunks for existing browser tabs."""

import json, os, re, shutil, subprocess, time, uuid
from pathlib import Path

ROOT = Path("/home/ubuntu/ai-news")


def publish(staging: Path, live: Path):
    index = staging / "index.html"
    if not index.is_file():
        raise ValueError("Build is missing index.html")
    refs = re.findall(r'(?:src|href)="(/assets/[^"?]+)', index.read_text())
    if not refs:
        raise ValueError("No frontend assets referenced")
    for ref in refs:
        p = (staging / ref.lstrip("/")).resolve()
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


def main():
    stage = ROOT / "runtime" / ("web-build-" + uuid.uuid4().hex)
    node = ROOT / "runtime/node/bin/node"
    nodebin = node.parent if node.exists() else Path(shutil.which("node")).parent
    env = {
        **os.environ,
        "NODE_OPTIONS": "--max-old-space-size=700",
        "PATH": str(nodebin) + os.pathsep + os.environ["PATH"],
    }
    web = ROOT / "upstream/reactflux"
    subprocess.run(
        [str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/patch_frontend.py")],
        check=True,
        env=env,
    )
    subprocess.run(
        [str(ROOT / "runtime/venv/bin/python"), str(ROOT / "src/polish_frontend.py")],
        check=True,
        env=env,
    )
    cmd = [
        str(ROOT / "runtime/build-tools/node_modules/.bin/pnpm"),
        "exec",
        "vite",
        "build",
        "--outDir",
        str(stage),
        "--emptyOutDir",
    ]
    subprocess.run(cmd, cwd=web, env=env, check=True)
    count = publish(stage, web / "build")
    report = {
        "at": time.time(),
        "files_published": count,
        "old_hashed_assets_retained": True,
        "upstream": "534eeb97723ac11025de4ec1ac56335072e3be52",
    }
    (ROOT / "artifacts/frontend-build.json").write_text(json.dumps(report, indent=2))
    shutil.rmtree(stage)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
