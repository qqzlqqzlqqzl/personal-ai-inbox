"""Isolated built-reader history QA; every API request is mocked, no live data.

Usage: AI_NEWS_TEST_BUILD=runtime/history-build python tests/history_browser_acceptance.py
Requires a built /inbox/ reader, Playwright, and CHROMIUM_EXECUTABLE (default chromium).
"""
import functools
import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
build_value = os.environ.get("AI_NEWS_TEST_BUILD")
if not build_value:
    raise SystemExit("Set AI_NEWS_TEST_BUILD to an isolated reader build; live URLs are not accepted")
BUILD = Path(build_value).resolve()
if not (BUILD / "index.html").is_file():
    raise SystemExit("Reader build/index.html is missing")


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path.removeprefix("/inbox/")
        self.path = "/" + path if (BUILD / path).is_file() else "/index.html"
        super().do_GET()

    def log_message(self, *_):
        pass


def fixture_history():
    return {
        "feed_id": 7, "archive_complete": False,
        "stored": {"state": "ok", "count": 120, "oldest_published_at": "2020-01-01T00:00:00Z",
                   "newest_published_at": "2026-09-22T00:00:00Z", "checked_at": "2026-09-30T00:00:00Z"},
        "feed_window": {"state": "ok", "count": 4, "dated_count": 3, "undated_count": 1,
                        "updated_fallback_count": 1, "oldest_at": "2026-09-20T00:00:00Z",
                        "newest_at": "2026-09-22T00:00:00Z", "span_days": 2,
                        "checked_at": "2026-09-30T00:00:00Z", "cached": False},
    }


