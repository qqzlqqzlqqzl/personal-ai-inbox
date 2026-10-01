"""Check actual Chromium-rendered Chinese glyph fonts, without external requests."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("runtime/cjk-font")
TEXT = "中文阅读历史缓存测试"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for width in (1440, 390):
                context = browser.new_context(viewport={"width": width, "height": 300}, locale="zh-CN")
                context.route("**/*", lambda route: route.abort())
                page = context.new_page()
                page.set_content(f'<html lang="zh-CN"><meta charset="utf-8"><body style="font:24px system-ui,sans-serif">'
                                 f'<p id="chinese">{TEXT}</p></body></html>')
                page.evaluate("document.fonts.ready")
                session = context.new_cdp_session(page)
                session.send("DOM.enable")
                session.send("CSS.enable")
                root = session.send("DOM.getDocument")["root"]["nodeId"]
                node = session.send("DOM.querySelector", {"nodeId": root, "selector": "#chinese"})["nodeId"]
                fonts = session.send("CSS.getPlatformFontsForNode", {"nodeId": node})["fonts"]
                assert sum(font["glyphCount"] for font in fonts
                           if "Noto Sans CJK" in font["familyName"] and not font["isCustomFont"]) >= len(TEXT), fonts
                page.screenshot(path=str(OUT / f"chinese-{width}.png"))
                report.append({"viewport_width": width, "text": TEXT, "rendered_fonts": fonts})
                context.close()
        finally:
            browser.close()
    (OUT / "rendered-fonts.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
