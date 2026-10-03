"""Actual Chromium single-hop admission regression; synthetic localhost only."""
import asyncio,json,os,threading
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from collections import Counter
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from fulltext_source import _fetch_browser_locked,FulltextUnavailable
from dispatch_policy import ConfigGuard,DispatchStopped

async def run():
    with TemporaryDirectory(prefix='browser-admission-') as tmp:
        folder=Path(tmp);config=folder/'config.json';calls=Counter();stop_on_redirect=False
        def enable():config.write_text(json.dumps({'schedule_enabled':True}))
        enable();guard=ConfigGuard(config,json.loads(config.read_text()),pause_file=folder/'paused.json')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls[self.path]+=1
                if self.path=='/redirect':
                    if stop_on_redirect:config.write_text(json.dumps({'schedule_enabled':False}))
                    self.send_response(307);self.send_header('Location','/landing');self.end_headers();return
                body=('<html><head><script>function openPopup(){window.open("/popup")}</script></head><body><article>'+('Complete synthetic article with inspectable text. '*30)+'</article></body></html>').encode()
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            result=await _fetch_browser_locked(base+'/direct','browser:article',[],[],admission=guard)
            assert len(result['source_text'])>120 and calls['/direct']==1
            try:await _fetch_browser_locked(base+'/redirect','browser:article',[],[],admission=guard)
            except FulltextUnavailable as error:assert str(error)=='browser_redirect_not_admitted'
            else:raise AssertionError('enabled browser redirect was admitted')
            assert calls['/landing']==0 and calls['/redirect']==1
            stop_on_redirect=True
            try:await _fetch_browser_locked(base+'/redirect','browser:article',[],[],admission=guard)
            except DispatchStopped as error:assert error.state=='schedule_disabled'
            else:raise AssertionError('stopped browser redirect was admitted')
            assert calls['/landing']==0 and calls['/redirect']==2
            # Load a real script while enabled, then stop and invoke its popup.
            # Instrument routing and navigation only; real Chromium/localhost
            # transport still runs, and the first popup request must be admitted
            # by the same context guard before any server request.
            enable();popup_seen=asyncio.Event();popup_attempts=[]
            @asynccontextmanager
            async def instrumented_playwright():
                async with async_playwright() as runtime:
                    original_launch=runtime.chromium.launch
                    async def launch(**kwargs):
                        browser=await original_launch(**kwargs);original_page=browser.new_page
                        async def new_page(**kwargs):
                            page=await original_page(**kwargs);context=page.context
                            original_route=context.route;original_goto=page.goto
                            async def route(pattern,callback):
                                async def observed(request):
                                    popup=request.request.url.endswith('/popup')
                                    if popup:popup_attempts.append(True)
                                    try:await callback(request)
                                    finally:
                                        if popup:popup_seen.set()
                                await original_route(pattern,observed)
                            context.route=route
                            async def goto(*args,**kwargs):
                                response=await original_goto(*args,**kwargs)
                                config.write_text(json.dumps({'schedule_enabled':False}))
                                await page.evaluate('openPopup()')
                                await asyncio.wait_for(popup_seen.wait(),3)
                                return response
                            page.goto=goto
                            return page
                        browser.new_page=new_page
                        return browser
                    runtime.chromium.launch=launch
                    yield runtime
            with patch('playwright.async_api.async_playwright',instrumented_playwright):
                try:await _fetch_browser_locked(base+'/loaded-script','browser:article',[],[],admission=guard)
                except DispatchStopped as error:assert error.state=='schedule_disabled'
                else:raise AssertionError('stopped popup was admitted')
            assert popup_attempts==[True] and calls['/popup']==0
            evidence={'passed':True,'direct_article':True,'enabled_redirect_blocked':True,'stopped_redirect_blocked':True,'landing_requests':0,'popup_initial_requests_blocked':len(popup_attempts),'popup_server_requests':0,'calls':dict(calls),'isolated':True}
            output=Path(os.environ.get('BROWSER_ADMISSION_REPORT','artifacts/browser-admission.json'));output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence))
        finally:server.shutdown();server.server_close()

if __name__=='__main__':
    with patch('fulltext_source._browser_executable',lambda:os.environ.get('AI_NEWS_CHROME_PATH') or None),patch.dict(os.environ,{'AI_NEWS_OUTBOUND_PROXY':''}):
        asyncio.run(run())
