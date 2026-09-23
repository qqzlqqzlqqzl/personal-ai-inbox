"""Real live reader data + explicitly controlled timing/failures; no direct AI calls."""
import sys,json,time,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from playwright.sync_api import sync_playwright
from browser_env import launch
from ops_common import client,local_admin
ROOT=Path('/home/ubuntu/ai-news');BASE=os.environ.get('AI_NEWS_WEB_BASE','https://106.53.40.6')
OUT=ROOT/'artifacts/scroll-session';OUT.mkdir(exist_ok=True)
report={'at':time.time(),'base':BASE,'checks':[],'mode':'Live entries + controlled translation counter and pagination faults; service workers blocked for reproducibility'}
def ck(name,ok,detail=None):
 report['checks'].append({'name':name,'passed':bool(ok),'detail':detail});print(name,ok,flush=True)
 if not ok:raise AssertionError(name)
GEO='''() => { const e=document.querySelector('.entry-list [data-entry-id]'); if(!e)return null;let root=e.parentElement;while(root&&!(root.scrollHeight>root.clientHeight+10&&['auto','scroll'].includes(getComputedStyle(root).overflowY)))root=root.parentElement;if(!root)return null;window.__scrollRoot=root;const rr=root.getBoundingClientRect();const a=[...root.querySelectorAll('[data-entry-id]')].find(x=>x.getBoundingClientRect().bottom>rr.top+1&&x.getBoundingClientRect().top<rr.bottom);const f=document.querySelector('.load-more-container');return {top:root.scrollTop,height:root.scrollHeight,viewport:root.clientHeight,anchor:a?.dataset.entryId,offset:a?Math.round(a.getBoundingClientRect().top-rr.top):null,count:Number(f?.dataset.loadedCount),more:f?.dataset.more,events:window.__prefetchEvents||[],url:location.pathname};}'''
def geo(page):return page.evaluate(GEO)
def move(page,delta):page.evaluate('(delta)=>{window.__scrollRoot.scrollTop+=delta}',delta)
def until_count(page,n):
 page.wait_for_function('(n)=>Number(document.querySelector(".load-more-container")?.dataset.loadedCount)>n',arg=n,timeout=30000)
 page.wait_for_timeout(450)
def read_to_next(page):
 old=geo(page)
 for _ in range(45):
  if geo(page)['count']>old['count']:return geo(page)
  move(page,max(140,old['viewport']*.28));page.wait_for_timeout(160)
 until_count(page,old['count']);return geo(page)
