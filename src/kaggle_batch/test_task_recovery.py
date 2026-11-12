import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from batch_control import Controller
from dispatch_policy import DispatchStopped
from queue_dispatch import claimed_entries
from recovery_policy import ProviderError,backoff,classify_failure,effective_retry_at,record_failure
import task_recovery as task


class PolicyTests(unittest.TestCase):
    def test_all_local_waits_are_at_most_one_hour(self):
        for code in ProviderError.CODES:
            for failures in (1,2,3,10000):
                self.assertGreater(backoff(code,failures),0)
                self.assertLessEqual(backoff(code,failures),3600)
        self.assertEqual(4600,effective_retry_at({'at':1000,'retry_at':1000+86400}))

    def test_operation_changes_error_scope_without_leaking_input(self):
        cases=[('404 Not Found','kernels.status','not_found','task'),
               ('403 forbidden','kernels.status','inaccessible','task'),
               ('403 forbidden','kernels.push','auth','account'),
               ('401 unauthorized','kernels.status','authentication','account'),
               ('429 too many requests','kernels.status','rate_limited','account'),
               ('GPU quota exhausted','kernels.push','quota','account'),
               ('No GPU available','kernels.push','capacity','account'),
               ('connection timed out','kernels.status','network','task'),
               ('unrecognized failure','kernels.status','provider_unavailable','task')]
        for message,operation,code,scope in cases:
            error=classify_failure(message+' token=secret',operation=operation)
            self.assertEqual((code,scope),(error.code,error.scope))
            self.assertEqual(scope,task.failure_scope(error))
            self.assertNotIn('secret',str(error))

    def test_url_and_incidental_numbers_cannot_masquerade_as_authentication_error(self):
        exc=classify_failure('503 Server Error: upstream for url: https://example.invalid/private?token=secret',operation='kernels.status')
        self.assertEqual('provider_unavailable',exc.code)
        self.assertEqual('provider_unavailable',classify_failure('article 404 account private').code)

    def test_legacy_task_failures_do_not_inflate_new_lane_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);events=[]
            (root/'recovery.json').write_text(json.dumps({'code':'provider_unavailable','failures':111,'at':1000,'retry_at':0}))
            audit=SimpleNamespace(append=lambda *a,**kw:events.append(kw))
            first=record_failure(root,'provider_unavailable',audit,'fixture',now=2000)
            self.assertEqual(1,first['failures'])
            self.assertEqual(2660,first['retry_at'])
            self.assertEqual(111,events[0]['previous_failures'])
            second=record_failure(root,'provider_unavailable',audit,'fixture',now=3000)
            self.assertEqual(2,second['failures'])


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.calls=[]
        def client(args,timeout):
            self.calls.append(args)
            if args[0]=='quota':return '[{"resource":"GPU","remaining":"30h"}]'
            if args[:2]==['kernels','push']:
                folder=Path(args[args.index('-p')+1])
                ref=json.loads((folder/'kernel-metadata.json').read_text())['id']
                return f'Kernel version 1 successfully pushed.  Please check progress at https://www.kaggle.com/code/{ref}'
            raise AssertionError('No status/push calls expected during isolation')
        self.c=Controller(self.root,'owner',client=client,initialize=True)
        self.b=self.make(1)
        self.c._set(self.b,'submit_unknown',error='network')
        self.events=[]
        self.audit=SimpleNamespace(append=lambda event,**kw:self.events.append((event,kw)))
        self.now=time.time()-7200

    def tearDown(self):self.tmp.cleanup()

    def make(self,entry):
        return self.c.prepare({'runtime_source':'o/r','session_timeout':600,'items':[
            {'id':str(entry),'input_hash':str(entry),'messages':[{'role':'user','content':'fixture'}],
             'source_refs':[{'entry_id':entry}]}]},'MANIFEST = None\n')

    def fail(self,at=None,code='not_found'):
        return task.recover(self.c,self.b,ProviderError(code,operation='kernels.status'),self.audit,
                            now=self.now if at is None else at)

    def repeated(self):
        self.fail();self.fail(self.now+1200)
        return self.fail(self.now+3601)

    def test_status_failure_is_per_task_and_never_writes_lane_backoff(self):
        before=claimed_entries([self.root])
        result=self.fail()
        self.assertEqual('task_retry_scheduled',result['state'])
        self.assertEqual('task',result['scope'])
        self.assertFalse((self.root/'recovery.json').exists())
        self.assertEqual(before,claimed_entries([self.root]))
        self.assertEqual([],self.calls)

    def test_budget_persisted_and_existing_active_job_does_not_allocate_new_one(self):
        result=self.fail(time.time())
        waiting=task.active_retry(self.c)
        self.assertEqual(self.b,waiting['batch_id'])
        self.assertEqual(result['retry_at'],task.pending(self.c,self.b)['retry_at'])
        self.assertEqual([],self.calls)

    def test_repeated_unknown_is_isolated_and_only_unclaimed_new_work_can_start(self):
        before=claimed_entries([self.root])
        result=self.repeated()
        self.assertEqual('quarantined',result['state'])
        self.assertFalse(result['remote_absence_proven'])
        self.assertEqual(before,claimed_entries([self.root]))
        self.assertIsNone(task.active_retry(self.c))
        new=self.make(2)
        self.assertNotEqual(self.b,new)
        self.assertEqual('submitted',self.c.submit(new)['state'])
        self.assertEqual(1,len([a for a in self.calls if a[:2]==['kernels','push']]))
        self.assertEqual({1,2},claimed_entries([self.root]))
        self.assertEqual('quarantined',self.c.submit(self.b)['state'])

    def test_one_failure_or_elapsed_time_alone_cannot_isolate(self):
        self.fail();result=self.fail(self.now+10000)
        self.assertNotEqual('quarantined',result['state'])
        self.assertEqual('submit_unknown',self.c.row(self.b)['state'])

    def test_positive_or_partial_receipt_always_protects_job(self):
        for filename in ('submit-receipt.json','submit-receipt.json.pending'):
            with self.subTest(filename=filename):
                path=self.root/self.b/filename
                path.write_text('{}')
                self.assertNotEqual('quarantined',self.repeated()['state'])
                path.unlink()

    def test_positive_remote_status_always_protects_job(self):
        self.c._set(self.b,'running',remote='RUNNING')
        self.assertNotEqual('quarantined',self.repeated()['state'])
        self.assertEqual('running',self.c.row(self.b)['state'])

    def test_receipt_integrity_error_is_never_auto_parked(self):
        self.c._set(self.b,'submit_unknown',error='submit_receipt_write_failed')
        self.assertNotEqual('quarantined',self.repeated()['state'])

    def test_account_failure_is_not_disguised_as_job_failure(self):
        with self.assertRaises(ProviderError):self.fail(code='authentication')
        self.assertFalse((self.root/self.b/'automatic-task-isolation.json').exists())

    def test_insufficient_quota_prevents_isolation_and_new_gpu_work(self):
        self.c.client=lambda *a:'[{"resource":"GPU","remaining":"0h"}]'
        result=self.repeated()
        self.assertEqual('isolation_deferred_account_not_ready',result['action'])
        self.assertEqual('submit_unknown',self.c.row(self.b)['state'])

    def test_corrupt_manifest_and_audit_failure_never_mutate_claims(self):
        before=claimed_entries([self.root])
        self.audit.append=lambda *a,**kw:(_ for _ in ()).throw(OSError('disk full token=secret'))
        with self.assertRaises(OSError):self.fail()
        self.assertEqual(before,claimed_entries([self.root]))
        self.audit.append=lambda *a,**kw:None
        (self.root/self.b/'manifest.json').write_text('{}')
        with self.assertRaises((ValueError,KeyError)):self.fail()
        self.assertEqual(before,claimed_entries([self.root]))

    def test_terminal_retry_does_not_block_new_work_and_keeps_old_output(self):
        self.c._set(self.b,'terminal',remote='COMPLETE')
        output=self.root/self.b/'output';output.mkdir();(output/'results.jsonl').write_text('evidence')
        result=self.fail(time.time())
        self.assertEqual('terminal_output_deferred_new_work_allowed',result['action'])
        self.assertIsNone(task.active_retry(self.c))
        new=self.make(2)
        self.assertEqual(new,self.c.next_pending())
        self.assertEqual('evidence',(output/'results.jsonl').read_text())

    def test_success_resets_only_task_owned_counters(self):
        self.fail(time.time())
        task.success(self.c,self.b)
        self.assertIsNone(task.pending(self.c,self.b))
        with self.c.db() as db:
            db.execute("UPDATE batch_progress SET next_try=9999999999,error='upstream_validation' WHERE batch_id=?",(self.b,))
        task.success(self.c,self.b)
        with self.c.db() as db:
            self.assertEqual(9999999999,db.execute('SELECT next_try FROM batch_progress WHERE batch_id=?',(self.b,)).fetchone()[0])

    def test_pause_guard_still_prevents_mutations(self):
        self.c.admission=lambda:(_ for _ in ()).throw(DispatchStopped('paused'))
        with self.assertRaises(DispatchStopped):self.fail()

    def test_quarantine_cas_rejects_concurrent_row_change(self):
        self.fail();self.fail(self.now+1200)
        original=self.c.quarantine_unknown
        def race(batch,*args,**kwargs):
            self.c._set(batch,'running',remote='RUNNING')
            return original(batch,*args,**kwargs)
        with patch.object(self.c,'quarantine_unknown',side_effect=race),self.assertRaises(ProviderError):
            self.fail(self.now+3601)
        self.assertEqual('running',self.c.row(self.b)['state'])
        self.assertEqual({1},claimed_entries([self.root]))

    def test_receipt_arriving_during_review_is_rechecked_under_submit_lock(self):
        self.fail();self.fail(self.now+1200)
        original=self.c.quarantine_unknown
        def arrive(batch,*args,**kwargs):
            (self.root/batch/'submit-receipt.json.pending').write_text('{}')
            return original(batch,*args,**kwargs)
        with patch.object(self.c,'quarantine_unknown',side_effect=arrive),self.assertRaises(ProviderError):
            self.fail(self.now+3601)
        self.assertEqual('submit_unknown',self.c.row(self.b)['state'])
        self.assertEqual({1},claimed_entries([self.root]))

    def test_repeated_isolation_is_bounded_per_account(self):
        for entry in (2,3):
            other=self.make(entry);self.c._set(other,'submit_unknown')
            self.c.quarantine_unknown(other,self.c.row(other),'Previous fixture isolation','fixture-review')
        result=self.repeated()
        self.assertEqual('isolation_budget_exhausted_attention_required',result['action'])
        self.assertEqual('submit_unknown',self.c.row(self.b)['state'])
        self.assertEqual({1,2,3},claimed_entries([self.root]))

    def snapshot(self):
        import lane_scheduler as scheduler
        cfg={'state_root':str(self.root),'schedule_enabled':True}
        with (patch.object(scheduler,'KEYS',('primary',)),patch.object(scheduler,'STAGE',self.root),
              patch.object(scheduler,'lane_config',return_value=cfg),
              patch.object(scheduler,'query_config',return_value={'allowed':True,'state':'available'})):
            return scheduler.snapshot_lanes(time.time(),{'primary':'inactive'},configs={'primary':cfg})['primary']

    def test_scheduler_preserves_active_job_slot_during_task_backoff(self):
        self.fail(time.time())
        lane=self.snapshot()
        self.assertEqual(self.b,lane['outstanding']['id'])
        self.assertFalse(lane['ready'])

    def test_quarantined_job_and_stale_cycle_flag_cannot_block_new_work(self):
        self.repeated()
        (self.root/'cycle-status.json').write_text(json.dumps({'recovery_required':True,'batch_id':self.b}))
        lane=self.snapshot()
        self.assertIsNone(lane['outstanding'])
        self.assertTrue(lane['ready'])
        self.assertTrue(lane['quota_gate']['allowed'])


if __name__=='__main__':unittest.main()
