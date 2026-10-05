"""Native Chromium 200% zoom on the exact built Reader; synthetic APIs only.

Uses a retained test-only extension in a headed Xvfb browser. No CSS zoom,
deviceScaleFactor or page-scale emulation substitutes for tabs.setZoom/getZoom.
"""
import json,os,struct,zlib,subprocess,hashlib,base64,math
from urllib.parse import urlsplit
from review_reader_harness import Harness
from playwright.sync_api import expect, Error as PlaywrightError

def surface_png(data):
    """Bounded stdlib decoder for the 8-bit RGB/RGBA PNG emitted by Chromium."""
    assert len(data)<=16*1024*1024 and data[:8]==b'\x89PNG\r\n\x1a\n','invalid PNG signature/budget'
    offset=8;compressed=bytearray();size=None;ended=False
    while offset<len(data):
        assert offset+12<=len(data),'truncated PNG chunk'
        length=struct.unpack('>I',data[offset:offset+4])[0]
        kind=data[offset+4:offset+8];payload=data[offset+8:offset+8+length]
        assert offset+12+length<=len(data),'truncated PNG payload'
        crc=struct.unpack('>I',data[offset+8+length:offset+12+length])[0]
        assert zlib.crc32(kind+payload)&0xffffffff==crc,'PNG CRC mismatch'
        if kind==b'IHDR':
            assert size is None and offset==8 and length==13,'invalid IHDR'
            width,height,depth,color,method,filtering,interlace=struct.unpack('>IIBBBBB',payload)
            assert 0<width<=8192 and 0<height<=8192 and width*height<=8_000_000,'PNG dimensions exceed budget'
            assert depth==8 and color in (2,6) and (method,filtering,interlace)==(0,0,0),'unsupported PNG encoding'
            size=(width,height,3 if color==2 else 4)
        elif kind==b'IDAT':
            assert size is not None,'IDAT before IHDR'
            compressed.extend(payload)
        elif kind==b'IEND':
            assert length==0 and offset+12==len(data),'invalid IEND/trailing bytes'
            ended=True;break
        offset+=length+12
    assert ended and size and compressed,'incomplete PNG'
    width,height,channels=size;stride=width*channels;budget=(stride+1)*height
    decoder=zlib.decompressobj();raw=decoder.decompress(bytes(compressed),budget+1)
    assert len(raw)==budget and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,'PNG decoded size mismatch'
    rows=[];previous=bytes(stride)
    for y in range(height):
        start=y*(stride+1);mode=raw[start];row=bytearray(raw[start+1:start+1+stride])
        assert mode<=4,'unsupported PNG row filter'
        for x in range(stride):
            left=row[x-channels] if x>=channels else 0;up=previous[x];upper_left=previous[x-channels] if x>=channels else 0
            if mode==1:predict=left
            elif mode==2:predict=up
            elif mode==3:predict=(left+up)//2
            elif mode==4:
                p=left+up-upper_left;dist=(abs(p-left),abs(p-up),abs(p-upper_left))
                predict=(left,up,upper_left)[dist.index(min(dist))]
            else:predict=0
            row[x]=(row[x]+predict)&255
        rows.append(bytes(row));previous=row
    return {'width':width,'height':height,'channels':channels,'rows':rows}


def surface_state(page,session,expected_url,target_id=None):
    """Read only this page target. Never attach to a browser/window/other tab."""
    assert page.url==expected_url,'capture page URL changed'
    target=session.send('Target.getTargetInfo')['targetInfo']
    assert target['type']=='page' and target['url']==expected_url,'capture target is not the fixture page'
    assert isinstance(target['targetId'],str) and target['targetId'],'missing page target identity'
    assert target_id is None or target['targetId']==target_id,'capture target changed'
    dom=page.evaluate('''() => {
      const v=visualViewport,e=document.documentElement,h=document.querySelector('.article-content h2');
      const focus=[];for(let n=document.activeElement;n&&n.parentElement;n=n.parentElement)focus.unshift(Array.from(n.parentElement.children).indexOf(n));
      return {url:location.href,dpr:devicePixelRatio,inner:[innerWidth,innerHeight],outer:[outerWidth,outerHeight],
        client:[e.clientWidth,e.clientHeight],scroll:[scrollX,scrollY],focus,
        visual:{width:v.width,height:v.height,offsetLeft:v.offsetLeft,offsetTop:v.offsetTop,pageLeft:v.pageLeft,pageTop:v.pageTop,scale:v.scale},
        heading:h.getBoundingClientRect().toJSON()};
    }''')
    assert dom['url']==expected_url,'fixture navigated while measuring'
    layout=session.send('Page.getLayoutMetrics')
    assert 'cssVisualViewport' in layout and 'cssLayoutViewport' in layout,'CSS layout metrics unavailable'
    return {'target_id':target['targetId'],'dom':dom,'layout':layout}


