"""Install durable, privacy-minimal reading telemetry into ReactFlux."""
from pathlib import Path
import shutil

ROOT = Path("/home/ubuntu/ai-news")
WEB = ROOT / "upstream/reactflux/src"

shutil.copy2(ROOT / "patches/reading-telemetry.js", WEB / "utils/reading-telemetry.js")

article = WEB / "components/Article/ArticleDetail.jsx"
text = article.read_text()

telemetry_import = 'import { startReadingTelemetry } from "@/utils/reading-telemetry"\n'
anchor_import = 'import buildArticleImageModel from "@/utils/images"\n'
if telemetry_import not in text:
    if anchor_import not in text:
        raise RuntimeError("ArticleDetail telemetry import anchor missing")
    text = text.replace(anchor_import, anchor_import + telemetry_import, 1)

anchor_effect = """  // Focus the scrollable area when activeContent changes
  useEffect(() => {
    if (scrollContainerRef.current) {
      const scrollElement = scrollContainerRef.current.getScrollElement()
      scrollElement?.focus()
    }
  }, [activeContent.id])
"""

telemetry_effect = """
  useEffect(
    () =>
      startReadingTelemetry({
        entryId: activeContent.id,
        getEntryState: () => activeContentState.get(),
        getScrollElement: () => scrollContainerRef.current?.getScrollElement(),
      }),
    [activeContent.id],
  )
"""

if "startReadingTelemetry({" not in text:
    if anchor_effect not in text:
        raise RuntimeError("ArticleDetail telemetry effect anchor missing")
    text = text.replace(anchor_effect, anchor_effect + telemetry_effect, 1)

article.write_text(text)
print("Reading telemetry overlay applied")
