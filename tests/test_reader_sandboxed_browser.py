import ast
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import reader_sandboxed_browser as s


class SandboxContracts(unittest.TestCase):
    def fake_runtime(self, *, version=s.VERSION):
        root=Path(tempfile.mkdtemp(prefix='sandbox-contract-retained-'))
        executable=root/'chromium-1243/chrome-linux64/chrome'
        executable.parent.mkdir(parents=True);executable.write_bytes(b'synthetic executable, never run')
        browser=MagicMock();browser.version=version
        chromium=MagicMock();chromium.executable_path=str(executable);chromium.launch.return_value=browser
        return SimpleNamespace(chromium=chromium),executable,browser

    def test_launch_is_explicit_sandbox_pinned_channel_clean_env_and_exact_proxy(self):
        pw,exe,browser=self.fake_runtime()
        with patch.object(s,'preflight'),patch.object(s,'BINARY_SHA',hashlib.sha256(exe.read_bytes()).hexdigest()):
            actual,receipt=s.launch(pw,'http://127.0.0.1:43210')
        self.assertIs(actual,browser)
        kw=pw.chromium.launch.call_args.kwargs
        self.assertIs(kw['chromium_sandbox'],True);self.assertEqual(kw['channel'],'chromium')
        self.assertNotIn('executable_path',kw);self.assertNotIn('--no-sandbox',kw['args'])
        self.assertEqual(kw['proxy'],{'server':'http://127.0.0.1:43210','bypass':'<-loopback>'})
        self.assertEqual(set(kw['env']),{'PATH','HOME','TMPDIR','LANG','TZ','XDG_CACHE_HOME','XDG_CONFIG_HOME'})
        self.assertEqual(receipt['status'],'LAUNCHED');self.assertFalse(receipt['fallback'])
        self.assertNotIn('profile_retained_outside_upload',receipt)
        self.assertTrue(Path(receipt['environment_and_launch_evidence_retained_at']).is_dir())
        self.assertIn('NOT_PROVEN',receipt['browser_profile_retention'])

    def test_wrong_binary_and_wrong_actual_version_have_no_fallback(self):
        for kind in ('hash','version'):
            with self.subTest(kind=kind):
                pw,exe,browser=self.fake_runtime(version='wrong' if kind=='version' else s.VERSION)
                digest=hashlib.sha256(exe.read_bytes()).hexdigest() if kind=='version' else '0'*64
                with patch.object(s,'preflight'),patch.object(s,'BINARY_SHA',digest),self.assertRaises(ValueError):
                    s.launch(pw,'http://127.0.0.1:43210')
                self.assertEqual(pw.chromium.launch.call_count,1 if kind=='version' else 0)
                if kind=='version':browser.close.assert_called_once()

    def test_unknown_origin_is_rejected_before_launch(self):
        pw,exe,browser=self.fake_runtime()
        for origin in ('https://127.0.0.1:43210','http://localhost:43210','http://example.test:43210',
                       'http://127.0.0.1:43210/path','http://user@127.0.0.1:43210','http://127.0.0.1:43210#x'):
            with self.subTest(origin=origin),patch.object(s,'preflight'),self.assertRaises(ValueError):s.launch(pw,origin)
        pw.chromium.launch.assert_not_called()

    def test_unavailable_sandbox_error_is_preserved_without_second_launch(self):
        pw,exe,browser=self.fake_runtime();pw.chromium.launch.side_effect=PermissionError('synthetic sandbox denial')
        with patch.object(s,'preflight'),patch.object(s,'BINARY_SHA',hashlib.sha256(exe.read_bytes()).hexdigest()),self.assertRaises(PermissionError):
            s.launch(pw,'http://127.0.0.1:43210')
        self.assertEqual(pw.chromium.launch.call_count,1)

    def test_platform_version_and_executable_override_fail_before_browser(self):
        for kind in ('platform','package','override'):
            with self.subTest(kind=kind),patch.object(s.sys,'platform','win32' if kind=='platform' else 'linux'), \
                 patch.object(s.metadata,'version',return_value='1.62.0'), \
                 patch.dict(os.environ,{'CHROMIUM_EXECUTABLE':'/synthetic' if kind=='override' else ''}),self.assertRaises(ValueError):
                s.preflight()

    def test_exact_descriptor_admission_and_missing_or_changed_revision_rejects(self):
        import playwright
        for kind in ('correct','missing','revision','version','duplicate-full','duplicate-shell'):
            with self.subTest(kind=kind):
                root=Path(tempfile.mkdtemp(prefix='sandbox-descriptor-control-'))
                p=root/'driver/package/browsers.json';p.parent.mkdir(parents=True)
                rows=[{'name':name,'revision':'1243','browserVersion':s.VERSION} for name in ('chromium','chromium-headless-shell')]
                if kind=='missing':rows.pop()
                if kind=='revision':rows[0]['revision']='1234'
                if kind=='version':rows[1]['browserVersion']='wrong'
                if kind=='duplicate-full':rows[1]['name']='chromium'
                if kind=='duplicate-shell':rows[0]['name']='chromium-headless-shell'
                p.write_text(json.dumps({'browsers':rows}))
                with patch.object(playwright,'__file__',str(root/'__init__.py')), \
                     patch.object(s.metadata,'version',return_value='1.63.0'),patch.dict(os.environ,{'CHROMIUM_EXECUTABLE':''}):
                    if kind=='correct':s.preflight()
                    else:
                        with self.assertRaises(ValueError):s.preflight()

    def test_harness_parameter_is_explicit_and_preflight_precedes_network(self):
        import review_reader_harness as harness
        tree=ast.parse(Path(harness.__file__).read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Harness')
        init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
        self.assertIn('sandboxed',[a.arg for a in init.args.kwonlyargs])
        with patch.object(s,'preflight',side_effect=ValueError('synthetic preflight denial')),patch.object(harness,'ThreadingHTTPServer') as server,self.assertRaises(ValueError):
            harness.Harness('never-created',sandboxed=True)
        server.assert_not_called()
        for options in ({'service_workers':'allow'},{'accept_downloads':True}):
            with self.subTest(options=options),patch.object(harness,'ThreadingHTTPServer') as server,self.assertRaises(ValueError):
                harness.Harness('never-created',sandboxed=True,**options)
            server.assert_not_called()

    def test_context_cannot_override_proxy_csp_credentials_permissions_or_outputs(self):
        import review_reader_harness as harness
        denied={'proxy':{'server':'http://outside.invalid:8888'},'bypass_csp':True,'ignore_https_errors':True,
                'http_credentials':{'username':'synthetic','password':'synthetic'},'storage_state':'synthetic.json',
                'extra_http_headers':{'X-Synthetic':'fixture'},'permissions':['geolocation'],
                'record_video_dir':'/tmp/synthetic','record_har_path':'/tmp/synthetic.har',
                'client_certificates':[],'base_url':'http://outside.invalid','unknown_option':None}
        for key,value in denied.items():
            with self.subTest(key=key),patch.object(s,'preflight') as preflight, \
                 patch.object(harness,'ThreadingHTTPServer') as server,self.assertRaises(ValueError):
                harness.Harness('never-created',sandboxed=True,**{key:value})
            preflight.assert_not_called();server.assert_not_called()
        s.validate_context_options({'viewport':{'width':390,'height':844},'locale':'zh-CN','is_mobile':True,
                                    'has_touch':True,'color_scheme':'dark','device_scale_factor':1,
                                    'reduced_motion':'no-preference','service_workers':'block','accept_downloads':False})

    def test_opt_in_fixture_proxy_rejects_other_host_absolute_origin_and_connect(self):
        import review_reader_harness as harness
        root=Path(tempfile.mkdtemp(prefix='sandbox-proxy-contract-'));(root/'index.html').write_text('synthetic-only')
        out=Path(tempfile.mkdtemp(prefix='sandbox-harness-root-'))
        pw=MagicMock();browser=MagicMock()
        with patch.object(s,'preflight'),patch.object(s,'launch',return_value=(browser,{'status':'SYNTHETIC_LAUNCH_CONTROL'})), \
             patch.object(harness,'ROOT',out),patch.object(harness,'sync_playwright',return_value=pw), \
             patch.dict(os.environ,{'AI_NEWS_TEST_BUILD':str(root)}):
            h=harness.Harness('fixture',sandboxed=True)
        try:
            host=urlsplit(h.base).netloc
            for method,path,header,expected in [('GET','/inbox/all',host,200),
                    ('GET','http://outside.invalid/no',host,403),('GET','/inbox/all','other.invalid',403),
                    ('HEAD','http://outside.invalid/no',host,403),('CONNECT','outside.invalid:443',host,403)]:
                with self.subTest(method=method,path=path):
                    client=http.client.HTTPConnection('127.0.0.1',h.server.server_port,timeout=3)
                    client.request(method,path,headers={'Host':header});response=client.getresponse();response.read()
                    self.assertEqual(response.status,expected);client.close()
            h.check('synthetic_proxy_control')
        finally:h.close()


if __name__=='__main__':unittest.main()