def surface_dimensions(png,state):
    """Expected dimensions come from DOM/CDP, never from the PNG itself.

    innerWidth/Height include scrollbars; CDP CSS viewports exclude them. Allow
    one CSS pixel for viewport rounding and one device pixel for raster rounding.
    Pinch/visual zoom and a scrollbar wider than 32 CSS px are unsupported here.
    """
    dom=state['dom'];visual=dom['visual'];cdp=state['layout'];dpr=dom['dpr']
    numbers=[dpr,*dom['inner'],*dom['client'],visual['width'],visual['height'],visual['scale'],visual['offsetLeft'],visual['offsetTop']]
    assert all(type(n) in (int,float) and math.isfinite(n) for n in numbers),'non-finite/bool surface geometry'
    assert 0<dpr<=4 and visual['scale']==1 and visual['offsetLeft']==0 and visual['offsetTop']==0,'unsupported visual zoom/offset'
    expected=[]
    for axis,key in enumerate(('width','height')):
        inner=dom['inner'][axis];client=dom['client'][axis];value=visual[key]
        assert inner>0 and client>0 and 0<=inner-client<=32,'unexpected scrollbar geometry'
        assert abs(value-client)<=1,'visual viewport/client mismatch'
        for vp in ('cssVisualViewport','cssLayoutViewport'):
            measured=cdp[vp]['client'+key.title()]
            assert type(measured) in (int,float) and math.isfinite(measured) and abs(measured-client)<=1,'CDP CSS viewport mismatch'
        pixel_size=round(inner*dpr);expected.append(pixel_size)
        assert abs(png[key]-pixel_size)<=1,('surface is clipped/rescaled',key,png[key],pixel_size)
    return {'expected_pixels':expected,'actual_pixels':[png['width'],png['height']],
        'device_pixel_tolerance':1,'css_rounding_tolerance':1,'scrollbar_css':[a-b for a,b in zip(dom['inner'],dom['client'])]}


def capture_surface(page,session,expected_url,target_id,path,probe=False):
    before=surface_state(page,session,expected_url,target_id)
    record={'before':before,'method':'Page.captureScreenshot fromSurface=true captureBeyondViewport=false; no clip','passed':False}
    try:
        record['probe_count']=page.evaluate("document.querySelectorAll('#native-surface-probe').length")
        assert record['probe_count']==int(probe),'unexpected probe in body capture'
        # Match Playwright screenshot's default caret paint suppression. No blur,
        # focus, animation, CSS zoom, device metrics or other UI change is used.
        page.evaluate('''() => {
          if(document.getElementById('native-capture-caret'))throw Error('caret fixture collision');
          const s=document.createElement('style');s.id='native-capture-caret';
          s.textContent='* { caret-color: transparent !important; }';document.documentElement.append(s);
        }''')
        try:
            assert page.url==expected_url,'capture page URL changed'
            response=session.send('Page.captureScreenshot',{'format':'png','fromSurface':True,'captureBeyondViewport':False})
            data=base64.b64decode(response['data'],validate=True)
            with path.open('xb') as f:f.write(data)
        finally:
            page.evaluate("() => document.getElementById('native-capture-caret')?.remove()")
        after=surface_state(page,session,expected_url,target_id);record['after']=after
        assert before==after,'capture changed target, viewport, focus, scroll or heading'
        assert page.evaluate("document.querySelectorAll('#native-surface-probe').length")==int(probe),'probe changed during capture'
        png=surface_png(data);record['dimensions']=surface_dimensions(png,before)
        record['sha256']=hashlib.sha256(data).hexdigest();record['passed']=True
        return data,png,before
    finally:
        with path.with_suffix('.surface.json').open('x') as f:json.dump(record,f,ensure_ascii=False,indent=2)


