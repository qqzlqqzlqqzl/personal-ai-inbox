"""Uncommitted review candidate: five cold/warm pairs against a bounded HTTP fixture.

No context/page route, downloads, production URL, real account, subprocess installer,
or arbitrary Chromium executable. NOT_RUN is a failing exit, never a green result.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import statistics
import stat
import sys
import tempfile
import time
from urllib.parse import urlsplit
import zipfile

from reader_loading_fixture import Fixture, TOKEN, SCENARIO, admit_build, bound_read, checked_directory, require

PLAYWRIGHT = '1.63.0'
CHROMIUM = '153.0.8010.12'
REVISION = 'chromium-1243'
PROBE = """(() => {
  window.__perfProbe = {images:[], prefetch:[], violations:[], longTasks:[]};
  const at=()=>performance.timeOrigin+performance.now();
  addEventListener('load', e=>{if(e.target instanceof HTMLImageElement)
    window.__perfProbe.images.push({src:new URL(e.target.currentSrc).pathname,at:at(),width:e.target.naturalWidth,height:e.target.naturalHeight})},true);
  addEventListener('inbox:prefetch',e=>window.__perfProbe.prefetch.push({at:at(),...e.detail}));
  addEventListener('securitypolicyviolation',e=>window.__perfProbe.violations.push({at:at(),directive:e.effectiveDirective}));
  new PerformanceObserver(list=>{for(const e of list.getEntries())window.__perfProbe.longTasks.push({start:e.startTime,duration:e.duration})}).observe({type:'longtask',buffered:true});
})()"""


class NotRun(RuntimeError): pass


def save_new(path, value):
    path = Path(path)
    parent = checked_directory(path.parent)
    root_fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parent.parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            os.close(root_fd); root_fd = following
        current = os.fstat(root_fd)
        require(current.st_uid == os.getuid() and stat.S_IMODE(current.st_mode) == 0o700,
                'receipt directory must be owned and private')
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        visible = parent.stat(follow_symlinks=False)
        require((current.st_dev,current.st_ino)==(visible.st_dev,visible.st_ino), 'receipt parent replaced; bytes retained at bound parent')
    finally:
        os.close(root_fd)


def browser_env(output):
    result = {'PATH': '/usr/bin:/bin', 'HOME': str(output/'home'), 'TMPDIR': str(output/'tmp'),
              'LANG': 'C.UTF-8', 'TZ': 'UTC', 'XDG_CACHE_HOME': str(output/'cache'),
              'XDG_CONFIG_HOME': str(output/'config')}
    for key in ('HOME','TMPDIR','XDG_CACHE_HOME','XDG_CONFIG_HOME'):
        Path(result[key]).mkdir(mode=0o700)
    return result


def artifact_identity(path, expected_sha, files, expected_tree):
    p = Path(os.path.abspath(path))
    raw = bound_read(p.parent, p.name)
    require(hashlib.sha256(raw).hexdigest() == expected_sha, 'artifact digest mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        require(len(z.infolist()) <= 1024 and sum(m.file_size for m in z.infolist()) <= 64*1024*1024,
                'artifact unpacked-size or member cap exceeded')
        require(z.testzip() is None, 'artifact CRC failure')
        identity = json.loads(z.read('artifacts/ci-reader-identity.json'))
        require(identity['tree'] == expected_tree and identity.get('passed') is True, 'artifact source identity mismatch')
        prefix = 'runtime/browser-build/'
        names = {n[len(prefix):] for n in z.namelist() if n.startswith(prefix) and not n.endswith('/')}
        require(names == set(files), 'artifact build set mismatch')
        for name, data in files.items():
            require(z.read(prefix+name) == data, 'artifact build bytes differ')
    return {'head': identity['head'], 'tree': identity['tree'], 'src': identity['src_tree'],
            'artifact_sha256': expected_sha, 'artifact_bytes': len(raw),
            'reactflux': identity['reactflux'], 'pnpm_lock_sha256': identity['pnpm_lock_sha256']}


def count_images(records):
    return sum(r['label'].startswith('image-') and r['method']=='GET' for r in records)


def install_network_observer(context, page, events, base):
    session = context.new_cdp_session(page)
    session.send('Network.enable')
    session.send('Network.setCacheDisabled', {'cacheDisabled': False})
    def record(kind, row):
        if len(events) >= 12000: raise AssertionError('network evidence budget exhausted')
        out = {'kind': kind, 'request_id': row.get('requestId'), 'timestamp': row.get('timestamp')}
        if kind == 'request':
            request = row['request']; parsed = urlsplit(request['url'])
            out.update(path=parsed.path if request['url'].startswith(base+'/') else '[blocked-origin]',
                       method=request['method'], type=row.get('type'), wall_time=row.get('wallTime'),
                       priority=request.get('initialPriority'))
        elif kind == 'response':
            response = row['response']
            out.update(status=response['status'], from_disk_cache=response.get('fromDiskCache',False),
                       from_service_worker=response.get('fromServiceWorker',False), timing=response.get('timing'),
                       headers={k:v for k,v in response.get('headers',{}).items() if k.lower() in
                                ('cache-control','etag','content-length','content-type','age','last-modified')})
        elif kind == 'finished': out['encoded_bytes'] = row.get('encodedDataLength')
        elif kind == 'failed': out.update(error=row.get('errorText'), canceled=row.get('canceled',False))
        events.append(out)
    for event, kind in [('requestWillBeSent','request'),('responseReceived','response'),
                        ('loadingFinished','finished'),('loadingFailed','failed'),('requestServedFromCache','cache-hit')]:
        session.on('Network.'+event, lambda row,kind=kind: record(kind,row))
    return session


def activate(locator, input_kind):
    if input_kind == 'touch': locator.tap()
    else: locator.focus(); locator.press('Enter')


def open_article(page, input_kind, number=1):
    before = time.monotonic()
    activate(page.locator(f'.entry-list [data-entry-id="{number}"]').first, input_kind)
    page.locator('.article-body').wait_for(state='visible')
    page.wait_for_function("(document.querySelector('.article-body')?.innerText.length||0)>100 && !document.querySelector('.article-body')?.getAttribute('aria-busy')")
    return (time.monotonic()-before)*1000


def close_article(page, input_kind):
    activate(page.get_by_role('button', name='关闭文章', exact=True), input_kind)
    page.locator('.article-body').wait_for(state='hidden')


def image_sweep(page):
    rows=[]
    for number in range(1,7):
        item=page.locator(f'.article-body img[src="/fixture-images/{number}.png"]').first
        start=time.monotonic(); item.scroll_into_view_if_needed()
        page.wait_for_function("src=>{const e=document.querySelector('.article-body img[src=\"'+src+'\"]');return e?.complete&&e.naturalWidth>0}", arg=f'/fixture-images/{number}.png')
        detail=item.evaluate("async e=>{await e.decode();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const b=e.getBoundingClientRect();return {naturalWidth:e.naturalWidth,naturalHeight:e.naturalHeight,width:b.width,height:b.height,top:b.top,visible:b.bottom>0&&b.top<innerHeight,loading:e.loading,fetchPriority:e.fetchPriority,at:performance.timeOrigin+performance.now()}}")
        require(detail['visible'], 'loaded image is not in viewport')
        rows.append({'image':number,'scroll_to_visible_ms':(time.monotonic()-start)*1000,**detail})
    return rows


def join_image_network(rows, events):
    for image in rows:
        matches=[event for event in events if event['kind']=='request' and event.get('path')==f"/fixture-images/{image['image']}.png"]
        if not matches:
            image['network_timing']='NO_REQUEST_EVENT_OBSERVED'
            continue
        request=matches[-1]
        related=[event for event in events if event.get('request_id')==request['request_id']]
        finishes=[event for event in related if event['kind']=='finished']
        responses=[event for event in related if event['kind']=='response']
        image['request_to_visible_ms']=image['at']-request['wall_time']*1000
        image['request_id']=request['request_id']
        image['browser_cache_event']=any(event['kind']=='cache-hit' for event in related)
        if finishes:
            image['request_to_finished_ms']=(finishes[-1]['timestamp']-request['timestamp'])*1000
            image['encoded_bytes']=finishes[-1].get('encoded_bytes')
        if responses:
            image['response_status']=responses[-1]['status']
            image['from_disk_cache']=responses[-1]['from_disk_cache']
            image['http_timing']=responses[-1].get('timing')
    return rows


def one_pair(browser, fixture, output, index, input_kind, weak):
    from playwright.sync_api import expect
    options = {'viewport': {'width':390,'height':844} if input_kind=='touch' else {'width':1440,'height':960},
               'is_mobile': input_kind=='touch', 'has_touch':input_kind=='touch',
               'locale':'zh-CN','service_workers':'block','accept_downloads':False}
    context = browser.new_context(**options)
    events=[]; errors=[]; page=None
    pair={'pair':index,'status':'FAILED','input':input_kind,'hover_dwell':False,'weak_network':weak}
    start_record=len(fixture.records)
    try:
        context.add_init_script("localStorage.setItem('auth',JSON.stringify({server:location.origin+'/mf',token:"+json.dumps(TOKEN)+",username:'',password:''}));localStorage.setItem('settings',JSON.stringify({articleListLayout:'card',showStatus:'all',theme:'light',markReadOnScroll:false}));localStorage.setItem('ai-view-state',JSON.stringify({mode:'recommended',minimum:6,sort:'score',direction:'desc',auxiliary:'none'}));")
        context.add_init_script(PROBE)
        page=context.new_page(); page.set_default_timeout(15000); page.set_default_navigation_timeout(15000)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.on('download', lambda download: download.cancel())
        session=install_network_observer(context,page,events,fixture.base)
        if weak:
            session.send('Network.emulateNetworkConditions',{'offline':False,'latency':150,'downloadThroughput':250000,'uploadThroughput':125000,'connectionType':'cellular3g'})
        began=time.monotonic();page.goto(fixture.base+'/inbox/all',wait_until='domcontentloaded')
        expect(page.locator('.load-more-container')).to_have_attribute('data-loaded-count','24')
        pair['list_initial_ms']=(time.monotonic()-began)*1000
        page.mouse.move(0,0)
        pair['before_activation_requests']=len(fixture.records)-start_record
        pair['before_activation_detail_requests']=sum(r['label'].startswith('detail:') for r in fixture.records[start_record:])
        pair['cold_click_to_body_ms']=open_article(page,input_kind)
        pair['cold_images']=image_sweep(page)
        join_image_network(pair['cold_images'],events)
        pair['cold_image_http_requests']=count_images(fixture.records[start_record:])
        require(pair['cold_image_http_requests']>=6, 'cold context did not issue all six image requests')
        close_article(page,input_kind)
        warm_start=len(fixture.records);event_start=len(events)
        pair['warm_click_to_body_ms']=open_article(page,input_kind)
        pair['warm_images']=image_sweep(page)
        join_image_network(pair['warm_images'],events[event_start:])
        pair['warm_image_http_requests']=count_images(fixture.records[warm_start:])
        pair['warm_cdp_cache_events']=sum(x['kind']=='cache-hit' for x in events[event_start:])
        pair['warm_cdp_image_disk_hits']=sum(x['kind']=='response' and x.get('from_disk_cache') for x in events[event_start:])
        pair['warm_detail_http_requests']=sum(r['label']=='detail:1' for r in fixture.records[warm_start:])
        require(pair['warm_image_http_requests']==0, 'warm image issued another HTTP GET despite fresh cache')
        require(pair['warm_cdp_cache_events']+pair['warm_cdp_image_disk_hits']>0, 'no browser cache event corroborates warm phase')
        close_article(page,input_kind)
        pair['next_pages']=[]
        for target, expected in [(7,48),(31,72)]:
            start=time.monotonic()
            page.locator(f'.entry-list [data-entry-id="{target}"]').first.evaluate("e=>e.scrollIntoView({block:'start',behavior:'instant'})")
            page.locator('.entry-list').evaluate("root=>{const scroll=root.querySelector('.simplebar-content-wrapper,.scroll-container')||root;scroll.scrollTop=scroll.scrollHeight}")
            bottom=time.monotonic()
            expect(page.locator('.load-more-container')).to_have_attribute('data-loaded-count',str(expected))
            finished=time.monotonic()
            pair['next_pages'].append({'target_entry':target,'loaded':expected,'trigger_to_append_ms':(finished-start)*1000,
                                       'fast_scroll_bottom_wait_ms':(finished-bottom)*1000})
        expect(page.locator('.load-more-container')).to_have_attribute('data-more','false')
        pair['list_http_offsets']=[r['label'] for r in fixture.records[start_record:] if r['label'].startswith('list:')]
        require(pair['list_http_offsets']==['list:0:24','list:24:24','list:48:24'], 'pagination refetched or skipped a bounded page')
        pair['probe']=page.evaluate('window.__perfProbe')
        require(not errors, 'browser emitted a page error')
        require(not any(r['label'] in ('unsupported-api','budget') for r in fixture.records[start_record:]), 'unsupported API or budget exhaustion')
        pair.update(status='PASSED',http_records=fixture.records[start_record:],cdp=events,errors=errors)
    except Exception as exc:
        pair.update(error_type=type(exc).__name__,error=str(exc),http_records=fixture.records[start_record:],cdp=events,errors=errors)
        if page:
            try: page.screenshot(path=str(output/f'pair-{index}-failure.png'),full_page=False)
            except Exception as screenshot_error: pair['screenshot_error']=type(screenshot_error).__name__+': '+str(screenshot_error)
        raise
    finally:
        close_error=None
        try: context.close()
        except Exception as exc:
            close_error=exc
            pair['context_close_error']=type(exc).__name__+': '+str(exc)
        was_passed=pair['status']=='PASSED'
        if close_error: pair['status']='FAILED'
        save_new(output/f'pair-{index}.json',pair)
        if close_error and was_passed: raise close_error
    return pair


def boundary_check(browser, fixture):
    """Actual browser proves unknown HTTP/CONNECT and other-loopback do not bypass proxy."""
    import http.server, threading
    from urllib.request import build_opener, ProxyHandler
    arrivals=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            arrivals.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'positive')
        def log_message(self,*args):pass
    sentinel=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=sentinel.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{sentinel.server_port}'
    context=browser.new_context(service_workers='block',accept_downloads=False)
    try:
        with build_opener(ProxyHandler({})).open(origin+'/parent-positive-control',timeout=3) as response:
            require(response.read()==b'positive','sentinel positive control failed')
        page=context.new_page();page.set_default_navigation_timeout(5000)
        start=len(fixture.records)
        for url in [origin+'/must-not-arrive','http://reader-perf-external.invalid/blocked','https://reader-perf-external.invalid/blocked']:
            try: page.goto(url,wait_until='domcontentloaded')
            except Exception: pass
        observed=fixture.records[start:]
        require(arrivals==['/parent-positive-control'],'browser escaped exact fixture origin')
        require(sum(r['label']=='blocked-origin' for r in observed)>=2 and any(r['label']=='blocked-connect' for r in observed),'proxy rejection did not observe all controls')
        return {'status':'PASSED','sentinel_parent_positive':1,'sentinel_browser_arrivals':0,'rejections':observed}
    finally:
        context.close();sentinel.shutdown();sentinel.server_close();thread.join(timeout=3)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',required=True);parser.add_argument('--manifest',required=True)
    parser.add_argument('--manifest-sha',required=True);parser.add_argument('--artifact-zip',required=True)
    parser.add_argument('--artifact-sha',required=True);parser.add_argument('--source-tree',required=True)
    parser.add_argument('--phase',choices=['baseline','candidate'],required=True)
    parser.add_argument('--output-parent',required=True);parser.add_argument('--input',choices=['keyboard','touch'],default='keyboard')
    parser.add_argument('--weak-network',action='store_true')
    args=parser.parse_args()
    parent=checked_directory(args.output_parent)
    require(not parent.is_relative_to(checked_directory(args.build)), 'output parent must be outside build')
    output=Path(tempfile.mkdtemp(prefix='reader-perf-',dir=parent))
    report={'status':'FAILED','phase':args.phase,'pairs_requested':5,'pairs':[], 'production':False,
            'physical_device':False,'http_cache_routing_disabled':False,'scenario':SCENARIO,'output':str(output),
            'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()), 'input':args.input,'weak_network':args.weak_network}
    report['python']=sys.version
    report['entry_argv']=sys.argv
    fixture=None;browser=None;pw=None
    try:
        files,build=admit_build(args.build,args.manifest,args.manifest_sha)
        report['build']=build
        report['identity']=artifact_identity(args.artifact_zip,args.artifact_sha,files,args.source_tree)
        require(importlib.metadata.version('playwright')==PLAYWRIGHT,'unexpected Playwright version')
        report['playwright']=PLAYWRIGHT
        from playwright.sync_api import sync_playwright
        pw=sync_playwright().start()
        executable=Path(pw.chromium.executable_path)
        if not executable.is_file(): raise NotRun('Pinned full Chromium is missing: '+str(executable))
        checked_directory(executable.parent)
        require(REVISION in executable.parts and not executable.is_symlink(),'unexpected browser path')
        fixture=Fixture(files)
        env=browser_env(output)
        report['browser_environment_names']=sorted(env)
        report['browser_executable_sha256']=hashlib.sha256(executable.read_bytes()).hexdigest()
        browser=pw.chromium.launch(channel='chromium',headless=True,timeout=15000,env=env,
            proxy={'server':fixture.base,'bypass':'<-loopback>'},
            args=['--disable-background-networking','--disable-component-update','--disable-quic',
                  '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'])
        require(browser.version==CHROMIUM,'unexpected real Chromium version')
        report['browser_version']=browser.version
        report['boundary']=boundary_check(browser,fixture)
        save_new(output/'boundary.json',report['boundary'])
        for index in range(1,6):
            report['pairs'].append(one_pair(browser,fixture,output,index,args.input,args.weak_network))
        report['summary']={key:{'median':statistics.median(row[key] for row in report['pairs']),
                                'min':min(row[key] for row in report['pairs']),'max':max(row[key] for row in report['pairs'])}
                           for key in ['list_initial_ms','cold_click_to_body_ms','warm_click_to_body_ms']}
        report['status']='PASSED'
    except NotRun as exc:
        report.update(status='NOT_RUN',error_type=type(exc).__name__,error=str(exc))
    except Exception as exc:
        report.update(status='FAILED',error_type=type(exc).__name__,error=str(exc))
    finally:
        for name,handle in [('browser',browser),('playwright',pw),('fixture',fixture)]:
            if handle:
                try:
                    handle.stop() if name=='playwright' else handle.close()
                except Exception as exc:
                    report.setdefault('close_errors',[]).append({'resource':name,'type':type(exc).__name__,'error':str(exc)})
                    if report['status']=='PASSED': report['status']='FAILED'
        if fixture: report['server_records']=list(fixture.records)
        report['finished_utc']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
        # Detailed pairs have their own immutable files; avoid duplicating large event lists.
        report['pairs']=[{k:v for k,v in p.items() if k not in ('cdp','http_records','probe')} for p in report['pairs']]
        save_new(output/'result.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='PASSED' else 1


if __name__=='__main__': raise SystemExit(main())
