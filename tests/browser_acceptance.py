"""Real two-session browser acceptance. No mock backend; test mutations are restored."""

import sys, json, time, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from playwright.sync_api import sync_playwright, expect
from ops_common import client, local_admin
from browser_env import launch

ROOT = Path("/home/ubuntu/ai-news")
BASE = os.environ.get("AI_NEWS_WEB_BASE", "http://127.0.0.1:8092").rstrip("/")
APP = BASE + "/inbox"
PUBLIC = BASE.startswith("https://")
OUT = ROOT / "artifacts/screenshots"
OUT.mkdir(parents=True, exist_ok=True)
report = {
    "at": time.time(),
    "base_url": BASE,
    "mode": "live Chromium, independent desktop and mobile contexts",
    "checks": [],
}
errors = []


def check(name, value, detail=None):
    report["checks"].append({"name": name, "passed": bool(value), "detail": detail})
    if not value:
        raise AssertionError(name)


def login(page):
    page.goto(APP + "/login", wait_until="domcontentloaded")
    username, password = local_admin()
    if username != "qqzl":
        raise AssertionError("production login username must be qqzl")
    page.locator("#username_input").wait_for()
    expect(page.locator("#username_input")).to_have_value("qqzl")
    page.locator("#password_input").fill(password)
    page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_url("**/all", timeout=30000)
    page.locator(".grid-card-title,.card-title").first.wait_for(timeout=45000)


def panel(page):
    page.get_by_role("button", name="AI 设置 · 来源").click()
    page.locator(".ai-dialog").wait_for()
    page.get_by_label("模型 ID", exact=True).wait_for()


