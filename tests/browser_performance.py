"""Real Chromium checks for deferred bodies, pagination, covers and X failure UI."""
import sys,os,json,time,re
from pathlib import Path
from urllib.parse import urlsplit,parse_qs,urlencode
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from playwright.sync_api import sync_playwright,expect
from browser_env import launch
from ops_common import local_admin,client
ROOT=Path(__file__).resolve().parents[1]
BASE=os.environ.get("AI_NEWS_WEB_BASE","https://106.53.40.6")
report={"at":time.time(),"base":BASE,"vantage":"Chromium on production server through public HTTPS", "checks":[]}
def check(name,ok,detail=None):
 report["checks"].append({"name":name,"passed":bool(ok),"detail":detail})
 if not ok:raise AssertionError(name)
entry=None
try:
 p=sync_playwright().start()
 with client(timeout=25) as api:
  browser=launch(p)
  context=browser.new_context(viewport={"width":1440,"height":1000},locale="zh-CN")
  page=context.new_page(); page.set_default_timeout(30000)
  responses=[];errors=[]
  page.on("response",lambda r:responses.append(r))
  page.on("pageerror",lambda e:errors.append(str(e)))
  page.goto(BASE+"/inbox/login",wait_until="domcontentloaded")
  user,password=local_admin();page.locator("#password_input").fill(password)
  page.get_by_role("button",name="登录",exact=True).click()
  page.wait_for_url("**/all");page.locator(".grid-card-title,.card-title").first.wait_for()
  responses.clear();start=time.perf_counter();page.reload(wait_until="domcontentloaded")
  page.locator(".grid-card-title,.card-title").first.wait_for()
  report["reload_to_first_cards_ms"]=round((time.perf_counter()-start)*1000,1)
  listing=next(r for r in responses if "/v1/entries?" in r.url and "ai_view=recommended" in r.url)
  data=listing.json();params=parse_qs(urlsplit(listing.url).query)
  check("PERF-01-deferred-list",all(e.get("content_deferred") and not e.get("content") for e in data["entries"]),{"bytes":len(listing.body()),"entries":len(data["entries"])})
  check("PERF-04-initial-24",len(data["entries"])==min(24,data["total"]))
  # A valid short post may have >100 HTML chars but only a few visible chars.
  # Select an actual long body before testing deferred rendering, never weaken the body assertion.
  candidates=[]
  for candidate in data["entries"]:
   r=api.get("/v1/entries/"+str(candidate["id"]));r.raise_for_status()
   candidate_full=r.json()
   plain=page.evaluate("html => new DOMParser().parseFromString(html, 'text/html').body.textContent",candidate_full.get("content",""))
   candidates.append({"entry_id":candidate["id"],"html_chars":len(candidate_full.get("content","")),"plain_chars":len(plain)})
   if len(plain.strip())>300:
    entry=candidate
    break
  report["body_candidates"]=candidates
  check("PERF-02-fulltext-fixture",entry is not None,candidates)
  first=page.locator(".grid-card-title,.card-title").filter(has_text=entry["title"]).first
  with page.expect_response(lambda r:r.url.endswith(f"/mf/v1/entries/{entry['id']}") and r.request.method=="GET") as received:
   first.click()
  full=received.value.json();page.locator(".article-body").wait_for()
  report["body_probe"]={"entry_id":entry["id"],"response_html_chars":len(full.get("content","")),"response_plain_chars":len(re.sub("<[^>]+>","",full.get("content","")))}
  page.wait_for_timeout(1500)
  report["body_probe"]["page_body_chars"]=len(page.locator(".article-body").inner_text())
  report["body_probe"]["aria_busy"]=page.locator(".article-body").get_attribute("aria-busy")
  page.screenshot(path=str(ROOT/"artifacts/screenshots/body-probe.png"),full_page=True)
  page.wait_for_function("(document.querySelector('.article-body')?.innerText.length || 0) > 100")
  rendered=page.locator(".article-body").inner_text()
  report["body_probe"]["page_body_chars"]=len(rendered)
  normalized=re.sub(r"\s+"," ",rendered).strip()
  source_plain=page.evaluate("html => new DOMParser().parseFromString(html, 'text/html').body.textContent",full.get("content",""))
  normalized_source=re.sub(r"\s+"," ",source_plain).strip()
  anchor=normalized[20:100]
  check("PERF-02-click-fetches-body",len(full.get("content",""))>100 and len(rendered)>100 and anchor in normalized_source,report["body_probe"])
  page.reload(wait_until="domcontentloaded");page.locator(".article-body").wait_for()
  page.wait_for_function("(document.querySelector('.article-body')?.innerText.length || 0) > 100")
  check("PERF-03-deep-link-body",len(page.locator(".article-body").inner_text())>100 and anchor in re.sub(r"\s+"," ",page.locator(".article-body").inner_text()))
  page.get_by_role("button",name="关闭文章",exact=True).click()
  # Refresh after the read mutation so the pagination comparison starts from a new snapshot.
  responses.clear();page.reload(wait_until="domcontentloaded");page.locator(".grid-card-title,.card-title").first.wait_for()
  pages=[r for r in responses if "/v1/entries?" in r.url and "ai_view=recommended" in r.url]
  listing=pages[0];initial=listing.json();params=parse_qs(urlsplit(listing.url).query)
  expected=[];offset=0
  while True:
   q={k:v[0] for k,v in params.items()};q.update(limit=100,offset=offset)
   r=api.get(BASE+"/mf/v1/entries?"+urlencode(q));r.raise_for_status();chunk=r.json()
   expected.extend(e["id"] for e in chunk["entries"]);offset+=len(chunk["entries"])
   if offset>=chunk["total"] or not chunk["entries"]:break
  deadline=time.monotonic()+90
  while time.monotonic()<deadline:
   more=page.locator(".load-more-container")
   loaded=[r for r in responses if "/v1/entries?" in r.url and "ai_view=recommended" in r.url]
   received_count=sum(len(r.json()["entries"]) for r in loaded)
   if received_count>=len(expected):
    expect(more).to_have_count(0,timeout=10000)
    break
   if more.count():more.scroll_into_view_if_needed()
   else:
    page.locator(".grid-card-title,.card-title").last.scroll_into_view_if_needed()
   page.wait_for_timeout(750)
  check("PERF-04-stops-at-end",page.locator(".load-more-container").count()==0)
  pages=[r for r in responses if "/v1/entries?" in r.url and "ai_view=recommended" in r.url]
  ids=[];offsets=[]
  for r in pages:
   offsets.append(int(parse_qs(urlsplit(r.url).query).get("offset",[0])[0]))
   ids.extend(e["id"] for e in r.json()["entries"])
  check("PERF-04-no-duplicates",len(ids)==len(set(ids)),{"loaded":len(ids),"offsets":offsets})
  check("PERF-04-no-gaps",ids==expected,{"expected":len(expected),"actual":len(ids)})
  check("PERF-06-json-gzip",listing.headers.get("content-encoding")=="gzip",{"content_length":listing.headers.get("content-length"),"decoded_bytes":len(listing.body())})
  assets=page.evaluate(r"performance.getEntriesByType('resource').map(r=>r.name).filter(n=>n.includes('/assets/') && /\.(js|css)$/.test(n))")
  encodings=[]
  for url in assets[:8]:
   r=context.request.get(url,headers={"Accept-Encoding":"gzip"})
   encodings.append({"path":urlsplit(url).path,"encoding":r.headers.get("content-encoding")})
  check("PERF-05-static-gzip",bool(encodings) and all(x["encoding"]=="gzip" for x in encodings),encodings)
  for eid,suffix in [(2844,"/blog/aae/cover.webp"),(2841,"/image/blog/2026-03-31.jpg")]:
   r=api.get(BASE+f"/mf/v1/entries/{eid}");r.raise_for_status();cover=r.json()["ai"]["cover_url"]
   check(f"COVER-{eid}",cover.endswith(suffix),cover)
  page.goto(BASE+"/inbox/all",wait_until="domcontentloaded")
  page.locator(".grid-card-title,.card-title").first.wait_for()
  page.get_by_role("button",name="AI 设置 · 来源 · 工具").click()
  page.get_by_role("button",name="来源目录",exact=True).click()
  page.get_by_role("button",name="检查 X 来源",exact=True).click()
  expect(page.get_by_role("status")).to_contain_text("尚未取得帖子",timeout=30000)
  check("X-no-false-subscription",page.get_by_role("button",name="订阅 X 来源",exact=True).is_disabled())
  page.screenshot(path=str(ROOT/"artifacts/screenshots/x-source-status.png"),full_page=True)
  check("no-javascript-errors",not errors,errors)
  browser.close()
except Exception as exc:
 try:
  report["failure_url"]=page.url
  page.screenshot(path=str(ROOT/"artifacts/screenshots/browser-performance-failure.png"),full_page=True)
 except Exception:
  report["failure_screenshot_unavailable"]=True
 report["error"]=type(exc).__name__+": "+str(exc)[:700]
try:
 browser.close()
except Exception:
 pass
try:
 p.stop()
except Exception:
 pass
if entry:
 with client(timeout=20) as api:
  api.put("/v1/entries",json={"entry_ids":[entry["id"]],"status":entry["status"]}).raise_for_status()
 report["test_mutations_restored"]=True
report["passed"]=not report.get("error") and all(x["passed"] for x in report["checks"])
(ROOT/"artifacts/browser-performance-public.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
sys.exit(0 if report["passed"] else 1)
