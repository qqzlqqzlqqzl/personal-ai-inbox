"""Airing cover DOM acceptance; list-only, no article state writes."""
import json, os, sys, time
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from playwright.sync_api import sync_playwright
from browser_env import launch
from ops_common import client, local_admin
ROOT=Path(__file__).resolve().parents[1]
BASE=os.environ.get("AI_NEWS_WEB_BASE","https://106.53.40.6")
report={"at":time.time(),"base":BASE,"checks":[],"covers":[],"image_failures":[],"blocked_writes":[]}
def check(name,ok,detail=None):
 report["checks"].append({"name":name,"passed":bool(ok),"detail":detail})
 if not ok:raise AssertionError(name)
try:
 with client(timeout=20) as api:
  entries=[]
  for eid in [2844,2841]:
   r=api.get("/v1/entries/"+str(eid));r.raise_for_status();entries.append(r.json())
 with sync_playwright() as p:
  browser=launch(p)
  context=browser.new_context(viewport={"width":1440,"height":1100},locale="zh-CN")
  page=context.new_page();page.set_default_timeout(30000)
  def guard(route):
   if route.request.method not in ["GET","HEAD","OPTIONS"]:
    report["blocked_writes"].append({"method":route.request.method,"path":urlsplit(route.request.url).path});route.abort()
   else:route.continue_()
  page.route("**/mf/v1/entries*",guard)
  page.on("requestfailed",lambda r:report["image_failures"].append({"url":r.url,"failure":r.failure}) if r.resource_type=="image" else None)
  page.add_init_script("""(() => {
    window.__coverEvidence = {};
    const capture = () => document.querySelectorAll('.grid-card-content,.card-content').forEach(card => {
      const title=card.querySelector('.grid-card-title,.card-title')?.textContent;
      const img=card.querySelector('img.grid-card-cover,.card-thumbnail img');
      if(title && img) window.__coverEvidence[title]={src:img.getAttribute('src'),naturalWidth:img.naturalWidth,naturalHeight:img.naturalHeight,complete:img.complete};
    });
    new MutationObserver(capture).observe(document,{subtree:true,childList:true,attributes:true});
    document.addEventListener('load',capture,true);
    document.addEventListener('error',capture,true);
  })()""")
  page.goto(BASE+"/inbox/login",wait_until="domcontentloaded")
  _,password=local_admin();page.locator("#password_input").fill(password)
  page.get_by_role("button",name="登录",exact=True).click();page.wait_for_url("**/today")
  page.goto(BASE+"/inbox/feed/"+str(entries[0]["feed_id"]),wait_until="domcontentloaded")
  for entry in entries:
   title=page.locator(".grid-card-title,.card-title").filter(has_text=entry["title"]).first
   title.wait_for();title.scroll_into_view_if_needed()
   deadline=time.monotonic()+20
   evidence=None
   while time.monotonic()<deadline:
    evidence=page.evaluate("title => window.__coverEvidence[title] || null",entry["title"])
    if evidence and evidence["complete"]:break
    page.wait_for_timeout(300)
   check("cover-dom-source-"+str(entry["id"]),bool(evidence) and evidence["src"]==entry["ai"]["cover_url"],evidence)
   result={"entry_id":entry["id"],"title":entry["title"],**evidence}
   result["network_loaded"]=evidence["naturalWidth"]>0
   result["selection_correct"]=True
   report["covers"].append(result)
  page.screenshot(path=str(ROOT/"artifacts/screenshots/cover-fix-airing-current.png"),full_page=True)
  check("no-article-state-writes",not report["blocked_writes"])
  browser.close()
 with client(timeout=20) as api:
  for entry in entries:
   r=api.get("/v1/entries/"+str(entry["id"]));r.raise_for_status()
   check("status-unchanged-"+str(entry["id"]),r.json()["status"]==entry["status"])
except Exception as exc:
 report["error"]=type(exc).__name__+": "+str(exc)[:700]
report["passed"]=not report.get("error") and all(c["passed"] for c in report["checks"])
report["all_images_loaded"]=len(report["covers"])==2 and all(c["network_loaded"] for c in report["covers"])
(ROOT/"artifacts/browser-cover-public.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
sys.exit(0 if report["passed"] else 1)
