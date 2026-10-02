"""Built Chromium mobile reading-space acceptance, isolated APIs and touch emulation.

No physical Android/iOS, browser-toolbar animation or real soft-keyboard claim.
Baseline mode records the same fixture against the unchanged release bundle.
"""
import argparse,json,re
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
from playwright.sync_api import expect
from review_reader_harness import Harness

parser=argparse.ArgumentParser()
parser.add_argument('--baseline',action='store_true')
parser.add_argument('--focus',action='store_true')
args=parser.parse_args()
reports=[]
dimensions=[(320,640),(360,640),(390,844),(412,915),(430,932),(390,576),(390,400),(640,360)]

def setup(name,width,height,theme):
    h=Harness(name,has_touch=True,is_mobile=True,viewport={'width':width,'height':height})
    p=h.page
    p.add_init_script("localStorage.setItem('settings',JSON.stringify({articleListLayout:'card',showStatus:'all',theme:"+json.dumps(theme)+"}));if(!localStorage.getItem('ai-view-state'))localStorage.setItem('ai-view-state',JSON.stringify({mode:'recommended',minimum:8,sort:'score',direction:'desc',auxiliary:'none'}))")
    h.settings['minimum_score']=8
    h.status={'counts':{'done':6564,'pending':1120},'kaggle':{'enabled':True,'lanes':{str(n):{'state':'cooldown','outstanding':{'state':'submit_unknown'}} for n in range(5)}}}
    h.feeds[0].update(icon={'feed_id':7,'icon_id':0},checked_at='2026-10-01T08:00:00Z',parsing_error_count=0,parsing_error_message='',disabled=False,hide_globally=False)
    body='<h2>可读正文</h2>'+''.join(f'<p>隔离验证第{i}段：正文在底部操作栏上方滚动，保持完整阅读。点击设置不会改变后台分析。</p>' for i in range(30))+'<p id="mobile-final-paragraph">文末完整段落验收。</p>'
    for eid in range(101,113):
        h.entries.append({'id':eid,'user_id':1,'feed_id':7,'title':f'手机阅读验证文章 {eid}','url':f'https://example.test/{eid}','comments_url':'','author':'测试作者','content':body,'hash':str(eid),'published_at':'2026-10-02T01:00:00Z','created_at':'2026-10-02T01:00:00Z','changed_at':'2026-10-02T01:00:00Z','status':'read','starred':False,'reading_time':4,'enclosures':[],'feed':h.feeds[0],'ai':{'state':'done','score':8.1,'technical_score':7.8,'business_score':7.4,'summary':'优先看：正文摘要与推荐理由完整呈现。','tags':['测试','AI'],'reason':'推荐理由完整可读，保持筛选和排序的真实状态。','has_note':False}})
    h.requests=[]
    def intercept(route,path,method):
        if path.endswith('/entries') and method=='GET':
            h.requests.append(parse_qs(urlsplit(route.request.url).query))
        return False
    h.custom=intercept
    h.goto()
    expect(p.locator('.entry-list [data-entry-id="101"]').first).to_be_visible()
    expect(p.locator('.page-info')).to_contain_text('(12)')
    if theme=='dark':p.evaluate("document.body.setAttribute('arco-theme','dark')")
    return h

def metrics(p):
    return p.evaluate("""() => {
      const rect=s=>{const e=document.querySelector(s),r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height,width:r.width}}
      const toolbar=rect('.ai-toolbar'),navigation=rect('.search-and-sort-bar'),footer=rect('.entry-panel'),list=rect('.entry-list');
      const visible=[...document.querySelectorAll('.entry-list [data-entry-id]')].filter(e=>{const r=e.getBoundingClientRect();return r.top<list.bottom&&r.bottom>list.top}).map(e=>e.dataset.entryId);
      const readable=[...document.querySelectorAll('.entry-list .card-title,.entry-list .ai-scoreline,.entry-list .ai-reason')].filter(e=>{const r=e.getBoundingClientRect();return r.top>=list.top&&r.bottom<=list.bottom}).map(e=>e.textContent);
      return {readableFirstScreen:readable,viewport:{width:innerWidth,height:innerHeight},toolbar,navigation,footer,list,readingRatio:list.height/innerHeight,controlsRatio:(toolbar.height+navigation.height+footer.height)/innerHeight,visibleCards:visible,overflow:document.documentElement.scrollWidth>innerWidth}
    }""")

def last_visible(p,selector,container):
    node=p.locator(selector).last
    node.scroll_into_view_if_needed()
    return node.evaluate("""(e,s)=>{const r=e.getBoundingClientRect(),c=document.querySelector(s).getBoundingClientRect();return r.bottom<=c.bottom+1&&r.top>=c.top-1}""",container)

