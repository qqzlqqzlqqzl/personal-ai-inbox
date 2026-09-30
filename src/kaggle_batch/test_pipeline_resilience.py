"""Regression tests for non-blocking, Kaggle-only, durable pipeline recovery."""
import asyncio, json, pathlib, sqlite3, tempfile, time, unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from batch_control import Controller
from recovery_policy import ProviderError, classify
from queue_dispatch import claimed_entries
from cloud_bridge import backup_before_import, prepare_sample, verify_upstream
import lane_scheduler as scheduler

class DurableBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
        self.calls=[]
        self.c=Controller(self.root,'owner',client=lambda *a:self.calls.append(a))
        self.b=self.c.prepare({'runtime_source':'owner/runtime','session_timeout':600,'items':[
          {'id':'a','input_hash':'ha','messages':[{'role':'user','content':'a'}],'source_refs':[{'entry_id':1}]},
          {'id':'b','input_hash':'hb','messages':[{'role':'user','content':'b'}],'source_refs':[{'entry_id':2}]},
          {'id':'c','input_hash':'hc','messages':[{'role':'user','content':'c'}],'source_refs':[{'entry_id':3}]}]},'MANIFEST = None\n')
        self.c._set(self.b,'terminal',remote='COMPLETE')
        self.output=self.root/self.b/'output';self.output.mkdir()
    def tearDown(self):self.tmp.cleanup()
    def record(self,key,**extra):
        return {'batch_id':self.b,'manifest_hash':self.c.manifest(self.b)['manifest_hash'],
          'id':key,'input_hash':'h'+key,'status':'ok','content':'{}',**extra}
    def write(self,records):
        p=self.output/'results.jsonl'
        p.write_text('\n'.join(json.dumps(r) if isinstance(r,dict) else r for r in records)+'\n')
        return p
    def test_bad_record_does_not_discard_good_records_or_edit_raw(self):
        p=self.write([self.record('a'),'broken-json',self.record('c')]);before=p.read_bytes()
        result=self.c.verify_output(self.b,salvage=True)
        self.assertEqual(['a','c'],[r['id'] for r in result['results']])
        self.assertEqual(['b'],result['missing_ids']);self.assertEqual(before,p.read_bytes())
        with self.assertRaises(ValueError):self.c.verify_output(self.b)
    def test_conflicting_duplicate_poison_only_that_item(self):
        self.write([self.record('a'),self.record('a',content='different'),self.record('b')])
        r=self.c.verify_output(self.b,salvage=True)
        self.assertEqual(['b'],[x['id'] for x in r['results']]);self.assertEqual(['a','c'],r['missing_ids'])
    def test_wrong_hash_never_imported_in_salvage(self):
        self.write([self.record('a',input_hash='wrong'),self.record('b')])
        r=self.c.verify_output(self.b,salvage=True)
        self.assertEqual(['b'],[x['id'] for x in r['results']])
    def test_missing_file_reports_missing_not_fake_success(self):
        r=self.c.verify_output(self.b,salvage=True)
        self.assertTrue(r['missing_results_file']);self.assertEqual([],r['results'])
        self.assertEqual(['a','b','c'],r['missing_ids'])
    def test_claim_ledger_survives_missing_manifest(self):
        (self.root/self.b/'manifest.json').unlink()
        self.assertEqual({1,2,3},claimed_entries([self.root]))
    def test_terminal_local_retry_does_not_block_new_batch_or_call_gpu(self):
        self.c._set(self.b,'downloaded',remote='COMPLETE');r=self.c.defer_local(self.b,'network')
        self.assertFalse(r['gpu_resubmitted']);self.assertIsNone(self.c.next_pending())
        self.assertGreater(self.c.next_retry(),time.time());self.assertEqual([],self.calls)
        b2=self.c.prepare({'runtime_source':'o/r','session_timeout':600,'items':[
            {'id':'new','input_hash':'hn','messages':[{'role':'user','content':'new'}]}]},'MANIFEST = None\n')
        self.assertEqual(b2,self.c.next_pending())
    def test_cached_terminal_status_never_needs_remote_poll(self):
        self.c._set(self.b,'downloaded',remote='COMPLETE')
        self.c.client=Mock(side_effect=AssertionError('must not contact Kaggle'))
        self.assertEqual('downloaded',self.c.status(self.b)['state']);self.c.client.assert_not_called()
    def test_permission_denial_never_releases_ambiguous_claim(self):
        self.c._set(self.b,'submit_unknown',error='network')
        with self.c.db() as db:db.execute('UPDATE batches SET remote_status=NULL,updated=?',(time.time()-86400,))
        self.c.client=Mock(side_effect=ProviderError('inaccessible'))
        with self.assertRaises(ProviderError):self.c.status(self.b)
        self.assertEqual('submit_unknown',self.c.row(self.b)['state'])
        # status denial plus an inconclusive account-list probe still cannot release the claim.
        self.assertEqual(2,self.c.client.call_count)
    def test_one_notfound_after_timeout_is_still_ambiguous(self):
        self.c._set(self.b,'submit_unknown',error='network')
        with self.c.db() as db:db.execute('UPDATE batches SET remote_status=NULL,updated=?',(time.time()-86400,))
        self.c.client=Mock(side_effect=ProviderError('not_found'))
        with self.assertRaises(ProviderError):self.c.status(self.b)
        self.assertEqual('submit_unknown',self.c.row(self.b)['state'])
    def test_incomplete_backup_recovers_without_losing_old_evidence(self):
        folder=self.root/'backup';folder.mkdir();(folder/'before-import.sqlite3').write_bytes(b'partial')
        source=self.root/'source.db'
        with sqlite3.connect(source) as db:db.execute('CREATE TABLE example(value TEXT)');db.execute("INSERT INTO example VALUES ('original')")
        backup_before_import(source,folder)
        self.assertTrue((folder/'backup-complete.json').exists())
        self.assertEqual(b'partial',next(folder.glob('incomplete-backup-*')).read_bytes())
        with sqlite3.connect(folder/'before-import.sqlite3') as db:self.assertEqual('original',db.execute('SELECT value FROM example').fetchone()[0])

class EntryIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_fulltext_bridge import FulltextBridgeTests
        self.f=FulltextBridgeTests();self.f.setUp()
        self.f.db.execute('ALTER TABLE card_translations ADD COLUMN error TEXT')
        self.f.db.execute('ALTER TABLE card_translations ADD COLUMN updated_at REAL')
        self.f.db.execute("INSERT INTO analyses SELECT 2,user_id,title,url,state,next_try,attempts,published_at,truncated,content_hash,source_text,content_source,source_chars,input_chars,image_count,extracted_at,updated_at,error FROM analyses WHERE entry_id=1")
        self.f.db.commit()
    def tearDown(self):self.f.tearDown()
    def patches(self):
        stack=ExitStack();f=self.f
        stack.enter_context(patch('cloud_bridge.load_inbox',return_value=(f.core,f.worker,f.cards)))
        stack.enter_context(patch.dict('sys.modules',{'content_input':SimpleNamespace(content_text=lambda t:(t,0),is_our_social_feed=lambda u:False),
            'product_source':SimpleNamespace(is_product_entry=lambda e:False),'prepared_content':SimpleNamespace(apply=lambda e:e)}))
        stack.enter_context(patch('fulltext_source.fetch',AsyncMock(return_value=f.body)))
        return stack
    async def test_one_source_timeout_does_not_abandon_next_article(self):
        async def mf(client,path):
            if path.endswith('/2'):raise TimeoutError()
            return self.f.entry
        self.f.worker.mf_get=AsyncMock(side_effect=mf)
        with self.patches():r=await prepare_sample('.',2,independent_cards=True,lease_owner='test-a')
        self.assertEqual([1],[x['entry_id'] for x in r['samples']])
        row=self.f.db.execute('SELECT state,attempts,next_try FROM analyses WHERE entry_id=2').fetchone()
        self.assertEqual('fetch_error',row['state']);self.assertEqual(1,row['attempts']);self.assertGreater(row['next_try'],time.time())
    async def test_active_lease_prevents_duplicate_extraction(self):
        async def mf(client,path):return {**self.f.entry,'id':int(path.rsplit('/',1)[1])}
        self.f.worker.mf_get=AsyncMock(side_effect=mf)
        with self.patches():
            first=await prepare_sample('.',2,lease_owner='a');second=await prepare_sample('.',2,lease_owner='b')
        self.assertEqual(2,len(first['samples']));self.assertEqual(0,second['considered'])
    async def test_global_lock_release_happens_after_durable_lease(self):
        events=[]
        def released():
            count=self.f.db.execute("SELECT count(*) FROM kaggle_prepare_leases WHERE owner='test-lock'").fetchone()[0]
            self.assertEqual(1,count);self.assertFalse(self.f.db.in_transaction)
            events.append('claimed_then_unlocked')
        async def mf(client,path):
            self.assertEqual(['claimed_then_unlocked'],events)
            return self.f.entry
        self.f.worker.mf_get=AsyncMock(side_effect=mf)
        with self.patches():
            result=await prepare_sample('.',1,allowed_entry_ids=[1],lease_owner='test-lock',on_claimed=released)
        self.assertEqual(1,len(result['samples']))

    async def test_expired_preparation_lease_is_recoverable(self):
        self.f.db.execute('CREATE TABLE kaggle_prepare_leases(entry_id INTEGER PRIMARY KEY,owner TEXT,expires REAL)')
        self.f.db.execute("INSERT INTO kaggle_prepare_leases VALUES(1,'dead-observer',0)");self.f.db.commit()
        with self.patches():r=await prepare_sample('.',1,allowed_entry_ids=[1],lease_owner='new-observer')
        self.assertEqual(1,len(r['samples']))
        self.assertEqual('new-observer',self.f.db.execute('SELECT owner FROM kaggle_prepare_leases WHERE entry_id=1').fetchone()[0])
    async def test_card_only_delayed_retry_is_reported(self):
        self.f.db.execute("UPDATE analyses SET state='done'")
        self.f.db.execute("INSERT INTO card_translations VALUES(1,2,'error',?,1,NULL,0)",(time.time()+1000,));self.f.db.commit()
        with self.patches():r=await prepare_sample('.',2,independent_cards=True)
        self.assertEqual([],r['samples']);self.assertGreater(r['next_retry_at'],time.time()+990)
    async def test_transient_upstream_check_is_distinct_from_source_change(self):
        self.f.worker.mf_get=AsyncMock(side_effect=TimeoutError())
        m={'items':[{'kind':'analysis','source_refs':[{'entry_id':1,'upstream_hash':'unchanged'}]}]}
        with self.patches():r=await verify_upstream('.',m)
        self.assertEqual(set(),r);self.assertEqual({1},r.unavailable)

