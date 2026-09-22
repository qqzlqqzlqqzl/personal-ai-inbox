"""Live user-reported cases: product previews, NVIDIA figures, Chinese card cache."""
import sys, os, json, re, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from playwright.sync_api import sync_playwright, expect
from browser_env import launch
from ops_common import client, local_admin
import core

BASE=os.environ.get('AI_NEWS_WEB_BASE','https://106.53.40.6').rstrip('/')
APP=BASE+'/inbox'
IDS=[2900,2899,2898,2897,2896,2881,2880,2871]
REPORT=core.ROOT/'artifacts/takeover-browser.json'
OUT=core.ROOT/'artifacts/screenshots'
result={'at':time.time(),'base_url':BASE,'checks':[]}
errors=[]
def check(name,ok,detail=None):
    result['checks'].append({'name':name,'passed':bool(ok),'detail':detail})
    if not ok: raise AssertionError(name)

def cached_snapshot(ids):
    with core.connect() as db:
        return [tuple(r) for r in db.execute('SELECT entry_id,source_hash,title_zh,summary_zh,translated_at FROM card_translations WHERE entry_id IN ('+','.join('?' for _ in ids)+') ORDER BY entry_id',ids)]

def login(page):
    page.goto(APP+'/login',wait_until='domcontentloaded')
    user,password=local_admin()
    page.locator('#username_input').fill(user);page.locator('#password_input').fill(password)
    page.get_by_role('button',name='登录',exact=True).click()
    page.wait_for_url('**/all',timeout=30000)
    page.locator('.article-entry').first.wait_for(timeout=45000)

with client() as api:
    originals={eid:api.get(f'/v1/entries/{eid}').json() for eid in IDS+[2892]}
    try:
        for eid in IDS:
            entry=originals[eid]
            check(f'ph_{eid}_source_attribution','产品介绍（Product Hunt）' in entry['content'] and '原始 RSS 简介' in entry['content'])
            check(f'ph_{eid}_card_chinese',entry.get('card',{}).get('language')=='zh-CN',entry.get('card',{}).get('title'))
        category=originals[2900]['feed']['category']['id']
        with sync_playwright() as p:
            browser=launch(p)
            context=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN')
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            login(page)
            for view in ['/all','/today',f'/category/{category}','/feed/40']:
                page.goto(APP+view,wait_until='domcontentloaded')
                page.locator('.article-entry').first.wait_for(timeout=45000)
                for attempt in range(8):
                    titles=page.locator('.article-entry-title').all_inner_texts()[:8]
                    summaries=page.locator('.grid-card-summary,.card-preview').all_inner_texts()[:8]
                    if titles and all(re.search(r'[\u3400-\u9fff]',t) for t in titles) and summaries and all(re.search(r'[\u3400-\u9fff]',s) for s in summaries):
                        break
                    page.wait_for_timeout(10000);page.reload(wait_until='domcontentloaded')
                    page.locator('.article-entry').first.wait_for(timeout=30000)
                check('chinese_view_'+view,bool(titles) and all(re.search(r'[\u3400-\u9fff]',t) for t in titles) and bool(summaries) and all(re.search(r'[\u3400-\u9fff]',s) for s in summaries),{'titles':titles,'summaries':summaries})
                page.screenshot(path=str(OUT/('takeover-'+view.strip('/').replace('/','-')+'.png')),full_page=False)
            covers=page.locator('.grid-card-cover')
            for index in range(min(8,covers.count())):
                image=covers.nth(index);image.scroll_into_view_if_needed()
                expect(image).to_be_visible(timeout=30000)
                page.wait_for_function('(i)=>{const x=document.querySelectorAll(".grid-card-cover")[i];return x?.complete&&x.naturalWidth>100&&!x.currentSrc.startsWith("data:")}',arg=index,timeout=45000)
            check('product_feed_real_covers',covers.count()>=8,{'count':covers.count()})
            page.locator('.article-entry').first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            page.screenshot(path=str(OUT/'takeover-product-covers-loaded.png'),full_page=False)
            before=cached_snapshot(IDS)
            page.reload(wait_until='domcontentloaded');page.locator('.article-entry').first.wait_for(timeout=45000)
            check('cached_translation_unchanged_by_reload',before==cached_snapshot(IDS))
            page.goto(APP+'/all/entry/2892',wait_until='domcontentloaded')
            page.locator('.article-body').wait_for(timeout=30000)
            body=page.locator('.article-body').inner_text()
            check('nvidia_original_language_retained','CUDA' in body and 'NVIDIA' in body and len(body)>1000,{'chars':len(body)})
            phrases=['three side-by-side ROS nodes','CUDA buffer backend','before-after side-by-side']
            for n,phrase in enumerate(phrases,1):
                selector=f'.article-body img[alt*="{phrase}"]'
                image=page.locator(selector).first
                check(f'nvidia_figure_{n}_exists',image.count()==1)
                image.scroll_into_view_if_needed()
                page.wait_for_function('(s)=>{const x=document.querySelector(s);return x?.complete&&x.naturalWidth>100&&!x.currentSrc.startsWith("data:")}',arg=selector,timeout=60000)
                image.evaluate('async x=>{await x.decode();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));}')
                page.wait_for_timeout(150)
                details=image.evaluate('(x)=>({src:x.currentSrc,width:x.naturalWidth,height:x.naturalHeight})')
                check(f'nvidia_figure_{n}_decoded',details['width']>100,details)
                page.screenshot(path=str(OUT/f'takeover-nvidia-figure-{n}.png'),full_page=False)
            mobile=browser.new_context(viewport={'width':390,'height':844},locale='zh-CN',is_mobile=True,has_touch=True)
            m=mobile.new_page();m.on('pageerror',lambda e:errors.append(str(e)))
            login(m);m.goto(APP+'/feed/40',wait_until='domcontentloaded');m.locator('.article-entry-title').first.wait_for(timeout=30000)
            check('mobile_chinese_cards',bool(re.search(r'[\u3400-\u9fff]',m.locator('.article-entry-title').first.inner_text())))
            check('mobile_no_overflow',not m.evaluate('document.documentElement.scrollWidth > innerWidth + 2'))
            m.screenshot(path=str(OUT/'takeover-mobile-chinese.png'),full_page=False)
            mobile.close()
            check('no_fatal_js_errors',not errors,errors)
            browser.close()
    except Exception as exc:
        result['error']=type(exc).__name__+': '+str(exc)[:700]
    finally:
        # Opening a detail can auto-mark read. Restore only the test's touched entry.
        original=originals[2892]
        api.put('/v1/entries',json={'entry_ids':[2892],'status':original['status'],'starred':original['starred']}).raise_for_status()
        result['test_mutations_restored']=True
result['passed']=not result.get('error') and all(c['passed'] for c in result['checks'])
REPORT.write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
sys.exit(0 if result['passed'] else 1)
