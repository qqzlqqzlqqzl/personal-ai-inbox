"""Refs #109: real built Reader, held synthetic responses, no production context."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, TimeoutError as PlaywrightTimeoutError
from review_reader_harness import Harness
from query_route_queue import HeldQueryRoutes, matches_query
from query_dom_ownership import assess_pending_ui


h = Harness("query-result-ownership", timezone_id="Asia/Shanghai")
# This suite holds the initial request; server hydration must retain its query.
h.settings["minimum_score"] = 8
# Miniflux feed DTOs include icon even when no icon has been downloaded.
h.feeds[0]["icon"] = {"feed_id": 7, "icon_id": 0}
p = h.page
held_routes = HeldQueryRoutes(p, h.base)
pending = held_routes.pending
expected_requests = []
request_trace = []
raw_request_trace = []
fail_requests = False
p.add_init_script("""localStorage.setItem('settings',JSON.stringify({articleListLayout:'card',showStatus:'all'}));
localStorage.setItem('ai-view-state',JSON.stringify({mode:'recommended',minimum:8,sort:'score',direction:'desc',auxiliary:'none'}));
""")
p.add_init_script(Path(__file__).with_name("query_frame_observer.js").read_text())


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
    if method == "GET" and path.endswith("/feeds/counters"):
        route.fulfill(json={"reads": {}, "unreads": {"7": 8088}})
        return True
    if method == "GET" and path.endswith("/entries") and not query.get("ai_view"):
        raw_request_trace.append({'path': path, 'query': query})
    if method == "GET" and path.endswith("/entries") and query.get("ai_view") == ["recommended"]:
        scope = "today" if "published_after" in query else "all"
        request_trace.append({"scope": scope, "ai_min": query.get("ai_min"),
                              "offset": query.get("offset"), "limit": query.get("limit"),
                              "status": query.get("status"),
                              "response": "503" if fail_requests else "held"})
        if fail_requests:
            route.fulfill(status=503, json={"detail": "synthetic read failure"})
            return True
        held_routes.hold(scope, route)
        return True
    return False


def take(scope):
    assert expected_requests and expected_requests[0][0] == scope, 'observed request order/scope mismatch'
    route = held_routes.take(scope, expected_requests[0][1])
    expected_requests.pop(0)
    return route


def go(scope):
    label = {"today": "今天", "all": "全部"}[scope]
    with p.expect_request(lambda r: matches_query(scope, r, h.base)) as observed:
        p.locator('.custom-menu-item').filter(has=p.get_by_text(label, exact=True)).first.click()
    expected_requests.append((scope, observed.value))


def frames():
    return p.evaluate("async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);return window.queryFrames}")


def pending_is_unowned(scope):
    baseline = p.evaluate("window.queryBaseline")
    remaining = max(1, min(5000, int(p.evaluate("5000-(performance.now()-window.queryBaseline.time)"))))
    wait_timed_out = False
    try:
        # Wait for route identity only, never for old rows/counts to disappear.
        # Every earlier observation remains in the array for the strict check.
        p.wait_for_function("""scope => (window.queryFrames || []).some(frame =>
          frame.dom?.selected_count === 1 && frame.dom.selected_scope === scope &&
          frame.dom.headers?.length === 1 &&
          frame.dom.headers[0].rendered_text?.startsWith(({all:'全部',today:'今天'})[scope]+' · AI精选'))""",
                            arg=scope, timeout=remaining)
    except PlaywrightTimeoutError:
        wait_timed_out = True
    captured = frames()
    report = assess_pending_ui(scope, baseline, captured, dropped=p.evaluate("window.queryObservation.dropped"))
    if wait_timed_out:
        report["passed"] = False
        report["violations"].append({"sequence": None, "reason": "target_ui_wait_timeout"})
    trace[scope+"_ui_ownership"] = {"baseline": baseline, "assessment": report}
    h.check(scope+"_pending_has_no_old_count_or_rows", report["passed"])
    return captured


def mark():
    p.evaluate("window.queryBaseline=window.queryFrame('baseline');window.queryFrames=[]")


h.custom = intercept
trace = {}
try:
    with p.expect_request(lambda r: matches_query("all", r, h.base)) as observed:
        h.goto("/inbox/all")
    expected_requests.append(("all", observed.value))
    take("all").fulfill(json={"total": 1965, "entries": entries(101, 24)})
    expect(p.locator('.page-info')).to_contain_text('(1965)')
    expect(p.locator('[data-entry-id="101"]')).to_be_visible()
    all_sidebar_count = p.locator('.custom-menu-item').filter(has=p.get_by_text('全部', exact=True)).first.locator('.item-count')
    h.check('active_all_sidebar_has_owned_AI_total', all_sidebar_count.inner_text().strip() == '1965')
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
    h.check('settled_today_does_not_expose_native_all_total', all_sidebar_count.inner_text().strip() == '')
    trace['settled_sidebar_lens'] = {'native_unread_fixture': 8088, 'initial_AI_all_total': 1965,
                                     'settled_today_total': 0, 'inactive_all_rendered_text': all_sidebar_count.inner_text(),
                                     'show_status': 'all', 'scope': 'today', 'mode': 'recommended'}
    h.check('sidebar_count_projection_adds_no_AI_queries', len(request_trace) == 6)
    p.screenshot(path=str(h.out / 'settled-today-count-lens.png'))
    # Continue in this same viewport/context through the observed AI + unread
    # condition. Preserve the original all-status flow and restore it below.
    unread_start = len(request_trace)
    unread_radio = p.locator('.entry-panel').get_by_role('radio', name='未读', exact=True)
    all_radio = p.locator('.entry-panel').get_by_role('radio', name='全部', exact=True)
    # Arco hides the native input; click the visible label users operate.
    unread_control = p.locator('.entry-panel .arco-radio-button').filter(has=p.get_by_role('radio', name='未读', exact=True))
    all_control = p.locator('.entry-panel .arco-radio-button').filter(has=p.get_by_role('radio', name='全部', exact=True))

    def change_filter(scope, status, action):
        expected_status = ['unread'] if status == 'unread' else None
        with p.expect_request(lambda r: matches_query(scope, r, h.base) and
                              parse_qs(urlsplit(r.url).query).get('status') == expected_status) as observed:
            action()
        expected_requests.append((scope, observed.value))
        return take(scope)

    change_filter('today', 'unread', unread_control.click).fulfill(json={'total': 0, 'entries': []})
    expect(unread_radio).to_be_checked()
    go('all'); unread_all = take('all')
    assert parse_qs(urlsplit(unread_all.request.url).query).get('status') == ['unread']
    unread_entries = [{**entry, 'status': 'unread'} for entry in entries(601, 24)]
    unread_all.fulfill(json={'total': 1965, 'entries': unread_entries})
    expect(p.locator('.page-info')).to_contain_text('(1965)')
    h.check('unread_active_all_sidebar_owns_1965', all_sidebar_count.inner_text().strip() == '1965')
    p.screenshot(path=str(h.out / 'unread-all-count-lens.png'))
    # Hold an actual refresh of the old All scope, then complete Today first.
    unread_late_all = change_filter('all', 'unread',
                                   p.locator('.entry-panel').get_by_role('button', name='刷新', exact=True).click)
    go('today'); unread_today = take('today')
    assert parse_qs(urlsplit(unread_today.request.url).query).get('status') == ['unread']
    unread_today.fulfill(json={'total': 0, 'entries': []})
    expect(p.get_by_text('当前范围暂无达到筛选条件的 AI 精选', exact=True)).to_be_visible()
    unread_late_all.fulfill(json={'total': 9999, 'entries': [{**entry, 'status': 'unread'} for entry in entries(701, 24)]})
    expect(unread_radio).to_be_checked()
    expect(p.locator('.page-info')).to_contain_text('Asia/Shanghai')
    expect(p.locator('.entry-list [data-entry-id]')).to_have_count(0)
    expect(p.locator('.page-info')).not_to_contain_text('9999')
    h.check('unread_settled_today_hides_unknown_all_after_late_response', all_sidebar_count.inner_text().strip() == '')
    trace['unread_sidebar_lens'] = {'native_unread_fixture': 8088, 'initial_AI_all_total': 1965,
                                   'settled_today_total': 0, 'late_all_total': 9999,
                                   'inactive_all_rendered_text': all_sidebar_count.inner_text(),
                                   'show_status': 'unread', 'scope': 'today', 'mode': 'recommended',
                                   'timezone': 'Asia/Shanghai', 'frames': frames()}
    p.screenshot(path=str(h.out / 'unread-today-count-lens.png'))
    # The explicit raw-unread exception is also a real UI change, not a store edit.
    raw_start = len(raw_request_trace)
    with p.expect_response(lambda r: urlsplit(r.url).path == '/mf/v1/entries' and
                           not parse_qs(urlsplit(r.url).query).get('ai_view') and
                           parse_qs(urlsplit(r.url).query).get('status') == ['unread'] and
                           parse_qs(urlsplit(r.url).query).get('limit') != ['1']) as raw_response:
        p.get_by_role('button', name='全部原始', exact=True).click()
    assert raw_response.value.status == 200
    expect(p.get_by_role('button', name='全部原始', exact=True)).to_have_attribute('aria-pressed', 'true')
    expect(unread_radio).to_be_checked()
    h.check('raw_unread_can_show_native_inactive_all', all_sidebar_count.inner_text().strip() == '8088')
    trace['raw_unread_sidebar_lens'] = {'show_status': 'unread', 'scope': 'today', 'mode': 'all',
                                       'inactive_all_rendered_text': all_sidebar_count.inner_text(),
                                       'requests': raw_request_trace[raw_start:]}
    p.screenshot(path=str(h.out / 'raw-unread-sidebar-counts.png'))
    change_filter('today', 'unread', p.get_by_role('button', name='AI 精选', exact=True).click).fulfill(json={'total': 0, 'entries': []})
    expect(p.get_by_text('当前范围暂无达到筛选条件的 AI 精选', exact=True)).to_be_visible()
    h.check('return_to_AI_unread_hides_unmatched_native_count', all_sidebar_count.inner_text().strip() == '')
    change_filter('today', 'all', all_control.click).fulfill(json={'total': 0, 'entries': []})
    expect(all_radio).to_be_checked()
    expect(p.get_by_text('当前范围暂无达到筛选条件的 AI 精选', exact=True)).to_be_visible()
    h.check('unread_addition_uses_only_six_existing_list_actions', len(request_trace) - unread_start == 6)
    h.check('raw_control_uses_one_existing_raw_list_request',
            len([r for r in raw_request_trace[raw_start:] if r['query'].get('limit') != ['1']]) == 1)
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
    trace["raw_requests"] = raw_request_trace
    trace["pending_scopes"] = [scope for scope, _ in pending]
    trace["route_queue"] = {"enqueued": held_routes.added, "consumed": held_routes.taken,
                            "expected_scopes": [scope for scope, _ in expected_requests]}
    try:
        trace["last_frames"] = p.evaluate("window.queryFrames || []")
        trace["observation_diagnostics"] = p.evaluate("window.queryObservation || null")
        p.evaluate("window.stopQueryObservation?.()")
    except Exception as capture_error:
        trace["frame_capture_error"] = type(capture_error).__name__
    (h.out / "query-frames.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2))
    h.close()
