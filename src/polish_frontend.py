"""Final, reproducible UX corrections on top of the pinned reader overlay."""

from pathlib import Path
import difflib, shutil, tarfile

ROOT = Path("/home/ubuntu/ai-news")
WEB = ROOT / "upstream/reactflux"


def replace(path, old, new):
    p = WEB / path
    s = p.read_text()
    if old in s:
        p.write_text(s.replace(old, new))
    elif new not in s:
        raise RuntimeError("Missing patch anchor: " + path)


# Keep a pristine reference for every touched upstream file.
with tarfile.open(ROOT / "runtime/reactflux.tar.gz") as archive:
    prefix = archive.getnames()[0].split("/")[0]
    for name in [
        "src/pages/Login.jsx",
        "src/store/settingsState.js",
        "index.html",
        "vite.config.js",
        "src/components/Sidebar/Sidebar.jsx",
    ]:
        original = ROOT / "runtime/reactflux-original" / name
        if not original.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(archive.extractfile(prefix + "/" + name).read())
replace(
    "vite.config.js",
    "        cleanupOutdatedCaches: true,",
    "        cleanupOutdatedCaches: true,\n        navigateFallbackDenylist: [/^\\/mf(?:\\/|$)/, /^\\/(?:healthz|readyz|deployment)(?:\\/|$)/],",
)
replace(
    "src/components/Sidebar/Sidebar.jsx",
    '<span className="home-brand-title">ReactFlux</span>',
    '<span className="home-brand-title">个人信息箱</span>',
)
replace(
    "src/pages/Login.jsx",
    'Object.fromEntries(searchParams).username ? "user" : "token"',
    '"user"',
)
replace(
    "src/store/settingsState.js",
    "createDefaultSettings(getBrowserLanguage())",
    'createDefaultSettings("zh-CN")',
)
replace("index.html", "<title>ReactFlux</title>", "<title>个人信息收集箱</title>")
for name in ["AiBadge.jsx", "AiToolbar.jsx", "AiPanel.jsx", "AiNews.css"]:
    shutil.copy2(ROOT / "patches" / name, WEB / "src/components/Ai" / name)
shutil.copy2(ROOT / "patches/aiState.js", WEB / "src/store/aiState.js")
diffs = []
for original in (ROOT / "runtime/reactflux-original").rglob("*"):
    if original.is_file():
        relative = str(original.relative_to(ROOT / "runtime/reactflux-original"))
        diffs.extend(
            difflib.unified_diff(
                original.read_text().splitlines(True),
                (WEB / relative).read_text().splitlines(True),
                fromfile="a/" + relative,
                tofile="b/" + relative,
            )
        )
(ROOT / "patches/reactflux.patch").write_text("".join(diffs))
print("Reader UX polish applied without replacing upstream reading components")