with client() as c:status=c.get('/v1/ai/status').json()
u,pw=local_admin()
try:
 with sync_playwright() as p:
  browser=launch(p)
  for mobile in (False,True):
   tag='mobile' if mobile else 'desktop'
   ctx=browser.new_context(viewport={'width':390,'height':844} if mobile else {'width':1440,'height':1000},locale='zh-CN',is_mobile=mobile,has_touch=mobile,service_workers='block')
   ctx.add_init_script("localStorage.setItem('settings',JSON.stringify({showStatus:'all',markReadOnScroll:false,removeDuplicates:false}));window.__prefetchEvents=[];addEventListener('inbox:prefetch',e=>window.__prefetchEvents.push(e.detail))")
   state={'done':0,'polls':0,'lists':0,'fault':False,'held':None,'hold':False,'errors':[]}
   def sr(route):
    state['polls']+=1;data=json.loads(json.dumps(status));data['translations']['counts']['done']=500+state['done'];route.fulfill(status=200,json=data)
   ctx.route('**/mf/v1/ai/status',sr)
   def lr(route):
    state['lists']+=1
    if state['fault']:route.fulfill(status=503,json={'error_message':'controlled pagination test failure'});return
    if state['hold'] and state['held'] is None:state['held']=route;return
    route.continue_()
   ctx.route('**/mf/v1/entries?*',lr)
   page=ctx.new_page();page.on('pageerror',lambda e:state['errors'].append(str(e)))
   page.goto(BASE+'/inbox/login',wait_until='domcontentloaded');page.locator('#password_input').fill(pw);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('**/all',timeout=40000);page.locator('[data-entry-id]').first.wait_for(timeout=40000);page.locator('.load-more-container[data-loaded-count]').wait_for()
   initial=geo(page);ck(tag+'_initial_batch',initial['count']>=12,initial['count'])
   page.wait_for_timeout(1800);ck(tag+'_no_eager_library_drain',geo(page)['count']==initial['count'])
   nxt=read_to_next(page);evt=nxt['events'][0];ck(tag+'_quarter_prefetch',evt['reason']=='quarter' and evt['remaining']>evt['scrollTop']*.4,evt)
   ck(tag+'_threshold_fraction',evt['index']>=evt['target'] and evt['index']<evt['target']+4,evt)
   hold_count=nxt['count'];page.wait_for_timeout(1800);ck(tag+'_one_page_ahead_only',geo(page)['count']==hold_count)
   for i in range(4):
    previous=geo(page);nxt=read_to_next(page)
    ck(f'{tag}_append_page_{i+3}',nxt['count']>previous['count'] and nxt['top']>100,{'count':nxt['count'],'top':nxt['top']})
   # Keep the same visible article through at least two real 30-second poll periods.
   page.wait_for_timeout(1000);before=geo(page);requests=state['lists'];state['done']=1
   page.wait_for_timeout(32000);after=geo(page)
   ck(tag+'_poll_preserves_list',after['count']==before['count'] and state['lists']==requests,{'before':before['count'],'after':after['count']})
   ck(tag+'_poll_preserves_anchor',after['anchor']==before['anchor'] and abs(after['offset']-before['offset'])<=40,{'before':{k:before[k] for k in ['top','anchor','offset']},'after':{k:after[k] for k in ['top','anchor','offset']}})
   ck(tag+'_explicit_update_notice',page.locator('.ai-updates').count()==1)
   if not mobile:
    state['done']=2;page.wait_for_timeout(32000);after2=geo(page)
    ck('two_poll_cycles_without_reset',after2['anchor']==after['anchor'] and after2['count']==after['count'],{'polls':state['polls'],'anchor':after2['anchor']})
   page.screenshot(path=str(OUT/(tag+'-reading.png')))
   # Intentional refresh is user-controlled, not periodic.
   page.locator('.ai-updates').click();page.wait_for_timeout(2000);ck(tag+'_manual_refresh',geo(page)['count']<before['count'])
   if not mobile:
    # Inject transient failure; old content survives and retry is explicit.
    state['fault']=True;start=geo(page)
    for _ in range(20):
     move(page,220);page.wait_for_timeout(180)
     if page.get_by_role('button',name='加载失败，点击重试',exact=True).count():break
    page.get_by_role('button',name='加载失败，点击重试',exact=True).wait_for(timeout=12000)
    failed=geo(page);ck('failed_prefetch_preserves_entries',failed['count']==start['count'] and failed['top']>0)
    attempts=state['lists'];page.wait_for_timeout(2500);ck('failure_no_retry_storm',state['lists']==attempts)
    state['fault']=False;page.get_by_role('button',name='加载失败，点击重试',exact=True).click();until_count(page,failed['count']);ck('manual_retry_appends',geo(page)['count']>failed['count'])
    # In-flight old AI page is ignored after user changes list mode.
    state['hold']=True
    for _ in range(35):
     move(page,260);page.wait_for_timeout(120)
     if state['held'] is not None:break
    ck('old_page_in_flight',state['held'] is not None)
    page.get_by_role('button',name='全部原始',exact=True).click();page.locator('.load-more-container[data-loaded-count]').wait_for(state='attached',timeout=45000);page.locator('.entry-list [data-entry-id]').first.wait_for(timeout=45000);page.wait_for_timeout(500)
    changed=geo(page);state['hold']=False
    try:state['held'].continue_()
    except Exception:pass
    page.wait_for_timeout(1800);ck('stale_page_ignored_after_mode_change',geo(page)['count']==changed['count'],{'before_release':changed['count'],'after_release':geo(page)['count']})
   ck(tag+'_no_fatal_javascript',not state['errors'],state['errors']);ctx.close()
  browser.close()
 report['passed']=all(c['passed'] for c in report['checks'])
except Exception as e:
 report['passed']=False;report['error']=type(e).__name__+': '+str(e)[:1000]
 import traceback;report['traceback']=traceback.format_exc()[-2500:]
finally:
 (OUT/'browser.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False),flush=True)
 if not report.get('passed'):sys.exit(1)