class SchedulerGuardTests(unittest.TestCase):
    def test_permission_message_has_typed_error(self):
        self.assertEqual('inaccessible',classify("Cannot access kernel 'x/y' (Permission 'kernels.get' was denied)."))
    def test_one_bad_lane_state_does_not_block_other_lanes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);(root/'bad.json').write_text('{broken')
            def config(key):
                if key=='third':return json.loads((root/'bad.json').read_text())
                return {'state_root':str(root/key)}
            with patch.object(scheduler,'lane_config',side_effect=config), patch.object(scheduler,'query_config',return_value={'allowed':True,'state':'available'}):
                lanes=scheduler.snapshot_lanes(time.time(),{k:'inactive' for k in scheduler.KEYS})
            self.assertFalse(lanes['third']['ready'])
            self.assertTrue(lanes['primary']['ready']);self.assertTrue(lanes['fourth']['ready'])
            starts,_=scheduler.plan(lanes,60,0)
            self.assertEqual({'primary','secondary','fourth'},set(starts))
    def test_manual_observer_lock_prevents_duplicate_service_launch(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);primary=root/'primary';primary.mkdir()
            with (primary/'cycle.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                with patch.object(scheduler,'lane_config',side_effect=lambda key:{'state_root':str(root/key)}):
                    lanes=scheduler.snapshot_lanes(time.time(),{k:'inactive' for k in scheduler.KEYS})
                self.assertTrue(lanes['primary']['active'])
                starts,_=scheduler.plan(lanes,60,0)
                self.assertNotIn('primary',starts)

    def test_pause_prevents_any_service_or_network_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp);(root/'paused.json').write_text('{}')
            run=Mock(side_effect=AssertionError('No commands while paused'))
            with patch.object(scheduler,'ROOT',root),patch.object(scheduler,'STAGE',root):
                self.assertEqual('paused',scheduler.tick(run=run)['state'])
            run.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