with client() as api:
    api.base_url = BASE + "/mf/"
    config = api.get("/v1/ai/settings").json()
    candidates = api.get("/v1/entries?ai_view=recommended&ai_min=6&limit=30").json()[
        "entries"
    ]
    target = next(
        (e for e in candidates if "BMW" in e["title"]),
        next(e for e in candidates if e.get("ai", {}).get("image_count", 0) > 0),
    )
    eid = target["id"]
    original_status = target["status"]
    original_star = target["starred"]
    try:
        api.put(
            "/v1/entries",
            json={"entry_ids": [eid], "status": "unread", "starred": False},
        ).raise_for_status()
        with sync_playwright() as p:
            browser = launch(p)
            desk = browser.new_context(
                viewport={"width": 1440, "height": 1000}, locale="zh-CN"
            )
            mobile = browser.new_context(
                viewport={"width": 390, "height": 844},
                locale="zh-CN",
                is_mobile=True,
                has_touch=True,
                device_scale_factor=1,
            )
            a = desk.new_page()
            b = mobile.new_page()
            a.on("pageerror", lambda e: errors.append("desktop: " + str(e)))
            b.on("pageerror", lambda e: errors.append("mobile: " + str(e)))
            a.goto(APP + "/login", wait_until="domcontentloaded")
            a.locator("#username_input").wait_for(timeout=15000)
            check("dedicated_login_no_server_selector", a.locator("#server_input").count() == 0)
            check("dedicated_login_default_username", a.locator("#username_input").input_value() == "qqzl")
            check("dedicated_login_no_token_input", a.locator("#token_input").count() == 0)
            check("dedicated_login_no_generic_help", a.locator(".login-help").count() == 0)
            sw_response = a.context.request.get(APP + "/sw.js")
            check(
                "service_worker_no_store",
                sw_response.ok and "no-store" in sw_response.headers.get("cache-control", ""),
                sw_response.headers.get("cache-control"),
            )
            manifest_response = a.context.request.get(APP + "/manifest.webmanifest")
            manifest = manifest_response.json()
            check("pwa_manifest_brand", manifest.get("name") == "个人信息箱", manifest)
            a.screenshot(path=str(OUT / "login.png"), full_page=True)
            login(a)
            login(b)
            check("desktop_login", a.url.endswith("/all"))
            check("mobile_login", b.url.endswith("/all"))
            a.wait_for_timeout(2500)
            b.wait_for_timeout(2500)
            count = a.locator(".grid-card-title,.card-title").count()
            check("real_ai_cards", count > 0, {"visible_cards": count})
            check(
                "card_reason", a.locator(".ai-reason").first.inner_text().strip() != ""
            )
            a.screenshot(path=str(OUT / "desktop-inbox.png"), full_page=True)
            b.screenshot(path=str(OUT / "mobile-inbox.png"), full_page=True)
            overflow = b.evaluate(
                "document.documentElement.scrollWidth > innerWidth + 2"
            )
            check("mobile_no_horizontal_overflow", not overflow)
            a.goto(APP + f"/all/entry/{eid}", wait_until="domcontentloaded")
            a.locator(".article-body").wait_for(timeout=30000)
            a.wait_for_timeout(800)
            a.on(
                "response",
                lambda r: (
                    print("ENTRY_WRITE", r.status, r.request.post_data)
                    if r.request.method == "PUT" and r.url == BASE + "/mf/v1/entries"
                    else None
                ),
            )
            if a.get_by_role("button", name="标记为已读", exact=True).count():
                a.get_by_role("button", name="标记为已读", exact=True).click()
            expect(
                a.get_by_role("button", name="标记为未读", exact=True)
            ).to_be_visible(timeout=15000)
            a.get_by_role("button", name="收藏", exact=True).click()
            expect(a.get_by_role("button", name="取消收藏", exact=True)).to_be_visible(
                timeout=15000
            )
            for _ in range(50):
                current = api.get(f"/v1/entries/{eid}").json()
                if current["status"] == "read" and current["starred"]:
                    break
                time.sleep(0.1)
            print(
                "PERSISTED_STATE", current["id"], current["status"], current["starred"]
            )
            check("desktop_read_persisted", current["status"] == "read")
            check("desktop_star_persisted", current["starred"] is True)
            b.goto(APP + "/history", wait_until="domcontentloaded")
            expect(
                b.locator(f'[data-entry-id="{eid}"]').first
            ).to_be_visible(timeout=30000)
            check("read_visible_in_independent_session_history", True)
            b.goto(APP + f"/all/entry/{eid}", wait_until="domcontentloaded")
            expect(b.get_by_role("button", name="取消收藏", exact=True)).to_be_visible(
                timeout=30000
            )
            check("star_visible_in_independent_session", True)
            check(
                "original_link",
                a.locator(".article-title a").get_attribute("href") == target["url"],
            )
            text = a.locator(".article-body").inner_text()
            check(
                "full_article_readable",
                len(text) > 500,
                {"body_chars": len(text), "entry_id": eid},
            )
            article_images = a.locator(".article-body img")
            check("real_article_has_image", article_images.count() > 0)
            article_images.first.scroll_into_view_if_needed()
            a.wait_for_function(
                "Array.from(document.querySelectorAll('.article-body img')).some(x=>x.complete && x.naturalWidth>100)",
                timeout=25000,
            )
            check("real_article_image_loaded", True)
            check(
                "ai_evidence_in_detail",
                a.locator(".ai-verdict-detail blockquote").inner_text().strip() != "",
            )
            a.screenshot(path=str(OUT / "desktop-article.png"), full_page=True)
            b.screenshot(path=str(OUT / "mobile-article.png"), full_page=True)
            a.locator(".article-body img").first.scroll_into_view_if_needed()
            a.screenshot(path=str(OUT / "desktop-reading-body.png"), full_page=True)
            b.locator(".article-body img").first.scroll_into_view_if_needed()
            b.screenshot(path=str(OUT / "mobile-reading-body.png"), full_page=True)
            b.reload(wait_until="domcontentloaded")
            expect(b.get_by_role("button", name="取消收藏", exact=True)).to_be_visible(
                timeout=30000
            )
            check("deep_link_reload", b.url.endswith(f"/entry/{eid}"))
            a.get_by_role("button", name="关闭文章", exact=True).click()
            panel(a)
            a.screenshot(path=str(OUT / "desktop-settings.png"), full_page=True)
            changed = 7 if config["minimum_score"] != 7 else 6
            a.get_by_label("默认最低推荐分", exact=True).fill(str(changed))
            a.get_by_role("button", name="保存到服务器", exact=True).click()
            expect(a.locator(".ai-dialog").get_by_role("status")).to_contain_text(
                "已保存到服务器", timeout=15000
            )
            b.get_by_role("button", name="关闭文章", exact=True).click()
            panel(b)
            expect(b.get_by_label("默认最低推荐分", exact=True)).to_have_value(
                str(changed), timeout=10000
            )
            check("preferences_cross_device", True)
            b.screenshot(path=str(OUT / "mobile-settings.png"), full_page=True)
            a.get_by_role("button", name="来源目录", exact=True).click()
            expect(
                a.locator(".ai-source-list").get_by_text("已订阅", exact=False).first
            ).to_be_visible(timeout=10000)
            check("source_catalog_live_status", True)
            a.screenshot(path=str(OUT / "desktop-sources.png"), full_page=True)
            check("tools_tab_removed", a.get_by_role("button", name="工具入口", exact=True).count() == 0)
            b.get_by_role("button", name="关闭", exact=True).click()
            registration = a.evaluate("""async () => {
                const reg = await navigator.serviceWorker.ready;
                return {scope: reg.scope, script: reg.active?.scriptURL};
            }""")
            check("service_worker_scoped_to_inbox", registration == {
                "scope": APP + "/", "script": APP + "/sw.js"
            }, registration)
            all_scopes = a.evaluate("async () => (await navigator.serviceWorker.getRegistrations()).map(r => r.scope)")
            check("no_root_service_worker", all(s == APP + "/" for s in all_scopes), all_scopes)
            if PUBLIC:
                response = a.goto(BASE + "/news/", wait_until="domcontentloaded")
                check("legacy_news_redirects_to_inbox", response.ok and a.url.rstrip("/") == APP,
                      {"status": response.status, "url": a.url})
                response = a.goto(BASE + "/deployment", wait_until="domcontentloaded")
                check("unpublished_status_remains_404", response.status == 404)
            else:
                a.goto(BASE + "/deployment", wait_until="domcontentloaded")
                check("service_worker_does_not_hijack_status", "部署状态" in a.locator("h1").inner_text())
            check("outside_inbox_not_controlled", a.evaluate("navigator.serviceWorker.controller === null"))
            check("no_fatal_javascript_errors", not errors, errors)
            browser.close()
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)[:800]
    finally:
        api.put(
            "/v1/entries",
            json={
                "entry_ids": [eid],
                "status": original_status,
                "starred": original_star,
            },
        ).raise_for_status()
        api.put("/v1/ai/settings", json=config).raise_for_status()
        report["test_mutations_restored"] = True
report["passed"] = not report.get("error") and all(
    x["passed"] for x in report["checks"]
)
(ROOT / ("artifacts/browser-acceptance-public.json" if PUBLIC else "artifacts/browser-acceptance.json")).write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False))
sys.exit(0 if report["passed"] else 1)
