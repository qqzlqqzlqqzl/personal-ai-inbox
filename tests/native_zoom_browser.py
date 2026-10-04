"""Native Chromium 200% zoom on the exact built Reader; synthetic APIs only.

Uses a retained test-only extension in a headed Xvfb browser. No CSS zoom,
deviceScaleFactor or page-scale emulation substitutes for tabs.setZoom/getZoom.
"""
import json,os,struct,zlib
from urllib.parse import urlsplit
from review_reader_harness import Harness
from playwright.sync_api import expect

h=Harness('native-zoom');ctx=None
try:
    extension=h.out/'extension';extension.mkdir(exist_ok=False)
    (extension/'manifest.json').write_text(json.dumps({'manifest_version':3,'name':'Isolated Reader native zoom','version':'1.0','permissions':['tabs'],'background':{'service_worker':'worker.js'}}))
    (extension/'worker.js').write_text('globalThis.zoomFixture=async(url,zoom)=>{const tabs=await chrome.tabs.query({});const tab=tabs.find(t=>t.url===url);if(!tab)throw new Error("fixture tab missing");await chrome.tabs.setZoom(tab.id,zoom);return await chrome.tabs.getZoom(tab.id)};')
    ctx=h.pw.chromium.launch_persistent_context(str(h.out/'profile'),executable_path=h.pw.chromium.executable_path,
        headless=False,no_viewport=True,locale='zh-CN',service_workers='block',ignore_default_args=['--disable-extensions'],
        args=['--no-sandbox','--disable-dev-shm-usage','--window-size=1440,960','--disable-extensions-except='+str(extension),'--load-extension='+str(extension)])
    ctx.add_init_script("localStorage.setItem('auth',JSON.stringify({server:location.origin+'/mf',token:'isolated-test-token',username:'',password:''}))")
    ctx.route('**/mf/**',h.route)
    ctx.route(lambda url:(urlsplit(url).scheme,urlsplit(url).netloc)!=(urlsplit(h.base).scheme,urlsplit(h.base).netloc),lambda r:r.abort())
    h.feeds[0].update(icon={'feed_id':7,'icon_id':0},disabled=False,hide_globally=False)
    title='第一章 控制原理'
    h.entries=[{'id':101,'user_id':1,'feed_id':7,'title':'原生缩放中文阅读验证','url':'https://example.test/article/101','comments_url':'','author':'隔离测试',
        'content':'<h2>'+title+'</h2>'+''.join('<p>这是隔离中文阅读测试，保留正文、笔记和阅读控件，不发送生产请求。</p>' for _ in range(20)),
        'hash':'native-zoom-101','published_at':'2026-10-01T08:00:00Z','created_at':'2026-10-01T08:00:00Z','changed_at':'2026-10-01T08:00:00Z',
        'status':'read','starred':False,'reading_time':4,'enclosures':[],'feed':h.feeds[0],'ai':{'status':'pending','has_note':True}}]
    h.notes={'101':'隔离笔记必须保留'}
    p=ctx.new_page();p.set_default_timeout(15000);p.on('pageerror',lambda e:h.errors.append(str(e)))
    p.goto(h.base+'/inbox/all/entry/101',wait_until='domcontentloaded')
    note=p.get_by_role('textbox',name='我的笔记',exact=True);expect(note).to_have_value(h.notes['101'])
    baseline=p.evaluate('({dpr:devicePixelRatio,width:innerWidth,outer:outerWidth})')
    worker=ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event('serviceworker',timeout=15000)
    zoom=worker.evaluate('([url,value])=>zoomFixture(url,value)',[p.url,2])
    assert zoom==2,'native zoom API must report 200%'
    p.wait_for_function('(base)=>Math.abs(devicePixelRatio/base.dpr-2)<.05&&Math.abs(innerWidth/base.width-.5)<.05',arg=baseline)
    metrics=p.evaluate('({dpr:devicePixelRatio,width:innerWidth,outer:outerWidth,documentWidth:document.documentElement.scrollWidth,cssZoom:document.documentElement.style.zoom})')
    h.check('native_api_reports_200_percent',zoom==2)
    h.check('native_dpr_and_css_viewport_changed',abs(metrics['dpr']/baseline['dpr']-2)<.05 and abs(metrics['width']/baseline['width']-.5)<.05)
    h.check('no_css_zoom_substitute',metrics['cssZoom']=='')
    h.check('native_200_document_no_horizontal_overflow',metrics['documentWidth']<=metrics['width'])
    expect(p.get_by_role('button',name='关闭文章',exact=True)).to_be_visible()
    expect(note).to_be_enabled();expect(note).to_have_value(h.notes['101'])
    h.check('native_200_close_and_note_reachable')
    heading=p.locator('.article-content h2').first;expect(heading).to_contain_text(title)
    heading.scroll_into_view_if_needed();p.evaluate('document.fonts.ready')
    session=ctx.new_cdp_session(p);session.send('DOM.enable');session.send('CSS.enable')
    root=session.send('DOM.getDocument')['root']['nodeId']
    node=session.send('DOM.querySelector',{'nodeId':root,'selector':'.article-content h2'})['nodeId']
    fonts=session.send('CSS.getPlatformFontsForNode',{'nodeId':node})['fonts']
    h.check('native_200_actual_cjk_glyphs',any('CJK' in f['familyName'] and f['glyphCount']>0 and not f['isCustomFont'] for f in fonts))
    shot=p.screenshot(path=str(h.out/'native-200.png'))
    offset=8;compressed=b''
    while offset<len(shot):
        size=struct.unpack('>I',shot[offset:offset+4])[0];kind=shot[offset+4:offset+8]
        if kind==b'IDAT':compressed+=shot[offset+8:offset+8+size]
        offset+=size+12
    h.check('native_200_visual_capture_not_blank',len(set(zlib.decompress(compressed)))>8)
    h.check('native_200_does_not_save_note',not h.note_writes)
    (h.out/'native-metrics.json').write_text(json.dumps({'native_zoom':zoom,'baseline':baseline,'observed':metrics,'rendered_fonts':fonts,'method':'chrome.tabs.setZoom/getZoom in headed Chromium/Xvfb','physical_android_ios':False},ensure_ascii=False,indent=2))
    assert worker.evaluate('([url,value])=>zoomFixture(url,value)',[p.url,1])==1
except Exception as exc:
    h.errors.append(type(exc).__name__+': '+str(exc)[:500]);raise
finally:
    if ctx:ctx.close()
    h.close()