def marker_pixel_proof(png,state,markers):
    """Find each full colored rectangle in actual pixels, then compare DOM bounds."""
    assert len(markers)==4 and {m['corner'] for m in markers}=={'tl','tr','bl','br'},'four distinct corners required'
    colors=[tuple(m['rgb']) for m in markers];assert len(set(colors))==4,'duplicate probe colors'
    dpr=state['dom']['dpr'];proof=[]
    for marker in markers:
        color=bytes(marker['rgb'])+(b'\xff' if png['channels']==4 else b'');count=0;left=png['width'];top=png['height'];right=bottom=0
        for y,row in enumerate(png['rows']):
            offset=row.find(color)
            while offset>=0:
                if offset%png['channels']==0:
                    x=offset//png['channels'];count+=1;left=min(left,x);top=min(top,y);right=max(right,x+1);bottom=max(bottom,y+1)
                offset=row.find(color,offset+1)
        assert count,('missing rendered corner',marker['corner'])
        box=[left,top,right,bottom]
        rect=marker['rect'];expected=[rect[k]*dpr for k in ('left','top','right','bottom')]
        assert all(abs(a-b)<=1 for a,b in zip(box,expected)),('marker coordinates differ',marker['corner'],box,expected)
        assert count==(box[2]-box[0])*(box[3]-box[1]) and count>=16,'marker is occluded/nonrectangular'
        x,y=box[0],box[1];corner=marker['corner']
        assert (x<png['width']/4 if corner.endswith('l') else x>png['width']*3/4),'marker not near horizontal edge'
        assert (y<png['height']/4 if corner.startswith('t') else y>png['height']*3/4),'marker not near vertical edge'
        proof.append({'corner':corner,'rgb':marker['rgb'],'actual_pixel_bounds':box,'expected_from_dom':expected,'pixel_count':count})
    return proof


def prove_surface_corners(page,session,expected_url,target_id,path):
    before=surface_state(page,session,expected_url,target_id);record={'before':before,'passed':False}
    assert page.evaluate("document.querySelectorAll('#native-surface-probe').length")==0,'probe fixture collision'
    try:
        markers=page.evaluate('''() => {
          const root=document.createElement('div');root.id='native-surface-probe';root.setAttribute('aria-hidden','true');
          globalThis.__nativeSurfaceOwnedProbe=root;
          root.style.cssText='all:initial!important;position:fixed!important;inset:0!important;pointer-events:none!important;z-index:2147483647!important;';
          document.documentElement.append(root);
          return [['tl',[239,17,173]],['tr',[19,223,131]],['bl',[37,83,241]],['br',[251,163,29]]].map(([corner,rgb])=>{
            const e=document.createElement('div');e.style.cssText='all:initial!important;position:absolute!important;width:12px!important;height:12px!important;opacity:1!important;transform:none!important;border:0!important;pointer-events:none!important;background:rgb('+rgb.join(',')+')!important;'+
              (corner[0]==='t'?'top:4px!important;':'bottom:4px!important;')+(corner[1]==='l'?'left:4px!important;':'right:4px!important;');
            root.append(e);return {corner,rgb,rect:e.getBoundingClientRect().toJSON()};
          });
        }''');record['markers']=markers
        page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        _,png,state=capture_surface(page,session,expected_url,target_id,path,probe=True)
        assert state==before,'probe changed page state'
        record['pixel_proof']=marker_pixel_proof(png,state,markers);record['passed']=True
    finally:
        page.evaluate("() => {globalThis.__nativeSurfaceOwnedProbe?.remove();delete globalThis.__nativeSurfaceOwnedProbe;}")
        page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        record['after']=surface_state(page,session,expected_url,target_id)
        record['restored']=record['after']==before and page.evaluate("document.querySelectorAll('#native-surface-probe, #native-capture-caret').length")==0
        with path.with_suffix('.proof.json').open('x') as f:json.dump(record,f,ensure_ascii=False,indent=2)
        assert record['restored'],'probe did not restore target, scroll, focus, heading and viewport'


