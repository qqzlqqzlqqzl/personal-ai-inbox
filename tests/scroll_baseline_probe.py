"""Reproduce the polling reset with live entries and a controlled status counter."""
import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from playwright.sync_api import sync_playwright
from browser_env import launch
from ops_common import client,local_admin
BASE='https://106.53.40.6'
ROOT=Path('/home/ubuntu/ai-news')
with client() as c: status=c.get('/v1/ai/status').json()
state={'count':0,'lists':[],'navigation':0}
geometry='''() => { const e=document.querySelector('.entry-list [data-entry-id]'); if(!e)return null;let root=e.parentElement;while(root&&!(root.scrollHeight>root.clientHeight+10&&['auto','scroll'].includes(getComputedStyle(root).overflowY)))root=root.parentElement;if(!root)return null;window.__scrollRoot=root; const rr=root.getBoundingClientRect();const els=[...root.querySelectorAll('[data-entry-id]')];const a=els.find(x=>x.getBoundingClientRect().bottom>rr.top);return {top:root.scrollTop,height:root.scrollHeight,viewport:root.clientHeight,anchor:a?.dataset.entryId,offset:a?Math.round(a.getBoundingClientRect().top-rr.top):null,url:location.pathname};}'''
with sync_playwright() as p:
 b=launch(p);ctx=b.new_context(viewport={'width':1440,'height':1000},locale='zh-CN',service_workers='block')
 ctx.add_init_script("localStorage.setItem('settings',JSON.stringify({showStatus:'all',markReadOnScroll:false,removeDuplicates:false}))")
 def status_route(route):
  data=json.loads(json.dumps(status));data['translations']['counts']['done']=200+state['count'];route.fulfill(status=200,json=data)
 ctx.route('**/mf/v1/ai/status',status_route)
 page=ctx.new_page()
 page.on('request',lambda r:state['lists'].append(r.url) if '/mf/v1/entries?' in r.url else None)
 u,pw=local_admin();page.goto(BASE+'/inbox/login',wait_until='domcontentloaded');page.locator('#password_input').fill(pw);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('**/all',timeout=45000);page.locator('[data-entry-id]').first.wait_for(timeout=45000)
 # Read several pages via the existing bottom sentinel.
 page.evaluate(geometry)
 for i in range(4):
  page.evaluate('window.__scrollRoot.scrollTop=window.__scrollRoot.scrollHeight')
  page.wait_for_timeout(1600)
 page.evaluate('window.__scrollRoot.scrollTop=Math.max(500,window.__scrollRoot.scrollTop-650)')
 page.wait_for_timeout(1200); before=page.evaluate(geometry); requests_before=len(state['lists']);state['count']=1
 page.wait_for_timeout(32000)
 after=page.evaluate(geometry)
 report={'at':time.time(),'base_commit':'10055e5','method':'live production entries; only translation done counter is controlled; no model calls by test','before':before,'after':after,'list_requests_after_poll':len(state['lists'])-requests_before,'reproduced':before['top']>1000 and after['top']<200}
 (ROOT/'artifacts/scroll-session/baseline.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)); print(json.dumps(report,ensure_ascii=False));b.close()
