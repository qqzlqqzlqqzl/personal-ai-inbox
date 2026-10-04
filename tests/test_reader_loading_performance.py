"""Retained unit/real-loopback contracts; this suite never claims a Chromium run."""
import ast
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import reader_loading_fixture as f
from reader_loading_performance import browser_env, save_new, join_image_network
from compare_reader_loading import compare

ROOT = Path(tempfile.mkdtemp(prefix='perf-contract-retained-', dir=Path(__file__).parent))
print('Retained contract fixture:', ROOT, flush=True)


class BuildContract(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='input-',dir=ROOT))
        self.build=self.root/'build';self.build.mkdir()
        (self.build/'index.html').write_bytes(b'<html>synthetic</html>')
        self.manifest=self.root/'manifest.json';self.write_manifest()
    def write_manifest(self):
        data=(self.build/'index.html').read_bytes()
        self.manifest.write_text(json.dumps([{'path':'index.html','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}]))
        self.sha=hashlib.sha256(self.manifest.read_bytes()).hexdigest()
    def admit(self): return f.admit_build(self.build,self.manifest,self.sha)
    def test_exact_build_read_only(self):
        before=(self.build/'index.html').stat()
        files,identity=self.admit()
        self.assertEqual(files['index.html'],b'<html>synthetic</html>')
        self.assertEqual(identity['files'],1)
        self.assertEqual((self.build/'index.html').stat().st_mtime_ns,before.st_mtime_ns)
    def test_manifest_digest_refused(self):
        with self.assertRaises(ValueError): f.admit_build(self.build,self.manifest,'0'*64)
    def test_content_change_refused(self):
        (self.build/'index.html').write_text('changed synthetic bytes')
        with self.assertRaises(ValueError): self.admit()
    def test_extra_file_refused(self):
        (self.build/'extra').write_text('extra')
        with self.assertRaises(ValueError): self.admit()
    def test_build_symlink_refused(self):
        p=self.root/'linked';p.symlink_to(self.build,target_is_directory=True)
        with self.assertRaises(ValueError): f.admit_build(p,self.manifest,self.sha)
    def test_member_symlink_refused(self):
        (self.build/'extra').symlink_to(self.manifest)
        with self.assertRaises(ValueError): self.admit()
    def test_bound_parent_symlink_refused(self):
        p=self.root/'linked';p.symlink_to(self.build,target_is_directory=True)
        with self.assertRaises(OSError): f.bound_read(p,'index.html')
    def test_relative_escape_refused(self):
        with self.assertRaises(ValueError): f.bound_read(self.build,'../manifest.json')
    def test_special_file_refused_without_block(self):
        os.mkfifo(self.build/'fifo')
        with self.assertRaises(ValueError): f.bound_read(self.build,'fifo')
    def test_size_limit_refused(self):
        with self.assertRaises(ValueError): f.bound_read(self.build,'index.html',3)
    def test_total_byte_limit_refused(self):
        with patch.object(f,'MAX_BUILD_BYTES',2):
            with self.assertRaises(ValueError): self.admit()
    def test_duplicate_manifest_path_refused(self):
        rows=json.loads(self.manifest.read_text());self.manifest.write_text(json.dumps(rows+rows))
        self.sha=hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        with self.assertRaises(ValueError): self.admit()


class OutputContract(unittest.TestCase):
    def setUp(self): self.root=Path(tempfile.mkdtemp(prefix='output-',dir=ROOT))
    def test_new_receipt_is_private(self):
        save_new(self.root/'receipt.json',{'status':'synthetic'})
        self.assertEqual(stat.S_IMODE((self.root/'receipt.json').stat().st_mode),0o600)
    def test_old_receipt_never_overwritten(self):
        save_new(self.root/'receipt.json',{'old':True})
        with self.assertRaises(FileExistsError): save_new(self.root/'receipt.json',{'new':True})
        self.assertEqual(json.loads((self.root/'receipt.json').read_text()),{'old':True})
    def test_receipt_leaf_link_refused(self):
        original=self.root/'original';original.write_text('preserve')
        (self.root/'receipt.json').symlink_to(original)
        with self.assertRaises(FileExistsError): save_new(self.root/'receipt.json',{})
        self.assertEqual(original.read_text(),'preserve')
    def test_receipt_parent_link_refused(self):
        linked=ROOT/('linked-'+self.root.name);linked.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError): save_new(linked/'receipt.json',{})
    def test_nonprivate_parent_refused_without_chmod(self):
        public=self.root/'public';public.mkdir(mode=0o755)
        # The fixture mode is explicitly set/read; aggregate umask cannot make this a false positive.
        public.chmod(0o755)
        with self.assertRaises(ValueError): save_new(public/'receipt.json',{})
        self.assertEqual(stat.S_IMODE(public.stat().st_mode),0o755)
    def test_environment_is_explicit_allowlist(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'synthetic-do-not-inherit','HTTPS_PROXY':'synthetic','DISPLAY':':99'}):
            env=browser_env(self.root)
        self.assertEqual(set(env),{'PATH','HOME','TMPDIR','LANG','TZ','XDG_CACHE_HOME','XDG_CONFIG_HOME'})
        self.assertNotIn('synthetic',json.dumps(env))


class HTTPContract(unittest.TestCase):
    def setUp(self): self.fixture=f.Fixture({'index.html':b'<html>synthetic</html>','assets/test.js':b'window.synthetic=true'})
    def tearDown(self): self.fixture.close()
    def request(self,path,method='GET',headers=None,body=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.fixture.server.server_port,timeout=3)
        try:
            connection.request(method,path,body=body,headers=headers or {})
            response=connection.getresponse();return response.status,dict(response.getheaders()),response.read()
        finally: connection.close()
    def api(self,path,method='GET',body=None):
        return self.request(path,method,{'X-Auth-Token':f.TOKEN},json.dumps(body) if body is not None else None)
    def test_loopback_only(self): self.assertEqual(self.fixture.server.server_address[0],'127.0.0.1')
    def test_document_csp_and_no_store(self):
        status,headers,body=self.request('/inbox/all')
        self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIn("connect-src 'self'",headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin',headers)
    def test_asset_bytes_and_cache_unchanged(self):
        status,headers,body=self.request('/inbox/assets/test.js')
        self.assertEqual((status,body),(200,b'window.synthetic=true'))
        self.assertIn('immutable',headers['Cache-Control'])
    def test_unknown_asset_not_spa_success(self): self.assertEqual(self.request('/inbox/assets/missing.js')[0],404)
    def test_unknown_api_fails(self): self.assertEqual(self.api('/mf/v1/not-supported')[0],501)
    def test_real_auth_not_accepted(self): self.assertEqual(self.request('/mf/v1/me',headers={'X-Auth-Token':'wrong'})[0],401)
    def test_external_absolute_target_denied(self): self.assertEqual(self.request('http://unreachable.invalid/a')[0],403)
    def test_other_loopback_denied(self): self.assertEqual(self.request('http://127.0.0.1:1/a')[0],403)
    def test_bad_host_denied(self): self.assertEqual(self.request('/inbox/all',headers={'Host':'unreachable.invalid'})[0],403)
    def test_connect_never_forwards(self): self.assertEqual(self.request('unreachable.invalid:443','CONNECT')[0],403)
    def test_upgrade_denied(self): self.assertEqual(self.request('/inbox/all',headers={'Upgrade':'websocket'})[0],400)
    def test_traversal_denied(self): self.assertEqual(self.request('/inbox/%2e%2e/secret')[0],400)
    def test_unknown_mutation_denied(self): self.assertEqual(self.api('/mf/v1/ai/settings','PUT',{'enabled':True})[0],501)
    def test_bounded_list_no_store(self):
        status,headers,raw=self.api('/mf/v1/entries?limit=24&offset=24')
        data=json.loads(raw);self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual([e['id'] for e in data['entries']],list(range(25,49)))
        self.assertTrue(all(e['content_deferred'] and not e['content'] for e in data['entries']))
    def test_large_page_refused(self): self.assertEqual(self.api('/mf/v1/entries?limit=1000')[0],400)
    def test_unknown_query_refused(self): self.assertEqual(self.api('/mf/v1/entries?url=https://unknown.invalid')[0],400)
    def test_duplicate_query_refused(self): self.assertEqual(self.api('/mf/v1/entries?limit=1&limit=24')[0],400)
    def test_synthetic_unread_count_is_zero(self):
        status,_,raw=self.api('/mf/v1/entries?status=unread&limit=1')
        self.assertEqual((status,json.loads(raw)),(200,{'total':0,'entries':[]}))
    def test_detail_has_six_local_images(self):
        status,_,raw=self.api('/mf/v1/entries/1');data=json.loads(raw)
        self.assertEqual(status,200);self.assertEqual(data['content'].count('<img'),6)
        self.assertNotIn('https://',data['content'])
    def test_read_mutation_has_empty_204_body(self):
        status,_,body=self.api('/mf/v1/entries','PUT',{'entry_ids':[1],'status':'read'})
        self.assertEqual((status,body),(204,b''))
    def test_image_http_conditional_contract(self):
        status,headers,body=self.request('/fixture-images/1.png')
        self.assertEqual(status,200);self.assertTrue(body.startswith(b'\x89PNG'))
        status,_,body=self.request('/fixture-images/1.png',headers={'If-None-Match':headers['ETag']})
        self.assertEqual((status,body),(304,b''))
        self.assertEqual(len(self.fixture.records),2)  # HTTP only, not browser cache proof.
    def test_image_failure_is_bounded_and_retained(self):
        self.fixture.failures=True
        self.assertEqual(self.request('/fixture-images/3.png')[0],503)
        self.assertEqual(self.request('/fixture-images/3.png')[0],200)
        self.assertEqual([r['status'] for r in self.fixture.records],[503,200])
    def test_request_budget_is_fail_closed(self):
        with patch.object(f,'MAX_REQUESTS',1):
            self.assertEqual(self.request('/inbox/all')[0],200)
            self.assertEqual(self.request('/inbox/all')[0],429)
    def test_rejection_evidence_is_bounded(self):
        with patch.object(f,'MAX_REQUESTS',1):
            for _ in range(4): self.request('unreachable.invalid:443','CONNECT')
            self.assertEqual(len(self.fixture.records),2)
    def test_expired_run_is_fail_closed(self):
        self.fixture.deadline=0;self.assertEqual(self.request('/inbox/all')[0],429)


class SamplingContract(unittest.TestCase):
    def row(self,phase):
        return {'status':'PASSED','phase':phase,'scenario':f.SCENARIO,'input':'keyboard','weak_network':False,
                'browser_version':'synthetic-test','identity':{'head':'synthetic'},
                'pairs':[{'pair':i,'status':'PASSED','list_initial_ms':2,'cold_click_to_body_ms':3,'warm_click_to_body_ms':1,
                          'cold_images':[{'image':j,'scroll_to_visible_ms':1} for j in range(1,7)],
                          'warm_images':[{'image':j,'scroll_to_visible_ms':1} for j in range(1,7)],
                          'next_pages':[{'loaded':48,'fast_scroll_bottom_wait_ms':2},{'loaded':72,'fast_scroll_bottom_wait_ms':2}]} for i in range(1,6)]}
    def test_five_pairs_compare(self): self.assertEqual(len(compare(self.row('baseline'),self.row('candidate'))['pairs']),5)
    def test_not_run_never_compares(self):
        other=self.row('candidate');other['status']='NOT_RUN'
        with self.assertRaises(ValueError): compare(self.row('baseline'),other)
    def test_partial_run_never_compares(self):
        other=self.row('candidate');other['pairs']=other['pairs'][:4]
        with self.assertRaises(ValueError): compare(self.row('baseline'),other)
    def test_different_conditions_never_compare(self):
        other=self.row('candidate');other['input']='touch'
        with self.assertRaises(ValueError): compare(self.row('baseline'),other)
    def test_no_routing_or_cache_disable_call(self):
        source=Path(__file__).with_name('reader_loading_performance.py').read_text();tree=ast.parse(source)
        forbidden=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('route','route_from_har','route_web_socket')]
        self.assertEqual(forbidden,[])
        self.assertIn("'cacheDisabled': False",source)
        self.assertNotIn('clearBrowserCache',source)
    def test_network_clocks_join_by_exact_request(self):
        rows=[{'image':1,'at':1250}]
        events=[{'kind':'request','path':'/fixture-images/1.png','request_id':'exact','wall_time':1,'timestamp':10},
                {'kind':'finished','request_id':'other','timestamp':999,'encoded_bytes':999},
                {'kind':'finished','request_id':'exact','timestamp':10.1,'encoded_bytes':12},
                {'kind':'cache-hit','request_id':'exact'}]
        joined=join_image_network(rows,events)[0]
        self.assertEqual(joined['request_to_visible_ms'],250)
        self.assertAlmostEqual(joined['request_to_finished_ms'],100)
        self.assertEqual(joined['encoded_bytes'],12);self.assertTrue(joined['browser_cache_event'])
    def test_missing_network_timing_not_invented(self):
        row=join_image_network([{'image':1,'at':1000}],[])[0]
        self.assertEqual(row['network_timing'],'NO_REQUEST_EVENT_OBSERVED')
        self.assertNotIn('request_to_visible_ms',row)


if __name__=='__main__': unittest.main(verbosity=2)