for width,height in ([(390,844)] if args.focus else dimensions):
  for theme in (['dark'] if args.focus else ['light','dark']):
    name=('mobile-baseline' if args.baseline else 'mobile-reading')+f'-{width}x{height}-{theme}'
    h=setup(name,width,height,theme);p=h.page
    try:
      m=metrics(p);reports.append({'name':name,**m})
      (h.out/'geometry.json').write_text(json.dumps(m,indent=2))
      p.screenshot(path=str(h.out/'list.png'))
      if args.baseline:
        h.check('baseline_measurement_recorded',m['list']['height']>0)
        continue
      h.check('no_horizontal_overflow',not m['overflow'])
      h.check('navigation_in_flex_flow',p.locator('.search-and-sort-bar').evaluate('e=>getComputedStyle(e).position')=='static')
      if width>=360:
        h.check('top_controls_at_most_92px',m['toolbar']['height']+m['navigation']['height']<=92)
        h.check('bottom_controls_at_most_52px',m['footer']['height']<=52)
        if height>=576:h.check('reading_area_at_least_75_percent',m['readingRatio']>=.75)
      h.check('direct_mode_score_and_status_touch_targets',p.locator('.ai-toolbar > .ai-modes button,.ai-toolbar > .ai-aux-modes button,.ai-minimum select,.ai-status-trigger').evaluate_all('(nodes)=>nodes.every(e=>{const r=e.getBoundingClientRect();return r.width>=43.9&&r.height>=43.9})'))
      h.check('search_sort_footer_touch_targets',p.locator('.reader-search-trigger,.ai-sort-select,.entry-panel button,.entry-panel .arco-radio-button').evaluate_all('(nodes)=>nodes.filter(e=>e.getClientRects().length).every(e=>{const r=e.getBoundingClientRect();return r.width>=43.9&&r.height>=43.9})'))
      p.locator('.entry-list').first.evaluate('e=>e.scrollTop=e.scrollHeight')
      expect(p.locator('.entry-list [data-entry-id="112"]').first).to_be_visible()
      contrast=p.locator('.ai-minimum select').evaluate('''e=>{
        const rgb=s=>(s.match(/[\d.]+/g)||[]).slice(0,3).map(Number);
        const luminance=s=>rgb(s).map(n=>{n/=255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4}).reduce((v,n,i)=>v+n*[.2126,.7152,.0722][i],0);
        const c=getComputedStyle(e),a=luminance(c.color),b=luminance(c.backgroundColor);
        return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
      }''')
      h.check('minimum_score_text_contrast_at_least_4_5',contrast>=4.5)
      h.check('last_card_footer_not_covered',last_visible(p,'.entry-list [data-entry-id="112"] .ai-tags','.entry-list') if p.locator('[data-entry-id="112"] .ai-tags').count() else last_visible(p,'.entry-list [data-entry-id="112"] .ai-verdict','.entry-list'))
      # Modal is outside flex flow. Repeated touch opens cannot add height or reset list.
      scroll=p.locator('.entry-list').first
      before=scroll.evaluate('e=>e.scrollTop')
      before_queries=len(h.requests)
      trigger=p.locator('.ai-status-trigger')
      for cycle in range(3):
        trigger.tap();expect(p.get_by_role('dialog',name='运行状态与更多',exact=True)).to_be_visible()
        expect(p.locator('.ai-status-dialog .ai-progress')).to_contain_text('6564 / 7684')
        expect(p.locator('.ai-status-dialog')).to_contain_text('提交未确认')
        expect(p.locator('.ai-status-dialog')).to_contain_text('上次成功读取')
        if cycle==0:p.screenshot(path=str(h.out/'status.png'))
        if cycle%2:p.keyboard.press('Escape')
        else:p.get_by_role('button',name='关闭运行状态',exact=True).tap()
        expect(p.get_by_role('dialog',name='运行状态与更多',exact=True)).to_be_hidden()
        expect(trigger).to_be_focused()
      h.check('status_close_preserves_anchor_and_query',abs(scroll.evaluate('e=>e.scrollTop')-before)<=2 and len(h.requests)==before_queries)
      h.check('closed_diagnostics_use_no_layout_height',metrics(p)['list']['height']==m['list']['height'])
      if (width,height,theme)!=(390,844,'dark'):continue
      # Status-to-navigation handoff keeps one modal and restores visible opener.
      trigger.tap();p.get_by_role('button',name='快速跳转',exact=False).tap()
      expect(p.get_by_role('dialog',name='快速跳转',exact=True)).to_be_visible()
      h.check('one_modal_at_navigation_handoff',p.locator('dialog[open]').count()==1)
      p.keyboard.press('Escape');expect(trigger).to_be_focused()
      # Settings handoff preserves the same visible status opener.
      h.panel();expect(p.locator('dialog[open]')).to_have_count(1)
      p.locator('.ai-dialog>header').get_by_role('button',name='关闭',exact=True).click()
      expect(trigger).to_be_focused()
      h.check('settings_handoff_focus_and_no_writes',not h.writes)
      # Search and IME under a simulated keyboard-reduced viewport.
      search=p.locator('.reader-search-trigger')
      search.tap();field=p.locator('.search-modal input');expect(field).to_be_visible()
      p.set_viewport_size({'width':390,'height':400});field.fill('输入法候选')
      before_queries=len(h.requests)
      field.dispatch_event('keydown',{'key':'Enter','isComposing':True,'keyCode':13})
      expect(field).to_be_visible();h.check('IME_does_not_commit_query',len(h.requests)==before_queries)
      field.fill('手机搜索')
      with p.expect_request(lambda r:parse_qs(urlsplit(r.url).query).get('search')==['手机搜索']):field.press('Enter')
      expect(field).to_be_hidden();expect(search).to_be_focused()
      search.tap();field=p.locator('.search-modal input');field.fill('');field.press('Enter')
      p.set_viewport_size({'width':390,'height':844})
      h.check('search_keyboard_height_recovers',metrics(p)['list']['height']==m['list']['height'])
      p.get_by_role('combobox',name='排序方式',exact=True).select_option('technical_asc')
      expect(p.get_by_role('combobox',name='排序方式',exact=True)).to_have_value('technical_asc')
      p.get_by_role('button',name='有笔记',exact=True).tap();expect(p.get_by_role('button',name='有笔记',exact=True)).to_have_attribute('aria-pressed','true')
      p.reload();expect(p.get_by_role('button',name='有笔记',exact=True)).to_have_attribute('aria-pressed','true')
      expect(p.get_by_role('combobox',name='排序方式',exact=True)).to_have_value('technical_asc')
      h.check('sort_auxiliary_reload_persistence',True)
      p.get_by_role('button',name='有笔记',exact=True).tap()
      # Failure disclosure keeps last good snapshot and offers explicit retry.
      old_custom=h.custom
      def fail_status(route,path,method):
        if path.endswith('/ai/status'):route.fulfill(status=503,json={'detail':'fixture outage'});return True
        return old_custom(route,path,method)
      h.custom=fail_status
      p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
      expect(trigger).to_contain_text('读取失败')
      trigger.tap();expect(p.locator('.ai-status-dialog')).to_contain_text('显示上次快照')
      h.custom=old_custom;p.get_by_role('button',name='重试资源状态',exact=True).click()
      expect(p.get_by_role('button',name='重试资源状态',exact=True)).to_have_count(0)
      p.get_by_role('button',name='关闭运行状态',exact=True).click()
      h.check('status_failure_keeps_snapshot_and_retry',True)
      # A lost save response is unknown, never presented as proof of non-save.
      def fail_minimum(route,path,method):
        if path.endswith('/ai/settings') and method=='PUT':route.fulfill(status=503,json={'detail':'fixture save failure'});return True
        return old_custom(route,path,method)
      h.custom=fail_minimum
      p.get_by_role('combobox',name='最低推荐分',exact=True).select_option('7')
      expect(trigger).to_contain_text('保存待查')
      trigger.tap();expect(p.locator('.ai-status-dialog')).to_contain_text('保存结果未确认')
      p.get_by_role('button',name='关闭运行状态',exact=True).click()
      h.custom=old_custom
      p.get_by_role('combobox',name='最低推荐分',exact=True).select_option('8')
      trigger.tap();expect(p.locator('.ai-status-dialog')).to_contain_text('已保存')
      p.get_by_role('button',name='关闭运行状态',exact=True).click()
      minimum_writes=[w for w in h.writes if w[1].endswith('/ai/settings')]
      h.check('minimum_score_saves_only_requested_field',len(minimum_writes)==2 and [w[2] for w in minimum_writes]==[{'minimum_score':7},{'minimum_score':8}])
      h.status['counts']['done']+=1
      p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
      expect(trigger).to_contain_text('有更新')
      trigger.tap();before_queries=len(h.requests)
      with p.expect_response(lambda r:urlsplit(r.url).path.endswith('/entries') and parse_qs(urlsplit(r.url).query).get('limit')==['24']):
        p.get_by_role('button',name='有新内容 / 中文更新 · 点击刷新',exact=True).click()
      p.wait_for_function("document.querySelector('.ai-status-trigger').textContent!=='有更新'")
      expect(p.locator('.entry-list [data-entry-id]').first).to_be_attached()
      h.check('new_content_update_remains_explicit',len(h.requests)>before_queries)
      p.get_by_role('button',name='关闭运行状态',exact=True).click()
      # CSS inset simulation validates reservation, not a physical notch.
      css=(Path(__file__).resolve().parents[1]/'frontend-review/after/src/components/Ai/MobileReader.css').read_text()
      tag=p.add_style_tag(content=css.replace('env(safe-area-inset-bottom, 0px)','24px').replace('env(safe-area-inset-top, 0px)','20px'))
      safe=metrics(p);reports.append({'name':'simulated-safe-area-20-top-24-bottom',**safe})
      h.check('safe_area_reserved_once',abs(safe['footer']['height']-m['footer']['height']-24)<=1 and abs(safe['toolbar']['height']-m['toolbar']['height']-20)<=1)
      p.locator('.entry-list').first.evaluate('e=>e.scrollTop=e.scrollHeight')
      expect(p.locator('.entry-list [data-entry-id="112"]').first).to_be_visible()
      h.check('safe_area_last_card_reachable',last_visible(p,'.entry-list [data-entry-id="112"] .ai-verdict','.entry-list'))
      tag.evaluate('e=>e.remove()')
      # 200% text sizing lets the direct controls wrap; every operation remains reachable.
      p.evaluate("document.documentElement.style.fontSize='32px'")
      trigger.tap();expect(p.get_by_role('dialog',name='运行状态与更多',exact=True)).to_be_visible()
      p.get_by_role('button',name='关闭运行状态',exact=True).click()
      h.check('200_percent_text_has_no_overflow',not metrics(p)['overflow'])
      p.screenshot(path=str(h.out/'text-200.png'))
      p.evaluate("document.documentElement.style.fontSize=''")
      # Portrait/landscape transition and browser Back/Forward preserve filter storage.
      p.set_viewport_size({'width':640,'height':360});h.check('landscape_no_overflow',not metrics(p)['overflow'])
      p.set_viewport_size({'width':390,'height':844})
      state=p.evaluate("JSON.parse(localStorage.getItem('ai-view-state'))")
      p.goto(h.base+'/inbox/feed/7');p.go_back();p.go_forward()
      p.wait_for_function("JSON.parse(localStorage.getItem('ai-view-state')).hydrated===true")
      after=p.evaluate("JSON.parse(localStorage.getItem('ai-view-state'))")
      h.check('back_forward_retains_filter',all(after[key]==state[key] for key in ['mode','auxiliary','minimum','sort','direction']))
      # Full article is a separate scroll layer. Reach the final text, link, note and pager.
      p.goto(h.base+'/inbox/all/entry/101')
      expect(p.get_by_role('textbox',name='我的笔记',exact=True)).to_be_enabled()
      body=p.locator('.article-content')
      h.check('full_text_last_paragraph_reachable',last_visible(p,'#mobile-final-paragraph','.article-content'))
      link=p.locator('.article-source-footer a').last;link.scroll_into_view_if_needed()
      h.check('full_text_source_link_reachable',link.is_visible() and link.get_attribute('href')=='https://example.test/101')
      note=p.get_by_role('textbox',name='我的笔记',exact=True);note.scroll_into_view_if_needed()
      h.check('full_text_note_reachable',note.is_visible())
      h.check('full_text_actions_touch_targets',p.locator('.action-buttons.mobile button').evaluate_all('(nodes)=>nodes.filter(e=>e.getClientRects().length).every(e=>{const r=e.getBoundingClientRect();return r.width>=43.9&&r.height>=43.9})'))
      p.screenshot(path=str(h.out/'full-text.png'))
      p.get_by_role('button',name='关闭文章',exact=True).click()
      h.check('no_other_backend_writes',all(w[1].endswith('/ai/reading-session') or (w[1].endswith('/ai/settings') and set(w[2])=={'minimum_score'}) for w in h.writes))
    except Exception as exc:
      h.errors.append(str(exc));p.screenshot(path=str(h.out/'failure.png'));raise
    finally:h.close()
out=Path(__file__).resolve().parents[1]/'runtime'/('mobile-baseline-metrics.json' if args.baseline else 'mobile-reading-metrics.json')
out.write_text(json.dumps({'physicalDevice':False,'keyboard':'viewport-height and synthetic IME emulation','safeArea':'CSS inset simulation','measurements':reports},ensure_ascii=False,indent=2))
print(out,flush=True)
