"""Public browser acceptance for scope × AI interaction. One unread state is restored."""
import json, os, re, sys, time, traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from playwright.sync_api import sync_playwright, expect
from browser_env import launch
from ops_common import client, local_admin

BASE=os.environ.get("AI_NEWS_WEB_BASE","https://106.53.40.6").rstrip("/")
APP=BASE+"/inbox"
OUT=Path("/home/ubuntu/ai-news/artifacts/scope-ai")
OUT.mkdir(parents=True,exist_ok=True)
report={"at":time.time(),"base":BASE,"checks":[]}
errors=[]
restores={}

def ck(name, ok, detail=None):
    report["checks"].append({"name":name,"passed":bool(ok),"detail":detail})
    print(name, bool(ok), detail if not ok else "", flush=True)
    if not ok: raise AssertionError(name)

def qdict(url):
    return parse_qs(urlsplit(url).query)

def visible_sidebar_count(locator):
    match=re.search(r'text:\s+"?(\d+)"?',locator.aria_snapshot())
    return int(match.group(1)) if match else 0

def wait_sidebar_count(page, locator, expected):
    for _ in range(60):
        actual=visible_sidebar_count(locator)
        if actual==expected:
            return
        page.wait_for_timeout(250)
    raise AssertionError(f"sidebar count expected {expected}, got {actual}")

def is_list_response(response, path, ai_view=None):
    if urlsplit(response.url).path != path: return False
    q=qdict(response.url)
    return (ai_view is None and "ai_view" not in q) or q.get("ai_view")==[ai_view]

def login(page):
    user,pw=local_admin()
    page.goto(APP+"/login",wait_until="domcontentloaded")
    page.locator("#password_input").fill(pw)
    page.get_by_role("button",name="登录",exact=True).click()
    page.wait_for_url("**/today",timeout=30000)
    page.locator(".page-info").wait_for(timeout=40000)

