"""Public browser regression for reader detail quality. No model calls."""
import json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from playwright.sync_api import sync_playwright, expect
from browser_env import launch
from ops_common import client, local_admin

BASE = os.environ.get("AI_NEWS_WEB_BASE", "https://106.53.40.6").rstrip("/")
APP = BASE + "/inbox"
ROOT = Path("/home/ubuntu/ai-news")
report = {"at": time.time(), "base": BASE, "checks": []}


def check(name, passed, detail=None):
    report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})
    print(name, bool(passed), "" if passed else detail, flush=True)
    if not passed:
        raise AssertionError(name)


def login(page):
    username, password = local_admin()
    page.goto(APP + "/login", wait_until="domcontentloaded")
    page.locator("#password_input").fill(password)
    page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_url("**/today", timeout=30000)


with client() as api:
    expected = {
        6902: "The AI boom took over Climate Week",
        6921: "Adding a non-WiFi Mitsubishi AC",
        6909: "Synthetic Aperture Radar Drone",
    }
    for eid, prefix in expected.items():
        item = api.get(f"/v1/entries/{eid}").json()
        if not item.get("title", "").startswith(prefix):
            raise RuntimeError(f"production fixture {eid} changed")

with sync_playwright() as pw:
    browser = launch(pw)
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
    ctx.add_init_script("""localStorage.setItem('settings',JSON.stringify({
      ...JSON.parse(localStorage.getItem('settings')||'{}'),
      showStatus:'all',markReadOnScroll:false,pageSize:20,
      orderBy:'published_at',orderDirection:'desc'
    }))""")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    login(page)

    page.goto(APP + "/all/entry/6902", wait_until="domcontentloaded")
    body = page.locator(".article-body")
    body.wait_for(timeout=30000)
    text = body.inner_text()
    check("captured_fulltext_visible", len(text) > 2500 and "It was the best of times" in text,
          {"chars": len(text), "head": text[:120]})
    verdict = page.locator(".ai-verdict-detail")
    verdict_text = verdict.inner_text()
    check("detail_has_no_internal_model_metadata",
          all(term not in verdict_text for term in ("模型输入", "正文包含", "依据抓取", "评分是模型判断")),
          verdict_text[-300:])
    page.goto(APP + "/all/entry/6921", wait_until="domcontentloaded")
    code = page.locator(".article-body code").first
    expect(code).to_be_visible(timeout=30000)
    colors = code.evaluate("""node => {
      const s=getComputedStyle(node); return {color:s.color,background:s.backgroundColor}
    }""")
    check("inline_code_readable",
          colors["color"] != colors["background"] and colors["background"] != "rgb(40, 44, 52)",
          colors)

    page.goto(APP + "/all/entry/6909", wait_until="domcontentloaded")
    enclosure = page.locator(".article-enclosures")
    expect(enclosure).to_be_visible(timeout=30000)
    if not enclosure.get_attribute("open"):
        enclosure.locator("summary").click()
    check("proxy_attachment_not_blocked",
          page.get_by_text("不支持的链接", exact=False).count() == 0)
    preview = page.locator(".article-enclosure-preview img").first
    expect(preview).to_be_visible(timeout=30000)
    preview.scroll_into_view_if_needed()
    page.wait_for_function(
        "el => el.complete && el.naturalWidth > 20", arg=preview.element_handle(), timeout=30000
    )
    check("proxy_attachment_preview_decodes", preview.evaluate("el=>el.naturalWidth") > 20)

    page.get_by_role("button", name="关闭文章", exact=True).click()
    page.get_by_role("button", name="AI 设置 · 来源", exact=True).click()
    dialog = page.locator(".ai-dialog")
    dialog.wait_for(timeout=15000)
    page.get_by_role("button", name="资源看板", exact=True).click()
    dashboard = page.locator(".ai-dashboard")
    expect(dashboard).to_be_visible()
    for label in ("已收录文章", "AI 已完成", "长正文已抓取", "中文卡片可用", "需要处理", "磁盘已用", "内存已用", "分析数据库"):
        expect(dashboard.get_by_text(label, exact=True)).to_be_visible()
    check("reader_facing_dashboard", True)
    details = dashboard.locator("details.ai-diagnostics")
    check("diagnostics_collapsed_by_default", details.get_attribute("open") is None)
    expect(dashboard.get_by_text("网页网关 · 正常", exact=True)).to_be_visible()
    expect(dashboard.get_by_text("阅读器 · 正常", exact=True)).to_be_visible()
    expect(dashboard.get_by_text("RSSHub · 正常", exact=True)).to_be_visible()
    check("dashboard_service_health", True)
    check("no_fatal_javascript_errors", not errors, errors)
    browser.close()

report["passed"] = all(item["passed"] for item in report["checks"])
out = ROOT / "artifacts/reader-detail-quality.json"
out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({"passed": report["passed"], "checks": len(report["checks"])}, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)
