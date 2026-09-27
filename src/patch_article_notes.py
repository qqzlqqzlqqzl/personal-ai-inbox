"""Install the server-backed personal article note surface into ReactFlux."""
from pathlib import Path
import shutil

ROOT = Path("/home/ubuntu/ai-news")
WEB = ROOT / "upstream/reactflux/src"

target = WEB / "components/Ai/ArticleNote.jsx"
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / "patches/ArticleNote.jsx", target)

article = WEB / "components/Article/ArticleDetail.jsx"
text = article.read_text()

note_import = 'import ArticleNote from "@/components/Ai/ArticleNote"\n'
anchor_import = 'import AiBadge from "@/components/Ai/AiBadge"\n'
if note_import not in text:
    if anchor_import not in text:
        raise RuntimeError("ArticleNote import anchor missing")
    text = text.replace(anchor_import, anchor_import + note_import, 1)

enclosures = """                <ArticleEnclosures
                  items={visibleAttachments}
                  open={enclosuresOpen}
                  onImagePreview={togglePhotoSlider}
                  onOpenChange={handleEnclosuresOpenChange}
                />
"""
with_note = enclosures + '                <ArticleNote entry={activeContent} key={activeContent.id} />\n'
if "<ArticleNote entry={activeContent}" not in text:
    if enclosures not in text:
        raise RuntimeError("ArticleNote body anchor missing")
    text = text.replace(enclosures, with_note, 1)

article.write_text(text)
print("Personal article notes overlay applied")