with client() as api:
    feeds=api.get("/v1/feeds").json()
    categories=api.get("/v1/categories").json()
    feed=None
    for candidate in feeds:
        r=api.get(f'/v1/feeds/{candidate["id"]}/entries?ai_view=recommended&ai_min=0&limit=1&direction=desc')
        if r.status_code==200 and r.json().get("total",0)>=30:
            feed=candidate
            break
    if not feed: raise RuntimeError("No feed with enough AI entries for pagination")
    category=next(c for c in categories if c["id"]==feed["category"]["id"])
    feed_id=feed["id"]; category_id=category["id"]

    # Pick a reversible single unread result for the bulk-read path.
    minimum=int(api.get("/v1/ai/settings").json()["minimum_score"])
    unread=api.get(f"/v1/feeds/{feed_id}/entries?ai_view=recommended&ai_min={minimum}&status=unread&limit=24&direction=desc").json()["entries"]
    target=None; term=None
    for e in unread:
        title=e["title"].strip()
        for width in (60,45,30):
            candidate=title[:width]
            if len(candidate)<8: continue
            r=api.get(f"/v1/feeds/{feed_id}/entries",params={
                "ai_view":"recommended","ai_min":minimum,"status":"unread","search":candidate,
                "limit":5,"direction":"desc"
            })
            if r.status_code==200 and r.json().get("total")==1:
                target=e; term=candidate; break
        if target: break
    if target:
        raw=api.get(f'/v1/entries/{target["id"]}').json()
        restores[target["id"]]=(raw["status"],raw["starred"])

    try:
        with sync_playwright() as pw:
            browser=launch(pw)
            ctx=browser.new_context(
                viewport={"width":1440,"height":1000}, locale="zh-CN",
                timezone_id="Asia/Singapore", service_workers="block"
            )
            ctx.add_init_script("""() => {
              localStorage.setItem('settings',JSON.stringify({
                ...JSON.parse(localStorage.getItem('settings')||'{}'),
                homePage:'all',showStatus:'all',markReadOnScroll:false,pageSize:20,
                skipMarkAllReadConfirmation:true,orderBy:'published_at',orderDirection:'desc'
              }))
              localStorage.setItem('ai-view-state',JSON.stringify({
                mode:'all',auxiliary:'none',minimum:__MINIMUM__,sort:'time',direction:'desc',hydrated:true
              }))
            }""".replace("__MINIMUM__",str(minimum)))
            page=ctx.new_page()
            page.on("pageerror",lambda e: errors.append(str(e)))
            login(page)
            expect(page.locator(".page-info")).to_contain_text("今天 · AI精选")
            ck("today_ai_default",page.url.endswith("/today"))
            ck("legacy_all_lens_migrated",
               page.get_by_role("button",name="AI 精选",exact=True).get_attribute("aria-pressed")=="true")

            # Today is a natural local day and keeps AI.
            with page.expect_response(lambda r:is_list_response(r,"/mf/v1/entries","recommended") and "published_after" in qdict(r.url),timeout=30000) as pending:
                page.goto(APP+"/today",wait_until="domcontentloaded")
            today_response=pending.value
            q=qdict(today_response.url)
            midnight=int(datetime.now(ZoneInfo("Asia/Singapore")).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
            ck("today_keeps_ai",page.url.endswith("/today") and "今天 · AI精选" in page.locator(".page-info").inner_text())
            ck("today_uses_local_midnight",int(q["published_after"][0])==midnight,{"sent":q.get("published_after"),"expected":midnight})
            page.wait_for_timeout(1200)
            page_info=page.locator(".page-info").inner_text()
            ai_match=re.search(r"\((\d+)\)",page_info)
            ai_today_total=int(ai_match.group(1)) if ai_match else 0
            selected_count=page.locator(".arco-menu-selected .item-count").first
            wait_sidebar_count(page,selected_count,ai_today_total)
            ck("today_sidebar_matches_ai_total",True,{"total":ai_today_total,"page_info":page_info})
            with page.expect_response(lambda r:is_list_response(r,"/mf/v1/entries",None) and "published_after" in qdict(r.url),timeout=30000) as raw_pending:
                page.get_by_role("button",name="全部原始",exact=True).click()
            raw_today_total=raw_pending.value.json()["total"]
            wait_sidebar_count(page,selected_count,raw_today_total)
            with page.expect_response(lambda r:is_list_response(r,"/mf/v1/entries","recommended") and "published_after" in qdict(r.url),timeout=30000) as ai_pending:
                page.get_by_role("button",name="AI 精选",exact=True).click()
            restored_ai_total=ai_pending.value.json()["total"]
            wait_sidebar_count(page,selected_count,restored_ai_total)
            ck("today_sidebar_restores_ai_total_after_raw_toggle",
               restored_ai_total==ai_today_total,
               {"ai":ai_today_total,"raw":raw_today_total,"restored":restored_ai_total,
                "initial_query":qdict(today_response.url),"restored_query":qdict(ai_pending.value.url)})

            # Category and feed preserve the lens instead of redirecting /all.
            category_path=f"/mf/v1/categories/{category_id}/entries"
            with page.expect_response(lambda r:is_list_response(r,category_path,"recommended"),timeout=30000):
                page.goto(APP+f"/category/{category_id}",wait_until="domcontentloaded")
            page.locator(".article-entry").first.wait_for(timeout=30000)
            ck("category_keeps_ai",page.url.endswith(f"/category/{category_id}") and "AI精选" in page.locator(".page-info").inner_text())

            feed_path=f"/mf/v1/feeds/{feed_id}/entries"
            with page.expect_response(lambda r:is_list_response(r,feed_path,"recommended") and qdict(r.url).get("offset",["0"])==["0"],timeout=30000) as first:
                page.goto(APP+f"/feed/{feed_id}",wait_until="domcontentloaded")
            first_data=first.value.json()
            page.locator(".article-entry").first.wait_for(timeout=30000)
            ck("feed_keeps_ai",page.url.endswith(f"/feed/{feed_id}") and "AI精选" in page.locator(".page-info").inner_text(),feed["title"])
            expect(page.locator(".page-info")).to_contain_text(f"({first_data['total']})", timeout=15000)
            ck("feed_ai_count_is_scoped", True, first_data["total"])

            # Real reading scroll triggers quarter-batch AI offset pagination.
            if first_data["total"]>24:
                seen_more=[]
                def watch_feed(response):
                    if is_list_response(response,feed_path,"recommended"):
                        query=qdict(response.url)
                        if int(query.get("offset",["0"])[0])>=24:
                            seen_more.append(query)
                page.on("response",watch_feed)
                page.evaluate("""() => {
                    const e=document.querySelector('.article-entry');
                    let root=e?.parentElement;
                    while(root && !(root.scrollHeight>root.clientHeight+10 && ['auto','scroll'].includes(getComputedStyle(root).overflowY))) root=root.parentElement;
                    if(root) root.scrollTop=Math.max(root.clientHeight,root.scrollHeight*0.38);
                }""")
                for _ in range(100):
                    if seen_more: break
                    page.wait_for_timeout(150)
                ck("feed_ai_load_more_uses_offset",bool(seen_more),seen_more[:1])

            # AI sorting has its own direction and does not mutate native article ordering.
            page.get_by_label("文章排序",exact=True).select_option("score_asc")
            expect(page.get_by_label("文章排序",exact=True)).to_have_value("score_asc")
            with page.expect_response(lambda r:is_list_response(r,feed_path,None),timeout=30000):
                page.get_by_role("button",name="全部原始",exact=True).click()
            raw_sort=page.get_by_label("文章排序",exact=True).input_value()
            ck("ai_sort_does_not_leak_to_raw",not raw_sort.startswith("score_"),raw_sort)
            with page.expect_response(lambda r:is_list_response(r,feed_path,"recommended"),timeout=30000):
                page.get_by_role("button",name="AI 精选",exact=True).click()
            expect(page.get_by_label("文章排序",exact=True)).to_have_value("score_asc")
            ck("ai_sort_restored_separately",True)

            # Search copy reflects the AI backend, not native full-body query syntax.
            page.get_by_role("button",name="搜索",exact=True).click()
            ai_input=page.get_by_label("搜索标题、AI摘要、理由和标签",exact=True)
            expect(ai_input).to_be_visible()
            ck("ai_search_copy_truthful",True)
            page.get_by_role("button",name="取消",exact=True).click()

            # Pending and notes are auxiliary filters and keep scope.
            with page.expect_response(lambda r:is_list_response(r,feed_path,"pending"),timeout=30000):
                page.get_by_role("button",name="待处理 / 异常",exact=True).click()
            ck("pending_keeps_feed_scope",page.url.endswith(f"/feed/{feed_id}"))
            page.get_by_role("button",name="待处理 / 异常",exact=True).click()
            with page.expect_response(
                lambda r:is_list_response(r,feed_path,"recommended") and qdict(r.url).get("has_note")==["true"],
                timeout=30000,
            ):
                page.get_by_role("button",name="有笔记",exact=True).click()
            ck("notes_intersect_ai_and_keep_scope",
               page.url.endswith(f"/feed/{feed_id}") and "AI精选 · 有笔记" in page.locator(".page-info").inner_text())
            page.get_by_role("button",name="有笔记",exact=True).click()

            # Filtered bulk read: one real unread entry, restored in finally.
            if target:
                page.get_by_role("button",name="搜索",exact=True).click()
                page.get_by_label("搜索标题、AI摘要、理由和标签",exact=True).fill(term)
                with page.expect_response(lambda r:is_list_response(r,feed_path,"recommended") and qdict(r.url).get("search")==[term],timeout=30000):
                    page.get_by_role("button",name="确定",exact=True).click()
                page.locator(f'[data-entry-id="{target["id"]}"]').wait_for(timeout=30000)
                mark=page.get_by_role("button",name="标记当前订阅源筛选出的文章为已读",exact=True)
                expect(mark).to_be_visible()
                mark.click()
                confirm=page.locator(".mark-all-read-popconfirm")
                if confirm.is_visible():
                    confirm.locator(".arco-btn-primary").click()
                for _ in range(60):
                    if api.get(f'/v1/entries/{target["id"]}').json()["status"]=="read": break
                    time.sleep(.1)
                ck("filtered_bulk_read_hits_target",api.get(f'/v1/entries/{target["id"]}').json()["status"]=="read",target["id"])
            else:
                report["checks"].append({"name":"filtered_bulk_read_hits_target","passed":True,"detail":"skipped:no unique unread candidate"})

            page.get_by_role("button",name="AI 设置 · 来源",exact=True).click()
            page.locator(".ai-dialog").wait_for()
            ck("tools_tab_removed",page.get_by_role("button",name="工具入口",exact=True).count()==0)
            ck("no_fatal_javascript",not errors,errors)
            browser.close()
    except Exception as exc:
        report["error"]=type(exc).__name__+": "+str(exc)[:1200]
        report["traceback"]=traceback.format_exc(limit=4)[:2500]
    finally:
        for eid,(status,starred) in restores.items():
            api.put("/v1/entries",json={"entry_ids":[eid],"status":status,"starred":starred}).raise_for_status()
        report["restored_entry_states"]=True

report["passed"]=not report.get("error") and all(c["passed"] for c in report["checks"])
(OUT/"browser.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({"passed":report["passed"],"checks":len(report["checks"]),"error":report.get("error")},ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

