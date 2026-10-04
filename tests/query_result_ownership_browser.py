"""Refs #109: real built Reader, held synthetic responses, no production context."""
import json
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
from review_reader_harness import Harness


h = Harness("query-result-ownership")
# This suite holds the initial request; server hydration must retain its query.
h.settings["minimum_score"] = 8
# Miniflux feed DTOs include icon even when no icon has been downloaded.
h.feeds[0]["icon"] = {"feed_id": 7, "icon_id": 0}
p = h.page
pending = []
request_trace = []
fail_requests = False
p.add_init_script("""localStorage.setItem('settings',JSON.stringify({articleListLayout:'card',showStatus:'all'}));
localStorage.setItem('ai-view-state',JSON.stringify({mode:'recommended',minimum:8,sort:'score',direction:'desc',auxiliary:'none'}));
window.queryFrames=[];
window.queryFrame=()=>{
 const selected=[...document.querySelectorAll('.arco-menu-selected .custom-menu-item')].find(e=>e.getClientRects().length);
 const rows=[...document.querySelectorAll('.entry-list [data-entry-id]')].filter(e=>e.getClientRects().length);
 const item={scope:location.pathname.split('/')[2],count:selected?.querySelector('.item-count')?.textContent.trim()??'',ids:rows.map(e=>e.dataset.entryId)};
 if(window.queryFrames.length<2400)window.queryFrames.push(item);return item;
};
document.addEventListener('DOMContentLoaded',()=>new MutationObserver(()=>{window.queryFrame();requestAnimationFrame(window.queryFrame)}).observe(document.body,{subtree:true,childList:true,attributes:true,characterData:true}));
""")


def entries(start, count):
    return [{"id": start+i, "user_id": 1, "feed_id": 7, "title": f"Synthetic {start+i}",
             "url": f"https://example.test/{start+i}", "hash": str(start+i), "status": "read",
             "starred": False, "content": "<p>Isolated query ownership fixture.</p>",
             "published_at": "2026-10-04T01:00:00Z", "created_at": "2026-10-04T01:00:00Z",
             "changed_at": "2026-10-04T01:00:00Z", "enclosures": [], "feed": h.feeds[0],
             "ai": {"state": "done", "score": 9, "technical_score": 8, "business_score": 8,
                    "summary": "Synthetic", "reason": "Synthetic", "tags": []}}
            for i in range(count)]


def intercept(route, path, method):
    query = parse_qs(urlsplit(route.request.url).query)
    if method == "GET" and path.endswith("/entries") and query.get("ai_view") == ["recommended"]:
        scope = "today" if "published_after" in query else "all"
        request_trace.append({"scope": scope, "ai_min": query.get("ai_min"),
                              "offset": query.get("offset"), "limit": query.get("limit"),
                              "response": "503" if fail_requests else "held"})
        if fail_requests:
            route.fulfill(status=503, json={"detail": "synthetic read failure"})
            return True
        pending.append((scope, route))
        return True
    return False


def take(scope):
    # Event processing comes from the expected real browser request, not a sleep.
    assert pending and pending[0][0] == scope, [(name, r.request.url) for name, r in pending]
    return pending.pop(0)[1]


def go(scope):
    label = {"today": "今天", "all": "全部"}[scope]
    with p.expect_request(lambda r: "/entries?" in r.url and "ai_view=recommended" in r.url):
        p.locator('.custom-menu-item').filter(has=p.get_by_text(label, exact=True)).first.click()


def frames():
    return p.evaluate("async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);return window.queryFrames}")


def pending_is_unowned(scope):
    captured = frames()
    current = [frame for frame in captured if frame["scope"] == scope]
    h.check(scope+"_pending_has_no_old_count_or_rows", bool(current) and all(not f["count"] and not f["ids"] for f in current))
    return captured


def mark():
    p.evaluate("window.queryFrames=[]")


h.custom = intercept
trace = {}
try:
    with p.expect_request(lambda r: "/entries?" in r.url and "ai_view=recommended" in r.url):
        h.goto("/inbox/all")
    take("all").fulfill(json={"total": 1965, "entries": entries(101, 24)})
    expect(p.locator('.page-info')).to_contain_text('(1965)')
    mark(); go("today"); old_today = take("today")
    trace["today_pending"] = pending_is_unowned("today")
    mark(); go("all"); new_all = take("all")
    old_today.fulfill(json={"total": 2, "entries": entries(301, 2)})
    trace["reversed_all_pending"] = pending_is_unowned("all")
    new_all.fulfill(json={"total": 24, "entries": entries(201, 24)})
    expect(p.locator('.page-info')).to_contain_text('(24)')
    expect(p.locator('[data-entry-id="301"]')).to_have_count(0)
    h.check("late_today_cannot_replace_reversed_all", True)
    mark(); go("today"); take("today").fulfill(json={"total": 2, "entries": entries(401, 2)})
    expect(p.locator('.page-info')).to_contain_text('(2)')
    mark(); go("all"); late_all = take("all")
    go("today"); take("today").fulfill(json={"total": 0, "entries": []})
    expect(p.get_by_text("当前范围暂无达到筛选条件的 AI 精选", exact=True)).to_be_visible()
    late_all.fulfill(json={"total": 1965, "entries": entries(501, 24)})
    trace["empty_after_late_all"] = frames()
    expect(p.locator('.entry-list [data-entry-id]')).to_have_count(0)
    expect(p.locator('.page-info')).not_to_contain_text('1965')
    h.check("empty_today_survives_late_all", True)
    mark(); fail_requests = True; go("all")
    expect(p.locator('.entry-list [role=alert]')).to_be_visible()
    trace["error"] = frames()
    h.check("failed_scope_does_not_claim_successful_zero", p.evaluate("window.queryFrame().count===''"))
    h.check("no_article_mutations", not h.writes)
    p.screenshot(path=str(h.out / "final.png"))
except Exception as exc:
    h.errors.append(str(exc))
    p.screenshot(path=str(h.out / "failure.png"))
    raise
finally:
    trace["requests"] = request_trace
    trace["pending_scopes"] = [scope for scope, _ in pending]
    try:
        trace["last_frames"] = p.evaluate("window.queryFrames || []")
    except Exception as capture_error:
        trace["frame_capture_error"] = type(capture_error).__name__
    (h.out / "query-frames.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2))
    h.close()
