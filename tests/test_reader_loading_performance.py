"""Retained unit/real-loopback contracts; this suite never claims a Chromium run."""
import ast
import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import reader_loading_fixture as f
import reader_loading_performance as measurement
from reader_loading_performance import browser_env, save_new, join_image_network, warm_image_proof, await_warm_image_proof
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

    def test_long_owned_output_parent_does_not_lengthen_browser_socket_tmp(self):
        output = self.root / ('a' * 200) / ('页' * 50)
        output.mkdir(parents=True, mode=0o700)
        env = browser_env(output)
        tmp = Path(env['TMPDIR'])
        self.assertEqual(Path(env['HOME']), output / 'home')
        self.assertGreater(len(os.fsencode(output)), 400)
        self.assertFalse(tmp.is_relative_to(output))
        self.assertEqual(tmp.stat().st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(tmp.stat().st_mode), 0o700)
        self.assertLessEqual(len(os.fsencode(tmp)) + measurement.CHROMIUM_SOCKET_SUFFIX_BYTES,
                             measurement.UNIX_SOCKET_PATH_BYTES)
        self.assertTrue(tmp.is_dir())  # Deliberately retained, including empty dirs.

    def maximum_tmp_socket_path(self):
        base = Path(tempfile.mkdtemp(prefix='t-', dir='/tmp'))
        max_parent_bytes = measurement.UNIX_SOCKET_PATH_BYTES - measurement.CHROMIUM_SOCKET_SUFFIX_BYTES - len('/rl-XXXXXXXX')
        name = 'x' * (max_parent_bytes - len(os.fsencode(base)) - 1)
        self.assertTrue(name)
        parent = base / name; parent.mkdir(mode=0o700)
        tmp = measurement.private_browser_tmp(parent)
        self.assertEqual(len(os.fsencode(tmp)) + measurement.CHROMIUM_SOCKET_SUFFIX_BYTES, 107)
        pathname = tmp / ('s' * (measurement.CHROMIUM_SOCKET_SUFFIX_BYTES - 1))
        self.assertEqual(len(os.fsencode(pathname)), 107)
        return pathname

    def test_exact_maximum_admitted_tmp_parent_has_bounded_path(self):
        self.maximum_tmp_socket_path()

    def test_actual_af_unix_107_byte_bind_and_108_byte_rejection(self):
        pathname = self.maximum_tmp_socket_path()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bound:
            bound.bind(str(pathname))
        self.assertTrue(stat.S_ISSOCK(pathname.lstat().st_mode))
        # The closed synthetic socket node is retained; nothing is unlinked.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as excessive:
            with self.assertRaises(OSError):
                excessive.bind(str(pathname) + 'x')

    def test_one_byte_excess_tmp_parent_is_rejected_before_creation(self):
        base = Path(tempfile.mkdtemp(prefix='t-', dir='/tmp'))
        max_parent_bytes = measurement.UNIX_SOCKET_PATH_BYTES - measurement.CHROMIUM_SOCKET_SUFFIX_BYTES - len('/rl-XXXXXXXX')
        parent = base / ('x' * (max_parent_bytes - len(os.fsencode(base))))
        parent.mkdir(mode=0o700)
        with patch.object(measurement.tempfile, 'mkdtemp') as create:
            with self.assertRaisesRegex(ValueError, 'AF_UNIX byte budget'):
                measurement.private_browser_tmp(parent)
            create.assert_not_called()
        self.assertEqual(list(parent.iterdir()), [])

    def test_nonascii_bytes_cannot_pass_a_character_count_budget(self):
        base = Path(tempfile.mkdtemp(prefix='t-', dir='/tmp'))
        max_parent_bytes = measurement.UNIX_SOCKET_PATH_BYTES - measurement.CHROMIUM_SOCKET_SUFFIX_BYTES - len('/rl-XXXXXXXX')
        parent = base / ('é' * ((max_parent_bytes - len(os.fsencode(base))) // 2 + 1))
        parent.mkdir(mode=0o700)
        predicted = parent / 'rl-XXXXXXXX'
        self.assertLessEqual(len(str(predicted)) + measurement.CHROMIUM_SOCKET_SUFFIX_BYTES, 107)
        self.assertGreater(len(os.fsencode(predicted)) + measurement.CHROMIUM_SOCKET_SUFFIX_BYTES, 107)
        with patch.object(measurement.tempfile, 'mkdtemp') as create:
            with self.assertRaisesRegex(ValueError, 'AF_UNIX byte budget'):
                measurement.private_browser_tmp(parent)
            create.assert_not_called()

    def test_short_temporary_parent_symlink_is_refused(self):
        base = Path(tempfile.mkdtemp(prefix='t-', dir='/tmp'))
        linked = base / 'l'; linked.symlink_to(base, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'real directory'):
            measurement.private_browser_tmp(linked)

    def test_unprotected_writable_temporary_parent_refused_without_chmod(self):
        parent = Path(tempfile.mkdtemp(prefix='t-', dir='/tmp')); parent.chmod(0o777)
        with self.assertRaisesRegex(ValueError, 'no unprotected other writers'):
            measurement.private_browser_tmp(parent)
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o777)

    def test_owned_parent_readable_but_not_writable_by_others_keeps_child_private(self):
        parent = Path(tempfile.mkdtemp(prefix='t-', dir='/tmp')); parent.chmod(0o755)
        tmp = measurement.private_browser_tmp(parent)
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(tmp.stat().st_mode), 0o700)
        self.assertEqual(tmp.stat().st_uid, os.getuid())

    def test_generated_temporary_directory_wrong_mode_is_refused_and_retained(self):
        parent = Path(tempfile.mkdtemp(prefix='t-', dir='/tmp'))
        wrong = parent / 'bad'; wrong.mkdir(mode=0o755); wrong.chmod(0o755)
        with patch.object(measurement.tempfile, 'mkdtemp', return_value=str(wrong)):
            with self.assertRaisesRegex(ValueError, 'must be owned-private'):
                measurement.private_browser_tmp(parent)
        self.assertTrue(wrong.exists())
        self.assertEqual(stat.S_IMODE(wrong.stat().st_mode), 0o755)
    def test_unsupported_platform_stops_before_any_files_or_browser(self):
        argv=['measure','--build','unused','--manifest','unused','--manifest-sha','unused',
              '--artifact-zip','unused','--artifact-sha','unused','--source-tree','unused',
              '--phase','baseline','--output-parent','unused']
        for platform in ('win32','darwin'):
            with self.subTest(platform=platform),patch.object(sys,'argv',argv),patch.object(sys,'platform',platform),\
                 patch.object(measurement,'checked_directory',side_effect=AssertionError('must not touch filesystem')):
                output=io.StringIO()
                with redirect_stdout(output):self.assertEqual(measurement.main(),1)
                self.assertEqual(json.loads(output.getvalue())['status'],'NOT_RUN')
    def test_chromium_sandbox_is_explicit_and_never_disabled(self):
        tree=ast.parse(Path(__file__).with_name('reader_loading_performance.py').read_text())
        launches=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='launch']
        self.assertEqual(len(launches),1)
        setting=next(k.value for k in launches[0].keywords if k.arg=='chromium_sandbox')
        self.assertIsInstance(setting,ast.Constant);self.assertIs(setting.value,True)
        self.assertNotIn('--no-sandbox',[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)])


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
        def cache_fixture():
            events,images=WarmCacheContract().fixture()
            for row in images:row['scroll_to_visible_ms']=1
            proof=warm_image_proof(events,images,WarmCacheContract.base,900,1500)
            return {'status':'PASSED','scope':'NEW_PAGE_SAME_BROWSER_CONTEXT_HTTP_CACHE',
                'previous_page_identity':{'target_id':'old','browser_context_id':'same'},
                'identity':{'target_id':'new','browser_context_id':'same'},'origin':WarmCacheContract.base,
                'bootstrap_ms':4,'click_to_body_ms':3,'image_event_start':0,'cdp':events,
                'images':images,'image_cache_proof':proof,'start_wall_ms':900,'end_wall_ms':1500,'image_http_requests':0}
        return {'status':'PASSED','measurement_contract':measurement.MEASUREMENT_CONTRACT,'phase':phase,'scenario':f.SCENARIO,'input':'keyboard','weak_network':False,
                'browser_version':'synthetic-test','identity':{'head':'synthetic'},
                'pairs':[{'pair':i,'status':'PASSED','measurement_contract':measurement.MEASUREMENT_CONTRACT,
                          'warm_observation_class':'SAME_PAGE_DECODED_REOPEN; HTTP_CACHE_HIT_NOT_CLAIMED',
                          'warm_start_wall_ms':900,'warm_end_wall_ms':1500,'warm_image_http_requests':0,
                          'http_cache_page':cache_fixture(),
                          'list_initial_ms':2,'cold_click_to_body_ms':3,'warm_click_to_body_ms':1,
                          'cold_images':[{'image':j,'scroll_to_visible_ms':1} for j in range(1,7)],
                          'warm_images':[{'image':j,'scroll_to_visible_ms':1,'at':1300,'visible':True,'naturalWidth':960,'naturalHeight':640} for j in range(1,7)],
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
    def test_duplicate_and_reordered_pair_ids_refused(self):
        for ids in ([1,1,1,1,1],[2,1,3,4,5],[True,2,3,4,5]):
            with self.subTest(ids=ids):
                before,after=self.row('baseline'),self.row('candidate')
                for side in (before,after):
                    for row,identity in zip(side['pairs'],ids): row['pair']=identity
                with self.assertRaises(ValueError): compare(before,after)
    def test_nonfinite_negative_bool_and_string_timing_refused(self):
        for value in (True,False,float('nan'),float('inf'),-float('inf'),-1,'1'):
            for family in ('click','image','optional','list'):
                with self.subTest(value=value,family=family):
                    after=self.row('candidate');row=after['pairs'][0]
                    if family=='click': row['cold_click_to_body_ms']=value
                    elif family=='image': row['cold_images'][0]['scroll_to_visible_ms']=value
                    elif family=='optional': row['warm_images'][0]['request_to_finished_ms']=value
                    else: row['next_pages'][0]['fast_scroll_bottom_wait_ms']=value
                    with self.assertRaises(ValueError): compare(self.row('baseline'),after)
    def test_optional_missing_or_null_never_implies_gain(self):
        before,after=self.row('baseline'),self.row('candidate')
        after['pairs'][0]['warm_images'][0]['request_to_finished_ms']=None
        result=compare(before,after)
        self.assertEqual(result['pairs'][0]['warm_images'][0]['request_to_finished_ms'],{'before':None,'after':None,'delta':None})
    def test_no_routing_or_cache_disable_call(self):
        source=Path(__file__).with_name('reader_loading_performance.py').read_text();tree=ast.parse(source)
        forbidden=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('route','route_from_har','route_web_socket')]
        self.assertEqual(forbidden,[])
        self.assertIn("'cacheDisabled': False",source)
        self.assertNotIn('clearBrowserCache',source)
    def test_network_clocks_join_by_exact_request(self):
        rows=[{'image':1,'at':1250}]
        events=[{'kind':'request','path':'/fixture-images/1.png','url':'http://127.0.0.1:9999/fixture-images/1.png','type':'Image','request_id':'exact','wall_time':1,'timestamp':10},
                {'kind':'finished','request_id':'other','timestamp':999,'encoded_bytes':999},
                {'kind':'finished','request_id':'exact','timestamp':10.1,'encoded_bytes':12},
                {'kind':'cache-hit','request_id':'exact'}]
        joined=join_image_network(rows,events,'http://127.0.0.1:9999')[0]
        self.assertEqual(joined['request_to_visible_ms'],250)
        self.assertAlmostEqual(joined['request_to_finished_ms'],100)
        self.assertEqual(joined['encoded_bytes'],12);self.assertTrue(joined['browser_cache_event'])
    def test_missing_network_timing_not_invented(self):
        row=join_image_network([{'image':1,'at':1000}],[],'http://127.0.0.1:9999')[0]
        self.assertEqual(row['network_timing'],'NO_UNIQUE_REQUEST_EVENT_OBSERVED')
        self.assertNotIn('request_to_visible_ms',row)


class WarmCacheContract(unittest.TestCase):
    base='http://127.0.0.1:9999'
    def fixture(self):
        rows=[{'image':i,'at':1300,'naturalWidth':960,'naturalHeight':640,'visible':True} for i in range(1,7)]
        events=[]
        for i in range(1,7):
            identity='warm-'+str(i)
            events.extend([
                {'kind':'request','request_id':identity,'url':f'{self.base}/fixture-images/{i}.png','type':'Image','wall_time':1,'timestamp':10},
                {'kind':'response','request_id':identity,'status':200,'timestamp':10.05,'from_disk_cache':False,'from_service_worker':False,'headers':{'Content-Type':'image/png'}},
                {'kind':'cache-hit','request_id':identity},
                {'kind':'finished','request_id':identity,'timestamp':10.1,'encoded_bytes':0}])
        return events,rows
    def check(self,events,rows): return warm_image_proof(events,rows,self.base,900,1500)
    def test_six_completed_memory_cached_images_pass(self):
        events,rows=self.fixture();proof=self.check(events,rows)
        self.assertEqual(len(proof),6);self.assertTrue(all(x['cache_event'] for x in proof))
    def test_six_completed_disk_cached_images_pass(self):
        events,rows=self.fixture();events=[x for x in events if x['kind']!='cache-hit']
        for event in events:
            if event['kind']=='response': event['from_disk_cache']=True
        self.assertTrue(all(x['from_disk_cache'] for x in self.check(events,rows)))
    def test_unrelated_js_disk_hit_cannot_replace_missing_images(self):
        _,rows=self.fixture()
        events=[{'kind':'request','request_id':'js','url':self.base+'/inbox/assets/unrelated.js','type':'Script','wall_time':1,'timestamp':10},
                {'kind':'response','request_id':'js','status':200,'timestamp':10.1,'from_disk_cache':True}]
        with self.assertRaises(ValueError): self.check(events,rows)
    def test_wrong_cache_request_identity_is_refused(self):
        events,rows=self.fixture()
        for event in events:
            if event['kind']=='cache-hit': event['request_id']='unrelated'
        with self.assertRaises(ValueError): self.check(events,rows)
    def test_one_cached_image_cannot_cover_all_six(self):
        events,rows=self.fixture();events=[x for x in events if x['kind']!='cache-hit' or x['request_id']=='warm-1']
        with self.assertRaises(ValueError): self.check(events,rows)
    def test_cold_request_delivered_late_is_refused(self):
        events,rows=self.fixture();events[0]['wall_time']=0.5
        with self.assertRaises(ValueError): self.check(events,rows)
    def test_wrong_origin_query_or_type_is_refused(self):
        for field,value in [('url','http://other.invalid/fixture-images/1.png'),('url',self.base+'/fixture-images/1.png?different=1'),('type','Script')]:
            with self.subTest(field=field,value=value):
                events,rows=self.fixture();events[0][field]=value
                with self.assertRaises(ValueError): self.check(events,rows)
    def test_missing_completion_or_failed_request_is_refused(self):
        for variant in ('missing','failed'):
            with self.subTest(variant=variant):
                events,rows=self.fixture()
                if variant=='missing': events=[x for x in events if not (x['kind']=='finished' and x['request_id']=='warm-1')]
                else: events.append({'kind':'failed','request_id':'warm-1'})
                with self.assertRaises(ValueError): self.check(events,rows)
    def test_late_completion_and_response_order_refused(self):
        for index,value in [(3,10.5),(1,11)]:
            with self.subTest(index=index):
                events,rows=self.fixture();events[index]['timestamp']=value
                with self.assertRaises(ValueError): self.check(events,rows)
    def test_wrong_mime_and_service_worker_refused(self):
        for variant in ('mime','sw'):
            with self.subTest(variant=variant):
                events,rows=self.fixture()
                if variant=='mime':events[1]['headers']['Content-Type']='text/html'
                else:events[1]['from_service_worker']=True
                with self.assertRaises(ValueError):self.check(events,rows)
    def test_duplicate_request_identity_is_refused(self):
        events,rows=self.fixture();events.append(copy.deepcopy(events[0]))
        with self.assertRaises(ValueError):self.check(events,rows)
    def test_decode_and_visibility_evidence_required(self):
        for field,value in [('naturalWidth',0),('visible',False),('at',800)]:
            with self.subTest(field=field):
                events,rows=self.fixture();rows[0][field]=value
                with self.assertRaises(ValueError):self.check(events,rows)
    def test_five_images_or_boolean_identity_refused(self):
        for variant in ('missing','bool'):
            with self.subTest(variant=variant):
                events,rows=self.fixture()
                if variant=='missing': rows=rows[:5]
                else: rows[0]['image']=True
                with self.assertRaises(ValueError):self.check(events,rows)
    def test_wait_observes_new_completion_events(self):
        events,rows=self.fixture();finished=[x for x in events if x['kind']=='finished'];events[:]=[x for x in events if x['kind']!='finished']
        class Page:
            def evaluate(self,script):return 1500
            def wait_for_timeout(self,ms):events.extend(finished)
        proof,end=await_warm_image_proof(Page(),events,0,rows,self.base,900)
        self.assertEqual((len(proof),end),(6,1500))
    def test_different_query_cannot_replace_proof_or_published_timing(self):
        events,rows=self.fixture()
        events.extend([
            {'kind':'request','url':self.base+'/fixture-images/1.png?other=1','path':'/fixture-images/1.png','request_id':'different-query','type':'Image','wall_time':1.2,'timestamp':11},
            {'kind':'response','request_id':'different-query','status':200,'timestamp':11.01,'from_disk_cache':True,'headers':{'content-type':'image/png'}},
            {'kind':'finished','request_id':'different-query','timestamp':11.025,'encoded_bytes':0}])
        proof=self.check(events,rows)
        self.assertEqual(rows[0]['request_id'],proof[0]['request_id'])
        self.assertEqual(rows[0]['request_to_visible_ms'],300)
        self.assertAlmostEqual(rows[0]['request_to_finished_ms'],100)
        self.assertEqual(rows[0]['request_to_visible_ms'],proof[0]['published_metrics']['request_to_visible_ms'])
        cold=join_image_network([{'image':1,'at':1300}],events,self.base)[0]
        self.assertEqual(cold['request_id'],'warm-1')
        self.assertEqual(cold['request_to_visible_ms'],300)
    def test_ambiguous_cold_exact_requests_do_not_choose_last(self):
        events,rows=self.fixture();duplicate=copy.deepcopy(events[0]);duplicate['request_id']='second';events.append(duplicate)
        cold=join_image_network([{'image':1,'at':1300}],events,self.base)[0]
        self.assertEqual(cold['network_timing'],'NO_UNIQUE_REQUEST_EVENT_OBSERVED')
        self.assertNotIn('request_to_visible_ms',cold)
    def test_only_cold_path_calls_optional_join(self):
        tree=ast.parse(Path(__file__).with_name('reader_loading_performance.py').read_text())
        function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='one_pair')
        calls=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='join_image_network']
        self.assertEqual(len(calls),1)
        self.assertIn('cold_images',ast.unparse(calls[0]))


if __name__=='__main__': unittest.main(verbosity=2)
