"""Frozen-time real Chromium/390px calendar consistency; every API is synthetic."""
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import expect
from review_reader_harness import Harness

import re

instant = datetime.fromisoformat("2026-10-02T01:37:00+00:00")
records = [
    (1, "2026-10-01T15:00:00Z", 9),
    (2, "2026-10-01T17:00:00Z", 9),
    (3, "2026-10-01T16:00:00Z", 9),
    (4, "2026-10-01T16:00:00.000001Z", 9),
    (5, "2026-10-01T17:00:00Z", 7),
    (6, "2026-10-01T17:00:00Z", 7),
]
for zone in ("America/Los_Angeles", "UTC", "Asia/Shanghai"):
    for width in (1440, 390):
        h = Harness("calendar-" + zone.replace("/", "-") + "-" + str(width), timezone_id=zone)
        page, queries, held_me, responses = h.page, [], [], []
        live_records = list(records)
        h.settings["minimum_score"] = 8
        h.feeds[0].update(icon={"feed_id":7,"icon_id":0}, checked_at="2026-10-01T08:00:00Z", parsing_error_count=0, parsing_error_message="", disabled=False, hide_globally=False)
        page.set_viewport_size({"width": width, "height": 900 if width > 390 else 844})
        page.clock.set_fixed_time(instant)
        page.add_init_script("localStorage.setItem('settings',JSON.stringify({showStatus:'unread',markReadOnScroll:false,articleListLayout:'list'}));localStorage.setItem('ai-view-state',JSON.stringify({mode:'recommended',minimum:8,auxiliary:'none',sort:'score',direction:'desc'}))")
        def entry(row):
            eid, publication, score = row[:3]
            return {"id": eid, "user_id": 1, "feed_id": 7, "feed": h.feeds[0],
                    "title": f"日历隔离文章 {eid}", "url": f"https://example.test/{eid}",
                    "hash": str(eid), "published_at": publication, "changed_at": publication,
                    "created_at": publication, "status": row[3] if len(row) > 3 else "unread", "starred": False,
                    "content": "<p>合成测试正文</p>", "enclosures": [], "reading_time": 1,
                    "ai": {"status": "done", "score": score, "summary_zh": "测试摘要"}}
        def intercept(route, path, method):
            if path.endswith("/me"):
                held_me.append(route)
                return True
            if path.endswith("/entries") and method == "GET":
                q = parse_qs(urlsplit(route.request.url).query)
                after = int(q.get("published_after", ["0"])[0])
                before = int(q.get("published_before", ["9999999999"])[0])
                ai = "ai_view" in q
                selected = [row for row in live_records
                            if ((datetime.fromisoformat(row[1].replace("Z", "+00:00")).timestamp() >= after)
                                if ai else (datetime.fromisoformat(row[1].replace("Z", "+00:00")).timestamp() > after))
                            and ((datetime.fromisoformat(row[1].replace("Z", "+00:00")).timestamp() <= before)
                                 if ai else (datetime.fromisoformat(row[1].replace("Z", "+00:00")).timestamp() < before))
                            and (not q.get("status") or (row[3] if len(row) > 3 else "unread") == q["status"][0])
                            and (not ai or row[2] >= int(q.get("ai_min", ["0"])[0]))]
                queries.append(q)
                responses.append((q, len(selected)))
                route.fulfill(json={"total": len(selected), "entries": [entry(row) for row in selected[:int(q.get("limit", ["24"])[0])]]})
                return True
            return False
        h.custom = intercept
        try:
            h.goto()
            expect(page.locator(".entry-list [data-entry-id]")).to_have_count(0)
            h.check("late me gates date list/count requests", not any("published_after" in q for q in queries))
            h.check("identity request held", len(held_me) == 1)
            held_me.pop().fulfill(json={"id": 1, "username": "fixture", "timezone": "Asia/Shanghai", "is_admin": True})
            expect(page.locator(".entry-list [data-entry-id]")).to_have_count(3)
            ids = page.locator(".entry-list [data-entry-id]").evaluate_all("(nodes)=>nodes.map(n=>Number(n.dataset.entryId))")
            h.check("same account IDs", ids == [2, 3, 4])
            h.check("same account total", "(3)" in page.locator(".page-info:visible").inner_text())
            h.check("effective timezone visible", "Asia/Shanghai" in page.locator(".reading-timezone:visible").inner_text())
            main = next(q for q in queries if q.get("ai_min") == ["8"] and q.get("limit") != ["1"])
            h.check("same cutoff and unread minimum8", main.get("published_after") == ["1790870400"] and main.get("status") == ["unread"])
            h.check("raw counts use same cutoff", any(q.get("published_after") == ["1790870400"] and "ai_view" not in q and q.get("limit") == ["1"] for q in queries))
            h.check("no horizontal overflow", page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
            page.screenshot(path=str(h.out / "today.png"), full_page=True)
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("document.querySelector('.reading-timezone') !== null")
            expect(page.locator(".entry-list [data-entry-id]")).to_have_count(0)
            page.wait_for_timeout(20)
            h.check("reload identity held", bool(held_me))
            held_me.pop().fulfill(json={"id": 1, "username": "fixture", "timezone": "Asia/Shanghai", "is_admin": True})
            expect(page.locator(".entry-list [data-entry-id]")).to_have_count(3)
            h.check("reload keeps cutoff and IDs", page.locator(".entry-list [data-entry-id]").evaluate_all("(nodes)=>nodes.map(n=>Number(n.dataset.entryId))") == [2, 3, 4])
            for index, event in enumerate(("focus", "pageshow", "visibilitychange")):
                boundary = datetime.fromisoformat("2026-10-02T16:00:00+00:00") + timedelta(days=index)
                base_id = 100 + index * 10
                publication = (boundary + timedelta(seconds=1)).isoformat()
                live_records.extend([(base_id, publication, 9), (base_id + 1, publication, 9),
                                     (base_id + 2, publication, 7), (base_id + 3, publication, 9, "read")])
                page.clock.set_fixed_time(boundary)
                if event == "visibilitychange":
                    page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'))")
                else:
                    page.evaluate("(event)=>window.dispatchEvent(new Event(event))", event)
                expect(page.locator(".entry-list [data-entry-id]")).to_have_count(2)
                expect(page.locator(".entry-list [data-entry-id]").first).to_have_attribute("data-entry-id", str(base_id))
                h.check(event + " new day IDs/total", page.locator(".entry-list [data-entry-id]").evaluate_all("(nodes)=>nodes.map(n=>Number(n.dataset.entryId))") == [base_id, base_id + 1]
                        and "(2)" in page.locator(".page-info:visible").inner_text())
                cutoff = str(int(boundary.timestamp()))
                h.check(event + " preserves minimum8/unread and date", any(q.get("published_after") == [cutoff] and q.get("ai_min") == ["8"] and q.get("status") == ["unread"] and total == 2 for q, total in responses))
                h.check(event + " raw count predicate", any(q.get("published_after") == [cutoff] and "ai_view" not in q and q.get("limit") == ["1"] and total == 3 for q, total in responses))
            h.check("no writes", not h.writes)
        except Exception:
            print("queries",queries,"calls",h.calls,"text",page.locator("body").inner_text(),flush=True)
            page.screenshot(path=str(h.out / "failure.png"),full_page=True)
            raise
        finally:
            h.close()

# Exercise the real picker and request/response membership; preserve existing endpoint bounds.
for zone in ("America/Los_Angeles", "UTC", "Asia/Shanghai"):
    for width in (1440, 390):
        h = Harness("calendar-picker-" + zone.replace("/", "-") + "-" + str(width), timezone_id=zone)
        page, queries, held_me, responses = h.page, [], [], []
        live_records = list(records) + [(7,"2026-10-02T15:59:59Z",9),
                                       (8,"2026-10-02T15:59:59.000001Z",9),
                                       (9,"2026-10-02T16:00:00Z",9),
                                       (10,"2026-10-02T10:00:00Z",9,"read")]
        h.feeds[0].update(icon={"feed_id":7,"icon_id":0}, checked_at="2026-10-01T08:00:00Z", parsing_error_count=0, parsing_error_message="", disabled=False, hide_globally=False)
        h.settings["minimum_score"] = 8
        page.set_viewport_size({"width":width,"height":900 if width>390 else 844})
        page.clock.set_fixed_time(instant)
        page.add_init_script("localStorage.setItem('settings',JSON.stringify({showStatus:'unread',markReadOnScroll:false,articleListLayout:'list'}));localStorage.setItem('ai-view-state',JSON.stringify({mode:'recommended',minimum:8,auxiliary:'none',sort:'score',direction:'desc'}))")
        h.custom = intercept
        try:
            h.goto('/inbox/all')
            page.wait_for_timeout(50)
            h.check("picker identity held", len(held_me)==1)
            held_me.pop().fulfill(json={"id":1,"username":"fixture","timezone":"Asia/Shanghai","is_admin":True})
            expect(page.locator(".entry-list [data-entry-id]")).to_have_count(7)
            page.get_by_role('button',name='选择日期',exact=True).click()
            popup = page.locator('.mobile-date-picker-popup:visible')
            expect(popup).to_be_visible()
            # Selecting a displayed calendar day supplies YYYY-MM-DD, never an ISO UTC instant.
            popup.locator('.arco-picker-cell-in-view').filter(has_text=re.compile(r'^2$')).click()
            expect(page.locator(".entry-list [data-entry-id]")).to_have_count(4)
            ids=page.locator(".entry-list [data-entry-id]").evaluate_all("ns=>ns.map(n=>Number(n.dataset.entryId))")
            h.check("picker AI inclusive membership",ids==[2,3,4,7])
            h.check("picker same zoned day query",any(q.get('published_after')==['1790870400'] and q.get('published_before')==['1790956799'] and q.get('ai_min')==['8'] and q.get('status')==['unread'] for q in queries))
            page.get_by_role('button',name='全部原始',exact=True).click()
            expect(page.locator(".entry-list [data-entry-id]")).to_have_count(4)
            expect(page.locator(".entry-list [data-entry-id]").nth(1)).to_have_attribute('data-entry-id','4')
            ids=page.locator(".entry-list [data-entry-id]").evaluate_all("ns=>ns.map(n=>Number(n.dataset.entryId))")
            h.check("picker raw strict membership",ids==[2,4,5,6])
            h.check("picker raw retains bounds/unread",any(q.get('published_after')==['1790870400'] and q.get('published_before')==['1790956799'] and 'ai_view' not in q and q.get('status')==['unread'] for q in queries))
            page.get_by_role('button',name='AI 精选',exact=True).click()
            expect(page.locator(".entry-list [data-entry-id]").nth(1)).to_have_attribute('data-entry-id','3')
            h.check("picker lens return preserves calendar day and min8",page.locator(".entry-list [data-entry-id]").evaluate_all("ns=>ns.map(n=>Number(n.dataset.entryId))")==[2,3,4,7])
            h.check("picker no horizontal overflow",page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
            h.check("picker no writes",not h.writes)
            page.screenshot(path=str(h.out/'picker.png'),full_page=True)
        except Exception:
            print("queries",queries,"text",page.locator("body").inner_text(),flush=True)
            print("cells",page.locator('.arco-picker-cell').evaluate_all("ns=>ns.slice(0,15).map(n=>n.outerHTML)"),flush=True)
            page.screenshot(path=str(h.out/'failure.png'),full_page=True)
            raise
        finally:
            h.close()
