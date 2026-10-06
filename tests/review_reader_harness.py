"""Serve only an isolated built reader; intercept all APIs and reject external HTTP."""
import functools,json,os,re,threading
from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]

class Harness:
    def __init__(self,name,*,timezone_id=None,persist_auth=False,sandboxed=False,**context_options):
        if type(sandboxed) is not bool:raise ValueError('sandboxed must be an explicit bool')
        if sandboxed:
            from reader_sandboxed_browser import preflight,validate_context_options
            validate_context_options(context_options)
            preflight()
        value=os.environ.get('AI_NEWS_TEST_BUILD')
        if not value:raise RuntimeError('AI_NEWS_TEST_BUILD must name a local isolated build')
        self.build=Path(value).resolve()
        if not (self.build/'index.html').is_file() or self.build==Path('/home/ubuntu/ai-news/upstream/reactflux/dist'):raise RuntimeError('Invalid isolated build')
        build=self.build
        class Handler(SimpleHTTPRequestHandler):
            def do_CONNECT(self):
                self.send_error(403 if sandboxed else 501,'fixture proxy rejects external CONNECT')
            def allowed_origin(self):
                parsed=urlsplit(self.path);authority=f'127.0.0.1:{self.server.server_port}'
                return self.headers.get('Host')==authority and (not parsed.scheme or (parsed.scheme=='http' and parsed.netloc==authority))
            def do_HEAD(self):
                if sandboxed and not self.allowed_origin():
                    self.send_error(403,'fixture proxy rejects other origins');return
                super().do_HEAD()
            def do_GET(self):
                if sandboxed and not self.allowed_origin():
                    self.send_error(403,'fixture proxy rejects other origins');return
                path=urlsplit(self.path).path.removeprefix('/inbox/')
                self.path='/'+path if (build/path).is_file() else '/index.html';super().do_GET()
            def log_message(self,*a):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(build)))
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.out=ROOT/'runtime'/name;self.out.mkdir(parents=True,exist_ok=True)
        self.pw=sync_playwright().start()
        self.sandboxed_browser_receipt=None
        if sandboxed:
            from reader_sandboxed_browser import launch
            try:self.browser,self.sandboxed_browser_receipt=launch(self.pw,self.base)
            except Exception:
                self.pw.stop();self.server.shutdown();self.server.server_close();raise
        else:
            self.browser=self.pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE') or None,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        self.ctx=self.browser.new_context(**{'viewport':{'width':1440,'height':960},'locale':'zh-CN','service_workers':'block','timezone_id':timezone_id,**({'accept_downloads':False} if sandboxed else {}),**context_options})
        auth_script="localStorage.setItem('auth',JSON.stringify({server:location.origin+'/mf',token:'isolated-test-token',username:'',password:''}))"
        self.ctx.add_init_script("if(!localStorage.getItem('auth')){"+auth_script+"}" if persist_auth else auth_script)
        self.errors=[];self.calls=[];self.writes=[];self.checks={};self.custom=None
        self.categories=[{'id':1,'title':'技术博客'},{'id':2,'title':'设计'}]
        self.feeds=[{'id':7,'user_id':1,'title':'已订阅技术源','feed_url':'https://example.test/existing.xml','site_url':'https://example.test','category':self.categories[0],'icon':{'feed_id':7,'icon_id':0,'external_icon_id':''}}]
        self.catalog=[{'name':'已订阅技术源','url':self.feeds[0]['feed_url'],'category':'技术博客','status':'subscribed','subscribed':True,'feed_id':7,'analysis_supported':True,'subscription_supported':False}]+[{'name':f'技术候选{i:02d}','url':f'https://example.test/tech{i}.xml','category':'技术博客','status':'ok','subscribed':False,'analysis_supported':True,'subscription_supported':True} for i in range(28)]+[{'name':'设计候选','url':'https://example.test/design.xml','category':'设计','status':'ok','subscribed':False,'analysis_supported':True,'subscription_supported':True},{'name':'错误来源','url':'https://example.test/broken.xml','category':'设计','status':'blocked','live_error':'HTTP 403','analysis_supported':True,'subscription_supported':False}]
        self.settings={'enabled':False,'translation_enabled':False,'base_url':'https://example.test/v1','model':'fixture-model','prompt':'初始测试提示词','minimum_score':6,'daily_articles':80,'daily_tokens':500000,'max_chars':40000,'json_mode':True}
        self.status={'counts':{},'coverage':{'total_articles':0,'source_count':1},'usage':[],'events':[],'resources':{},'kaggle':{'enabled':False}}
        self.entries=[];self.notes={};self.note_writes=[]
        self.ctx.route('**/mf/**',self.route)
        self.ctx.route(lambda url:(urlsplit(url).scheme,urlsplit(url).netloc)!=(urlsplit(self.base).scheme,urlsplit(self.base).netloc),lambda r:r.abort())
        self.page=self.ctx.new_page();self.page.set_default_timeout(15000);self.page.on('pageerror',lambda e:self.errors.append(str(e)))
    def route(self,route):
        request=route.request;path=urlsplit(request.url).path;method=request.method
        self.calls.append((method,path))
        if method not in ('GET','HEAD','OPTIONS'):self.writes.append((method,path,request.post_data_json))
        if self.custom and self.custom(route,path,method):return
        if path.endswith('/ai/settings'):
            if method=='PUT':self.settings.update(request.post_data_json or {})
            body=self.settings
        elif path.endswith('/ai/status'):body=self.status
        elif path=='/mf/v1/ai/scope-counts' and method=='GET':
            # Custom list transports may own a larger set than these detail rows.
            # No complete batch snapshot is declared by this generic fixture.
            route.fulfill(status=503,json={'error_message':'Fixture scope-count metadata is unavailable'},headers={'Cache-Control':'no-store'});return
        elif path.endswith('/ai/catalog'):body=self.catalog
        elif path.endswith('/ai/x/roster'):body={'counts':{'total':0},'sources':[]}
        elif path.endswith('/me'):body={'id':1,'username':'fixture-owner','is_admin':True}
        elif path.endswith('/version'):body={'version':'2.3.3'}
        elif path.endswith('/categories'):
            if method=='POST':
                item={'id':len(self.categories)+1,'title':request.post_data_json['title']};self.categories.append(item);body=item
            else:body=self.categories
        elif path.endswith('/feeds/counters'):body={'reads':{},'unreads':{}}
        elif path.endswith('/feeds'):body=self.feeds
        elif '/ai/notes/' in path:
            eid=path.rsplit('/',1)[-1]
            if method=='PUT':self.notes[eid]=(request.post_data_json or {}).get('note','');self.note_writes.append((eid,self.notes[eid]))
            body={'note':self.notes.get(eid,''),'note_count':len(self.notes.get(eid,'')),'updated_at':'2026-10-01T12:00:00Z'}
        elif path.endswith('/ai/subscribe'):
            item=request.post_data_json;fid=len(self.feeds)+7
            feed={'id':fid,'user_id':1,'title':item['url'],'feed_url':item['url'],'site_url':'https://example.test','category':next(c for c in self.categories if c['id']==item['category_id']),'icon':{'feed_id':fid,'icon_id':0,'external_icon_id':''}};self.feeds.append(feed)
            for source in self.catalog:
                if source['url']==item['url']:source.update(subscribed=True,feed_id=fid,status='subscribed')
            body=feed
        elif path.endswith('/entries'):body={'total':len(self.entries),'entries':self.entries}
        elif '/v1/entries/' in path:
            eid=path.rsplit('/',1)[-1];body=next((e for e in self.entries if str(e['id'])==eid),{})
        else:body={}
        route.fulfill(json=body)
    def goto(self,path='/inbox/today'):
        route_path=urlsplit(path).path
        detail=re.fullmatch(r'/inbox/[^/]+/entry/([1-9][0-9]*)',route_path)
        entry=None
        if '/entry/' in route_path:
            if not detail:raise ValueError('invalid isolated article route')
            matches=[item for item in self.entries if str(item['id'])==detail.group(1)]
            if len(matches)!=1:raise ValueError('article route requires one matching isolated fixture')
            entry=matches[0]
        self.page.goto(self.base+path,wait_until='domcontentloaded')
        if entry is None:
            self.page.get_by_role('button',name='AI 精选',exact=True).wait_for()
        else:
            # On mobile the background list is correctly inert/aria-hidden.
            # Wait for the requested visible article, within the original timeout.
            article=self.page.locator('.article-content')
            expect(article).to_be_visible()
            expect(article.locator('.article-title')).to_have_text(entry['title'])
            body=article.locator('.article-body')
            expect(body).to_be_visible()
            expect(body).not_to_have_attribute('aria-busy','true')
            expect(article.locator('.article-source-footer a')).to_have_attribute('href',entry['url'])
    def panel(self):
        # Viewport changes return before the matchMedia listener/React commit.
        # Wait for the expected responsive opener instead of branching on count.
        compact=self.page.evaluate("matchMedia('(max-width: 768px), (pointer: coarse) and (max-height: 500px)').matches")
        if compact:
            trigger=self.page.locator('.ai-toolbar > .ai-status-trigger')
            expect(trigger).to_be_visible();trigger.click()
        else:expect(self.page.locator('.ai-toolbar > .ai-settings-button')).to_be_visible()
        self.page.get_by_role('button',name='AI 设置 · 来源',exact=True).click();expect(self.page.locator('.ai-dialog')).to_be_visible()
    def check(self,name,value=True):
        self.checks[name]=bool(value);print('PASS' if value else 'FAIL',name,flush=True)
        if not value:raise AssertionError(name)
    def close(self):
        result={'checks':self.checks,'errors':self.errors,'passed':bool(self.checks) and all(self.checks.values()) and not self.errors,'isolated':True,'api_writes':len(self.writes)}
        if self.sandboxed_browser_receipt is not None:result['sandboxed_browser']=self.sandboxed_browser_receipt
        (self.out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        self.browser.close();self.pw.stop();self.server.shutdown();self.server.server_close()
        if not result['passed']:raise AssertionError(result)
