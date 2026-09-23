"""Read-only, real-source native pagination to the last page; no model requests."""
import json, time, traceback
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright
from browser_env import launch
from ops_common import local_admin
ROOT=Path('/home/ubuntu/ai-news');BASE='https://106.53.40.6'
report={'at':time.time(),'mode':'live native source/category/today, duplicates visible, pageSize20','checks':[]}
errors=[]
def ck(name,ok,detail=None):
 report['checks'].append({'name':name,'passed':bool(ok),'detail':detail});print(name,ok,flush=True)
 if not ok:raise AssertionError(name)
try:
 with sync_playwright() as p:
  b=launch(p);c=b.new_context(viewport={'width':1440,'height':1000},locale='zh-CN',service_workers='block')
  c.add_init_script("localStorage.setItem('settings',JSON.stringify({showStatus:'all',markReadOnScroll:false,removeDuplicates:false,pageSize:20,orderDirection:'desc'}))")
  page=c.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  _,password=local_admin();page.goto(BASE+'/inbox/login',wait_until='domcontentloaded');page.locator('#password_input').fill(password);page.get_by_role('button',name='登录',exact=True).click();page.wait_for_url('**/all',timeout=30000)
  for route,endpoint in [('/feed/56','/mf/v1/feeds/56/entries'),('/category/7','/mf/v1/categories/7/entries'),('/today','/mf/v1/entries')]:
   records={'seen':set(),'first':None,'responses':0,'pages':[]}
   def response(r):
    q=parse_qs(urlsplit(r.url).query)
    if urlsplit(r.url).path!=endpoint or q.get('limit')!=['20'] or 'ai_view' in q:return
    try:
     data=r.json()
     records['pages'].append({'query':urlsplit(r.url).query,'total':data['total'],'entries':[(e['id'],e['published_at']) for e in data['entries']]})
     if records['first'] is None:records['first']=data['total']
     records['seen'].update(e['id'] for e in data['entries']);records['responses']+=1
    except Exception:pass
   page.on('response',response)
   page.goto(BASE+'/inbox'+route,wait_until='domcontentloaded')
   page.locator('[data-entry-id]').first.wait_for(timeout=30000)
   page.locator('.load-more-container[data-loaded-count]').wait_for(timeout=30000)
   def state():
    return page.locator('.load-more-container').evaluate("e=>({count:Number(e.dataset.loadedCount),more:e.dataset.more})")
   first=state();ck(route+'_first_page_bounded',0<first['count']<=20,first)
   for _ in range(30):
    old=state()
    if old['more']!='true':break
    page.evaluate('''()=>{let root=document.querySelector('.entry-list [data-entry-id]')?.parentElement;while(root&&!(root.scrollHeight>root.clientHeight+10&&['auto','scroll'].includes(getComputedStyle(root).overflowY)))root=root.parentElement;if(!root)throw Error('scroll root missing');root.scrollTop=root.scrollHeight}''')
    page.wait_for_function("n=>{const e=document.querySelector('.load-more-container');return Number(e?.dataset.loadedCount)>n||e?.dataset.more!=='true'}",arg=old['count'],timeout=30000)
    page.wait_for_timeout(150)
   final=state();detail={'loaded':final['count'],'expected':records['first'],'seen':len(records['seen']),'responses':records['responses'],'more':final['more'],'pages':records['pages']}
   ck(route+'_reaches_true_tail',final['more']!='true' and final['count']==records['first']==len(records['seen']),detail)
   page.remove_listener('response',response)
  ck('native_views_no_fatal_javascript',not errors,errors)
  b.close()
except Exception as e:
 report['error']=type(e).__name__+': '+str(e)[:600];report['traceback']=traceback.format_exc(limit=2)[:1200]
report['passed']=not report.get('error') and all(c['passed'] for c in report['checks'])
(ROOT/'artifacts/ui-review/native-tail.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'passed':report['passed'],'error':report.get('error')},ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
