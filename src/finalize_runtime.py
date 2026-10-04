"""Remove the old news stack runtime dependency and install project-only backup timer."""

if __name__ == '__main__':
    import argparse
    argparse.ArgumentParser(allow_abbrev=False, description='Finalize the project runtime and install its services.').parse_args()

from pathlib import Path
import hashlib, json, os, shutil, subprocess
from initialize_secrets import ROOT, ENV


def main():
    target = ROOT / "runtime/node"
    if not (target / "bin/node").exists():
        source = Path(shutil.which("node")).resolve().parent.parent
        if not (source / "LICENSE").exists():
            raise RuntimeError("Cannot identify the installed Node distribution")
        shutil.copytree(source, target, symlinks=True)
    version = subprocess.check_output(
        [str(target / "bin/node"), "--version"], text=True
    ).strip()
    manifest = {
        "node_version": version,
        "node_sha256": hashlib.sha256((target / "bin/node").read_bytes()).hexdigest(),
        "origin": "Copy of already-installed official Node distribution; no old application files copied",
        "rsshub": "a84b41faa31581bacac4cea374fcf909f784bab9",
        "reactflux": "534eeb97723ac11025de4ec1ac56335072e3be52",
        "miniflux": "2.3.3",
    }
    (ROOT / "upstream.lock.json").write_text(json.dumps(manifest, indent=2) + "\n")
    units = Path.home() / ".config/systemd/user"
    p = units / "ai-news-rsshub.service"
    text = p.read_text()
    text = (
        "\n".join(
            "ExecStart=" + str(target / "bin/node") + " dist/index.mjs"
            if line.startswith("ExecStart=")
            else line
            for line in text.splitlines()
        )
        + "\n"
    )
    if "Environment=PATH=" not in text:
        text = text.replace(
            "[Service]",
            "[Service]\nEnvironment=PATH=" + str(target / "bin") + ":/usr/bin:/bin",
        )
    p.write_text(text)
    (ROOT / "docs/ops" / p.name).write_text(text)
    p = ROOT / "src/install_services.py"
    text = p.read_text().replace(
        "node=shutil.which('node')", "node=str(ROOT/'runtime/node/bin/node')"
    )
    p.write_text(text)
    service = f"""[Unit]
Description=Personal AI Inbox coordinated database backup
[Service]
Type=oneshot
WorkingDirectory={ROOT}
ExecStart={ROOT}/runtime/venv/bin/python {ROOT}/src/backup.py
UMask=0077
"""
    timer = """[Unit]
Description=Daily private backup of Personal AI Inbox
[Timer]
OnCalendar=*-*-* 04:15:00
Persistent=true
RandomizedDelaySec=600
[Install]
WantedBy=timers.target
"""
    for name, text in [
        ("ai-news-backup.service", service),
        ("ai-news-backup.timer", timer),
    ]:
        (units / name).write_text(text)
        (ROOT / "docs/ops" / name).write_text(text)
    subprocess.run(["systemctl", "--user", "daemon-reload"], env=ENV, check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "ai-news-backup.timer"],
        env=ENV,
        check=True,
    )
    subprocess.run(
        ["systemctl", "--user", "restart", "ai-news-rsshub"], env=ENV, check=True
    )
    print("Independent Node runtime and daily private backup timer installed", version)


if __name__ == "__main__":
    main()
