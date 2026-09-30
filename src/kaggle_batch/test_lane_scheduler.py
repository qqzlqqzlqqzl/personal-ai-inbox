import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from batch_control import Controller
from recovery_policy import ProviderError
import lane_scheduler as scheduler


def lane(active=False,outstanding=None,ready=True,retry_at=0,cycle='empty'):
    return {'active':active,'outstanding':outstanding,'ready':ready,'retry_at':retry_at,
            'cycle':{'state':cycle},'recovery':{},'service_state':'active' if active else 'inactive',
            'quota_gate':{'allowed':True,'state':'available','remaining_hours':20}}


class RotationPlanTests(unittest.TestCase):
    def test_fifth_lane_joins_without_overlapping_active_lanes(self):
        lanes={k:lane(active=k!='fifth') for k in scheduler.KEYS}
        starts,_=scheduler.plan(lanes,20,0)
        self.assertEqual(['fifth'],starts)
    def test_fifth_lane_recovers_when_other_accounts_cool(self):
        lanes={k:lane(ready=k=='fifth') for k in scheduler.KEYS}
        self.assertEqual(['fifth'],scheduler.plan(lanes,20,0)[0])

    def test_cooling_lane_never_blocks_healthy_lanes(self):
        lanes={k:lane() for k in scheduler.KEYS}
        lanes['third']=lane(outstanding={'state':'submit_unknown'},ready=False,retry_at=999)
        starts,cursor=scheduler.plan(lanes,45,0)
        self.assertEqual(['primary','secondary','fourth'],starts)
        self.assertEqual(4,cursor)

    def test_reconciliation_and_new_work_can_progress_together(self):
        lanes={k:lane() for k in scheduler.KEYS}
        lanes['third']=lane(outstanding={'state':'submit_unknown'},ready=True)
        starts,_=scheduler.plan(lanes,45,0)
        self.assertEqual('third',starts[0])
        self.assertEqual({'primary','secondary','fourth'},set(starts[1:]))
    def test_small_queue_rotates_accounts_instead_of_pin_one(self):
        lanes={k:lane() for k in scheduler.KEYS}
        first,cursor=scheduler.plan(lanes,1,0)
        second,cursor2=scheduler.plan(lanes,1,cursor)
        self.assertEqual(['primary'],first)
        self.assertEqual(['secondary'],second)
        self.assertEqual(2,cursor2)

    def test_active_slots_are_never_overcommitted(self):
        lanes={k:lane(active=True) for k in scheduler.KEYS}
        self.assertEqual(([],0),scheduler.plan(lanes,100,0))


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=self.root/'analysis.sqlite3';self.allow=self.root/'allow.json'
        self.peer=self.root/'peer';self.peer.mkdir()
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE analyses(entry_id INTEGER,user_id INTEGER,state TEXT,next_try REAL,attempts INTEGER)')
            db.execute('CREATE TABLE card_translations(entry_id INTEGER,user_id INTEGER,status TEXT,next_try REAL,attempts INTEGER)')
            db.executemany('INSERT INTO analyses VALUES (?,?,?,?,?)',[(1,1,'waiting_model',0,0),(2,1,'waiting_model',0,0),(3,1,'done',0,0),(4,1,'requires_fulltext_adapter',0,0)])
            db.execute("INSERT INTO card_translations VALUES (3,1,'pending',0,0)")
        self.allow.write_text(json.dumps({'entry_ids':[1,2,3,4]}))
        with sqlite3.connect(self.peer/'batches.sqlite3') as db:
            db.execute('CREATE TABLE batches(id TEXT,state TEXT)');db.execute("INSERT INTO batches VALUES ('b','submit_unknown')")
        (self.peer/'b').mkdir();(self.peer/'b/manifest.json').write_text(json.dumps({'items':[{'source_refs':[{'entry_id':2}]}]}))
    def tearDown(self):self.temp.cleanup()

    def test_due_queue_excludes_only_actually_claimed_items(self):
        cfg={'database':str(self.db),'entry_allowlist':str(self.allow),'peer_state_roots':[str(self.peer)]}
        due,claimed=scheduler.due_entries(time.time(),cfg)
        self.assertEqual({1,3},due);self.assertEqual({2},claimed)

    def test_exception_review_is_counted_as_real_scheduler_work(self):
        cfg={'database':str(self.db),'entry_allowlist':str(self.allow),'peer_state_roots':[str(self.peer)],
             'qwen_exception_review':True}
        due,claimed=scheduler.due_entries(time.time(),cfg)
        self.assertEqual({1,3,4},due);self.assertEqual({2},claimed)


class UncertainSubmitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.c=Controller(self.root,'owner',client=lambda *a:(_ for _ in ()).throw(ProviderError('not_found')))
        self.batch=self.c.prepare({'session_timeout':600,'runtime_source':'o/r','items':[
            {'id':'a','messages':[{'role':'user','content':'x'}],'input_hash':'h'}]},'MANIFEST = None\n')

    def tearDown(self):self.temp.cleanup()

    def age(self,seconds,state='submit_unknown',error='network'):
        self.c._set(self.batch,state,error=error)
        with self.c.db() as db:db.execute('UPDATE batches SET updated=? WHERE id=?',(time.time()-seconds,self.batch))

    def test_network_timeout_claim_is_kept_during_grace(self):
        self.age(1200)
        with self.assertRaises(ProviderError):self.c.status(self.batch)
        self.assertEqual('submit_unknown',self.c.row(self.batch)['state'])
    def test_confirmed_missing_after_grace_releases_network_claim(self):
        self.age(1900)
        (self.root/self.batch/'absence-observations.json').write_text(json.dumps({'count':1,'last_at':time.time()-700,'first_at':time.time()-700}))
        self.assertEqual('retired',self.c.status(self.batch)['state'])
        self.assertEqual('confirmed_not_found_after_network',self.c.row(self.batch)['error'])

    def test_crash_in_submitting_can_be_released_only_after_confirmed_missing(self):
        self.age(1900,state='submitting',error=None)
        (self.root/self.batch/'absence-observations.json').write_text(json.dumps({'count':1,'last_at':time.time()-700,'first_at':time.time()-700}))
        self.assertEqual('retired',self.c.status(self.batch)['state'])
        self.assertEqual('confirmed_not_found_after_unknown',self.c.row(self.batch)['error'])

    def test_readable_other_kernel_does_not_prove_inaccessible_batch_absent(self):
        old=self.batch
        self.c._set(old,'imported',remote='COMPLETE')
        current=self.c.prepare({'session_timeout':600,'runtime_source':'o/r','attempt':3,'items':[
            {'id':'current','messages':[{'role':'user','content':'x'}],'input_hash':'current'}]},'MANIFEST = None\n')
        def client(args,timeout):
            if args[-1].endswith('/'+old):return 'has status "KernelWorkerStatus.COMPLETE"'
            raise ProviderError('inaccessible')
        self.c.client=client;self.c._set(current,'submit_unknown',error='network')
        with self.c.db() as db:db.execute('UPDATE batches SET updated=? WHERE id=?',(time.time()-1900,current))
        with self.assertRaises(ProviderError):self.c.status(current)
        self.assertEqual('submit_unknown',self.c.row(current)['state'])
        self.assertEqual('network',self.c.row(current)['error'])

    def test_inaccessible_missing_slug_uses_short_absence_recovery_then_retires(self):
        self.age(1900,error='unknown')
        calls=[]
        def client(args,timeout):
            calls.append(args)
            if args[:2]==['kernels','status']:raise ProviderError('inaccessible')
            if args[:2]==['kernels','list']:return 'Not found\n'
            if args[0]=='quota':return '[{"resource":"GPU","remaining":"20h"}]'
            raise AssertionError(args)
        self.c.client=client
        with self.assertRaises(ProviderError) as err:self.c.status(self.batch)
        self.assertEqual('not_found',err.exception.code)
        proof=json.loads((self.root/self.batch/'absence-observations.json').read_text())
        self.assertEqual(1,proof['count']);self.assertEqual('submit_unknown',self.c.row(self.batch)['state'])
        proof['last_at']=time.time()-700;(self.root/self.batch/'absence-observations.json').write_text(json.dumps(proof))
        self.assertEqual('retired',self.c.status(self.batch)['state'])
        self.assertEqual('confirmed_not_found_after_unknown',self.c.row(self.batch)['error'])
        self.assertTrue(any(args and args[0]=='quota' for args in calls))

    def test_inaccessible_exact_slug_in_account_list_never_releases_claim(self):
        self.age(86400,error='network')
        target='owner/'+self.batch
        def client(args,timeout):
            if args[:2]==['kernels','status']:raise ProviderError('inaccessible')
            if args[:2]==['kernels','list']:return json.dumps([{'ref':target}])
            raise AssertionError('quota must not be needed when exact slug is visible')
        self.c.client=client
        with self.assertRaises(ProviderError) as err:self.c.status(self.batch)
        self.assertEqual('inaccessible',err.exception.code)
        self.assertEqual('submit_unknown',self.c.row(self.batch)['state'])
        self.assertFalse((self.root/self.batch/'absence-observations.json').exists())


if __name__=='__main__':unittest.main(verbosity=2)
