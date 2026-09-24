import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import unittest
import uuid

from cloud_cycle import drain
from queue_dispatch import claimed_entries,defer_unresolved
import test_import_results


class QueueTests(unittest.TestCase):
    def test_claims_span_both_accounts_and_release_finished_batches(self):
        root=Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        roots=[]
        for owner,entry,state in [('one',11,'running'),('two',12,'prepared'),('old',13,'resolved')]:
            folder=root/owner
            (folder/'batch').mkdir(parents=True)
            db=sqlite3.connect(folder/'batches.sqlite3')
            db.execute('CREATE TABLE batches(id TEXT,state TEXT)')
            db.execute('INSERT INTO batches VALUES (?,?)',('batch',state))
            db.commit()
            db.close()
            (folder/'batch/manifest.json').write_text(json.dumps({'items':[{'source_refs':[{'entry_id':entry}]}]}))
            roots.append(folder)
        self.assertEqual({11,12},claimed_entries(roots))

    def test_failed_input_retries_are_idempotent_and_bounded(self):
        fixture=test_import_results.ImportTests()
        fixture.setUp()
        outcome={'items':[{'id':'analysis-1','state':'invalid'}]}
        for attempt in range(1,4):
            fixture.manifest['batch_id']='retry-'+str(attempt)
            result=defer_unresolved(fixture.path,fixture.manifest,outcome)
            self.assertEqual('retry_scheduled' if attempt<3 else 'requires_model_review',result[0]['action'])
            self.assertEqual(attempt,fixture.read()['attempts'])
            defer_unresolved(fixture.path,fixture.manifest,outcome)
            self.assertEqual(attempt,fixture.read()['attempts'])
        self.assertEqual('requires_model_review',fixture.read()['state'])
        self.assertIsNone(fixture.read()['result'])

    def test_retry_preserves_changed_sources_and_concurrent_success(self):
        for column,value in [('source_text','changed'),('state','done')]:
            fixture=test_import_results.ImportTests()
            fixture.setUp()
            with fixture.db() as db:
                db.execute('UPDATE analyses SET '+column+'=?',(value,))
            before=fixture.read()
            defer_unresolved(fixture.path,fixture.manifest,{'items':[]})
            self.assertEqual(before,fixture.read())

    def test_translation_retry_preserves_successful_card(self):
        fixture=test_import_results.ImportTests()
        fixture.setUp()
        fixture.cards()
        with fixture.db() as db:
            db.execute("UPDATE card_translations SET status='done' WHERE entry_id=10")
        actions=defer_unresolved(fixture.path,fixture.manifest,{'items':[]})
        self.assertEqual('done',fixture.read('card_translations',10)['status'])
        self.assertEqual('error',fixture.read('card_translations',11)['status'])
        self.assertEqual(['source_changed_or_completed','retry_scheduled'],[row['action'] for row in actions])

    def test_drain_processes_multiple_batches_and_waits_between_observations(self):
        root=Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        root.mkdir(parents=True)
        states={'a':'prepared','b':'prepared'}
        batches=iter([None,'a','b',None])
        elapsed=[0]
        sleeps=[]
        calls=[]
        control=SimpleNamespace(root=root,row=lambda batch:{'state':states[batch]})
        def call(config,action,*args,timeout):
            calls.append((action,args))
            if action=='prepare':
                if len(calls)==1:
                    next(batches)
                    return {'batch_id':None,'next_retry_at':time.time()+100}
                return {'batch_id':next(batches)}
            batch=args[-1]
            states[batch]='submitted' if states[batch]=='prepared' else 'imported'
            return {'state':states[batch]}
        def sleep(seconds):
            sleeps.append(seconds)
            elapsed[0]+=seconds
        result=drain('config',{'batch_limit':20,'drain_queue':True},control,call,sleep,lambda:elapsed[0])
        self.assertEqual('empty',result['state'])
        self.assertEqual(2,result['completed_batches'])
        self.assertEqual([660,660,660],sleeps)
        self.assertEqual(4,sum(action=='prepare' for action,args in calls))