server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(BUILD)))
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f"http://127.0.0.1:{server.server_port}"
report = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE", "/usr/bin/chromium"),
                                    headless=True, args=["--no-sandbox"],
                                    env={**os.environ, "HOME": str(ROOT / "runtime/browser-test-home")})
        for name, width in [("desktop", 1440), ("mobile", 390)]:
            context = browser.new_context(viewport={"width": width, "height": 900}, locale="zh-CN", service_workers="block")
            context.add_init_script("localStorage.setItem('auth', JSON.stringify({server: location.origin + '/mf', token: 'test-only', username:'', password:''}))")
            pending, calls, errors = [], [], []
            mode = {"value": "success"}
            feed = {"id": 7, "user_id": 1, "title": "Manual source", "feed_url": "https://example.org/feed",
                    "site_url": "https://example.org", "category": {"id": 1, "title": "技术博客"}}

            def api_route(route, calls=calls, mode=mode, pending=pending, feed=feed):
                path = urlsplit(route.request.url).path
                calls.append(path)
                if path.endswith("/history"):
                    if mode["value"] == "pending":
                        pending.append(route)
                        return
                    if mode["value"] == "error":
                        route.fulfill(status=502, json={"error_message": "test failure"})
                        return
                    body = fixture_history()
                elif path.endswith("/ai/settings"):
                    body = {"enabled": False, "translation_enabled": False, "base_url": "https://example.org", "model": "test-model", "prompt": "test", "minimum_score": 6, "daily_articles": 80, "daily_tokens": 500000, "max_chars": 40000, "json_mode": True}
                elif path.endswith("/ai/status"):
                    body = {"counts": {}, "coverage": {}, "usage": [], "events": [], "kaggle": {}, "resources": {}}
                elif path.endswith("/ai/catalog"):
                    body = [{"name": feed["title"], "url": feed["feed_url"], "category": "技术博客", "status": "subscribed", "subscribed": True, "feed_id": 7}]
                elif path.endswith("/ai/x/roster"):
                    body = {"counts": {"total": 0, "timeline_nonempty": 0, "timeline_empty": 0, "empty_with_fallback": 0}, "sources": []}
                elif path.endswith("/me"):
                    body = {"id": 1, "username": "test", "is_admin": True}
                elif path.endswith("/categories"):
                    body = [{"id": 1, "title": "技术博客"}]
                elif path.endswith("/feeds/counters"):
                    body = {"reads": {}, "unreads": {}}
                elif path.endswith("/feeds"):
                    body = [feed]
                elif path.endswith("/entries"):
                    body = {"total": 0, "entries": []}
                else:
                    body = {}
                route.fulfill(json=body)

            context.route("**/mf/**", api_route)
            # No external requests (including version checks) leave this harness.
            context.route(lambda url: not url.startswith(base), lambda route: route.abort())
            page = context.new_page()
            page.on("pageerror", lambda err, errors=errors: errors.append(str(err)))
            page.goto(base + "/inbox/today")
            page.get_by_role("button", name="AI 设置 · 来源", exact=True).click()
            page.get_by_role("button", name="来源目录", exact=True).click()
            expect(page.get_by_text("Manual source", exact=True)).to_be_visible()
            assert not any(path.endswith("/history") for path in calls), "No eager feed probes"
            summary = page.locator(".ai-source-history summary")
            summary.click()
            expect(page.get_by_text("RSS/Atom 本次暴露 4 条", exact=True)).to_be_visible()
            expect(page.get_by_text("最旧 published_at：2020-01-01 00:00:00 UTC", exact=True)).to_be_visible()
            expect(page.get_by_text("最新 published_at：2026-09-22 00:00:00 UTC", exact=True)).to_be_visible()
            expect(page.get_by_text("1 条缺少有效发布时间，范围采用其更新时间", exact=True)).to_be_visible()
            assert len([path for path in calls if path.endswith("/history")]) == 1
            summary.click(); summary.click()
            assert len([path for path in calls if path.endswith("/history")]) == 1, "Reopening reuses displayed result"
            assert page.locator(".ai-dialog").evaluate("el => el.scrollWidth <= el.clientWidth"), "Dialog must not overflow horizontally"
            screenshot = ROOT / "runtime" / f"history-{name}.png"
            page.screenshot(path=str(screenshot), full_page=True)

            mode["value"] = "pending"
            refresh = page.get_by_role("button", name="刷新历史范围", exact=True)
            refresh.click()
            expect(refresh).to_be_disabled()
            # Even scripted repeated clicks cannot queue duplicate in-flight work.
            refresh.dispatch_event("click")
            expect(page.get_by_text("正在查询存储与 feed 窗口……", exact=True)).to_be_visible()
            assert len(pending) == 1
            pending.pop().fulfill(json=fixture_history())
            expect(refresh).to_be_enabled()

            mode["value"] = "error"
            refresh.click()
            expect(page.get_by_text("历史范围查询失败，请重试", exact=True)).to_be_visible()
            expect(page.get_by_text("RSS/Atom 本次暴露 4 条", exact=True)).to_have_count(0)
            mode["value"] = "success"
            refresh.click()
            expect(page.get_by_text("RSS/Atom 本次暴露 4 条", exact=True)).to_be_visible()

            # Dismiss while pending, reopen the panel, and verify no stale result.
            mode["value"] = "pending"
            refresh.click()
            expect(refresh).to_be_disabled()
            page.get_by_role("button", name="关闭", exact=True).click()
            expect(page.locator(".ai-dialog")).to_have_count(0)
            pending.clear()
            mode["value"] = "success"
            page.get_by_role("button", name="AI 设置 · 来源", exact=True).click()
            page.get_by_role("button", name="来源目录", exact=True).click()
            expect(page.get_by_text("RSS/Atom 本次暴露 4 条", exact=True)).to_have_count(0)
            page.locator(".ai-source-history summary").click()
            expect(page.get_by_text("RSS/Atom 本次暴露 4 条", exact=True)).to_be_visible()
            page.keyboard.press("Escape")
            expect(page.locator(".ai-dialog")).to_have_count(0)
            assert not errors, errors
            report.append({"viewport": name, "passed": True, "checks": ["lazy query", "stored bounds/count", "feed window/date caveats", "collapse/reopen", "no overflow", "duplicate clicks", "failure/retry", "pending close/reopen", "Escape"], "screenshot": str(screenshot)})
            context.close()
        browser.close()
finally:
    server.shutdown()
    server.server_close()
print(json.dumps(report, ensure_ascii=False, indent=2))