def rendered_chinese_fonts(fonts,text):
    points=[ord(c) for c in text if '\u4e00'<=c<='\u9fff'];proof=[]
    installed=subprocess.run(['fc-list','--format=%{postscriptname}\t%{family}\t%{file}\t%{index}\n'],check=True,capture_output=True,text=True,timeout=10).stdout.splitlines()
    faces=[row.split('\t') for row in installed]
    for font in fonts:
        if font['isCustomFont'] or font['glyphCount']<=0:continue
        matches={(row[2],row[3]) for row in faces if len(row)==4 and font['postScriptName'] in [v.strip() for v in row[0].split(',')] and font['familyName'] in [v.strip() for v in row[1].split(',')]}
        assert len(matches)==1,(font,matches)
        file,index=matches.pop()
        charset=subprocess.run(['fc-query','-i',index,'--format=%{charset}',file],check=True,capture_output=True,text=True,timeout=10).stdout
        ranges=[tuple(int(v,16) for v in token.split('-')) for token in charset.split()]
        covers=all(any(r[0]<=cp<=r[-1] for r in ranges) for cp in points)
        proof.append({'family':font['familyName'],'postscript_name':font['postScriptName'],'glyphs':font['glyphCount'],'font_file':os.path.basename(file),'font_index':index,'covers_chinese_codepoints':covers})
    return sum(f['glyphs'] for f in proof if f['covers_chinese_codepoints'])>=len(points)>0,proof

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
    close=p.get_by_role('button',name='关闭文章',exact=True)
    detached_retries=0
    for control in (close,note):
        for attempt in range(3):
            try:
                control.scroll_into_view_if_needed();control.click(trial=True);control.focus();expect(control).to_be_focused()
                break
            except PlaywrightError as error:
                # Native zoom can remount responsive controls between resolution
                # and scroll. Retry only this precise detach, never failed checks.
                if 'Element is not attached to the DOM' not in str(error) or attempt==2:raise
                detached_retries+=1;p.wait_for_timeout(100)
        h.check('native_200_'+('close' if control==close else 'note')+'_intersects_viewport',control.evaluate('e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.left<innerWidth&&r.right>0&&r.top<innerHeight&&r.bottom>0}'))
    expect(note).to_be_enabled();expect(note).to_have_value(h.notes['101'])
    h.check('native_200_close_and_note_reachable')
    heading=p.locator('.article-content h2').first;expect(heading).to_contain_text(title)
    heading.scroll_into_view_if_needed();p.evaluate('document.fonts.ready');p.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    session=ctx.new_cdp_session(p);session.send('DOM.enable');session.send('CSS.enable')
    root=session.send('DOM.getDocument')['root']['nodeId']
    node=session.send('DOM.querySelector',{'nodeId':root,'selector':'.article-content h2'})['nodeId']
    fonts=session.send('CSS.getPlatformFontsForNode',{'nodeId':node})['fonts']
    (h.out/'native-metrics.json').write_text(json.dumps({'native_zoom':zoom,'baseline':baseline,'observed':metrics,'detached_control_retries':detached_retries,'rendered_fonts':fonts,'heading':heading.evaluate('e=>({html:e.outerHTML,rect:e.getBoundingClientRect().toJSON(),font:getComputedStyle(e).fontFamily})'),'method':'chrome.tabs.setZoom/getZoom in headed Chromium/Xvfb','physical_android_ios':False},ensure_ascii=False,indent=2))
    expected_url=h.base+'/inbox/all/entry/101'
    capture_target=surface_state(p,session,expected_url)['target_id']
    prove_surface_corners(p,session,expected_url,capture_target,h.out/'native-200-corner-probe.png')
    shot,_,_=capture_surface(p,session,expected_url,capture_target,h.out/'native-200.png')
    print('Rendered fonts:',json.dumps(fonts,ensure_ascii=False),flush=True)
    chinese_ok,font_proof=rendered_chinese_fonts(fonts,title)
    (h.out/'native-font-coverage.json').write_text(json.dumps(font_proof,ensure_ascii=False,indent=2))
    h.check('native_200_actual_cjk_glyphs',chinese_ok)
    h.check('native_200_western_font_rejected',any(f['family']=='DejaVu Sans' and not f['covers_chinese_codepoints'] for f in font_proof))
    offset=8;compressed=b''
    while offset<len(shot):
        size=struct.unpack('>I',shot[offset:offset+4])[0];kind=shot[offset+4:offset+8]
        if kind==b'IDAT':compressed+=shot[offset+8:offset+8+size]
        offset+=size+12
    h.check('native_200_visual_capture_not_blank',len(set(zlib.decompress(compressed)))>8)
    stable_before=heading.evaluate('e=>e.getBoundingClientRect().toJSON()');p.wait_for_timeout(300)
    stable_after=heading.evaluate('e=>e.getBoundingClientRect().toJSON()');second,_,_=capture_surface(p,session,expected_url,capture_target,h.out/'native-200-after300ms.png')
    (h.out/'native-stability.json').write_text(json.dumps({'wait_ms':300,'before':stable_before,'after':stable_after,'before_sha256':hashlib.sha256(shot).hexdigest(),'after_sha256':hashlib.sha256(second).hexdigest()},indent=2))
    h.check('native_200_capture_stable_300ms',stable_before==stable_after and shot==second)
    h.check('native_200_does_not_save_note',not h.note_writes)
    assert worker.evaluate('([url,value])=>zoomFixture(url,value)',[p.url,1])==1
except Exception as exc:
    h.errors.append(type(exc).__name__+': '+str(exc)[:500]);raise
finally:
    if ctx:ctx.close()
    h.close()
