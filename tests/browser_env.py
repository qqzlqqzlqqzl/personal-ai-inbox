"""Project-local browser dependencies, with no system-wide library changes."""

import os
from pathlib import Path

ROOT = Path("/home/ubuntu/ai-news")


def prepare():
    libs = ROOT / "runtime/browser-libs/usr/lib/x86_64-linux-gnu"
    os.environ["LD_LIBRARY_PATH"] = str(libs)
    fontconfig = ROOT / "runtime/browser-fonts.conf"
    fontconfig.write_text(
        '<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        f"<dir>{ROOT}/runtime/browser-libs/usr/share/fonts</dir><dir>/usr/share/fonts</dir>"
        f"<cachedir>{ROOT}/runtime/browser-font-cache</cachedir></fontconfig>"
    )
    os.environ["FONTCONFIG_FILE"] = str(fontconfig)
    override = os.environ.get("CHROMIUM_EXECUTABLE")
    if override:
        if not Path(override).is_file():
            raise RuntimeError("Specified Chromium does not exist")
        return override
    candidates = list(
        Path("/home/ubuntu/.claude-server-commander/puppeteer-cache/chrome").glob(
            "*/chrome-linux64/chrome"
        )
    )
    if not candidates:
        raise RuntimeError("Chromium executable not installed")
    return str(sorted(candidates)[-1])


def launch(playwright):
    executable = prepare()
    return playwright.chromium.launch(
        executable_path=executable,
        headless=True,
        args=["--no-sandbox"],
        env=dict(os.environ),
    )
