"""Reader detail polish: quiet AI card, readable code and safe proxy attachments."""
from pathlib import Path
import difflib
import os
import shutil

ROOT = Path(os.environ.get("AI_NEWS_ROOT", "/home/ubuntu/ai-news"))
WEB = ROOT / "upstream/reactflux"
BACK = ROOT / "runtime/reactflux-original"


def backup(name):
    target = BACK / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WEB / name, target)


def patch(name, old, new):
    path = WEB / name
    text = path.read_text()
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError("reader detail patch anchor mismatch: " + name)
    backup(name)
    path.write_text(text.replace(old, new, 1))


# These tracked overlay files are authoritative.
for name in ("AiBadge.jsx", "AiPanel.jsx", "SourceHistory.jsx", "source-history.js", "AiNews.css"):
    target = WEB / "src/components/Ai" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "patches" / name, target)

patch(
    "src/components/Article/ArticleDetail.css",
    """.article-content code {
  background-color: #282c34;
  border-radius: var(--border-radius-small);
  padding: 0.2em 0.4em;
}""",
    """.article-content :not(pre) > code {
  border: 1px solid var(--color-border-2);
  border-radius: var(--border-radius-small);
  padding: 0.12em 0.35em;
  background-color: var(--color-fill-2);
  color: var(--color-text-1);
}""",
)

patch(
    "src/utils/entry-presentation.js",
    """  const scheme = getUriScheme(url)
  const kind = getAttachmentKind(mimeType, url)
  const isEmbeddable = EMBEDDABLE_URI_SCHEMES.has(scheme)""",
    """  const sameOriginProxy = url.startsWith("/mf/proxy/")
  const scheme = sameOriginProxy ? "https" : getUriScheme(url)
  const kind = getAttachmentKind(mimeType, url)
  const isEmbeddable = sameOriginProxy || EMBEDDABLE_URI_SCHEMES.has(scheme)""",
)

diffs = []
for original in BACK.rglob("*"):
    if not original.is_file():
        continue
    relative = str(original.relative_to(BACK))
    modified = WEB / relative
    if modified.is_file():
        diffs.extend(
            difflib.unified_diff(
                original.read_text().splitlines(True),
                modified.read_text().splitlines(True),
                fromfile="a/" + relative,
                tofile="b/" + relative,
            )
        )
(ROOT / "patches/reactflux.patch").write_text("".join(diffs))
print("Reader detail quality overlay applied.")

