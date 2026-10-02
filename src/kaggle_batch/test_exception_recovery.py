import json
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch
import uuid
from types import SimpleNamespace

from exception_audit import Audit,clean
from recovery_policy import ProviderError,classify,backoff,effective_retry_at,safe_summary
from batch_control import Controller
from cloud_cycle import drain
import qwen_exceptions as qe

def folder():
    path=Path(__file__).parent/'test-runs'/uuid.uuid4().hex
    path.mkdir(parents=True);return path

class RecoveryTests(unittest.TestCase):
    def test_zero_exit_push_error_is_still_quota_rejection(self):
        c=Controller(folder(),'owner',client=lambda args,timeout:'[{"resource":"GPU","remaining":"2h"}]' if args[0]=='quota' else 'Kernel push error: Maximum weekly GPU quota of 30.00 hours reached.', initialize=True)
        b=c.prepare({'session_timeout':600,'runtime_source':'o/r','items':[
            {'id':'a','messages':[{'role':'user','content':'x'}],'input_hash':'h','source_refs':[{'entry_id':1}]}]},'MANIFEST = None\n')
        with self.assertRaises(ProviderError) as err:c.submit(b)
        self.assertEqual('quota',err.exception.code)
        self.assertEqual('quota',c.row(b)['error'])

    def test_known_errors_classified_without_raw_error_storage(self):
        for text,expected in [('GPU quota exhausted','quota'),('No GPU available','capacity'),
            ('429 too many requests','rate_limit'),('403 Forbidden','auth'),('404 Not Found','not_found'),
            ("Permission 'kernels.get' was denied",'inaccessible'),
            ('proxy connection timed out token=secret','network')]:
            self.assertEqual(expected,classify(text))
        self.assertGreaterEqual(backoff('network',1),660)
        self.assertEqual(21600,backoff('quota',1))
        self.assertLessEqual(backoff('network',100),21600)

    def test_safe_summary_keeps_diagnostic_but_removes_credentials_and_url_query(self):
        summary=safe_summary('403 https://u:p@example.com/path?token=secret Bearer TOPSECRET api_key=abc')
        self.assertIn('403',summary);self.assertIn('https://example.com/path',summary)
        self.assertNotIn('TOPSECRET',summary);self.assertNotIn('secret',summary);self.assertNotIn('abc',summary)

    def test_persisted_cooldown_is_preserved_by_strict_policy(self):
        record={'code':'unknown','failures':9,'at':1000,'retry_at':1000+6*3600}
        self.assertEqual(22600,effective_retry_at(record))

    def test_rejected_and_confirmed_missing_releases_claim_but_timeout_does_not(self):
        for code,retired in [('quota',False),('network',False)]:
            c=Controller(folder(),'owner',client=lambda *a:(_ for _ in ()).throw(ProviderError('not_found')), initialize=True)
            batch=c.prepare({'session_timeout':600,'runtime_source':'o/r','items':[
                {'id':'a','messages':[{'role':'user','content':'x'}],'input_hash':'h','source_refs':[{'entry_id':1}]}]},'MANIFEST = None\n')
            c._set(batch,'submit_unknown',error=code)
            with c.db() as db:db.execute('UPDATE batches SET updated=?',(time.time()-700,))
            if retired:self.assertEqual('retired',c.status(batch)['state'])
            else:
                with self.assertRaises(ProviderError):c.status(batch)
                self.assertEqual('submit_unknown',c.row(batch)['state'])

    @unittest.skipUnless(os.name=='posix','Linux audit lock')
    def test_cooldown_persists_without_repeated_remote_calls(self):
        root=folder();control=SimpleNamespace(root=root)
        cfg={'schedule_enabled':True,'exception_audit_root':str(root/'audit'),'owner':'o','batch_limit':20,'cycle_timeout_seconds':1000}
        def fail(*args,**kwargs):raise ProviderError('quota')
        from unittest.mock import Mock
        call=Mock(side_effect=fail)
        self.assertEqual('cooldown',drain('c',cfg,control,call=call)['state'])
        self.assertEqual('cooldown',drain('c',cfg,control,call=call)['state'])
        self.assertEqual(1,call.call_count)

class AuditTests(unittest.TestCase):
    def test_sensitive_fields_and_signed_urls_redacted(self):
        value=clean({'api_key':'secret','message':'https://u:p@example.com/a?token=secret#f Bearer TOPSECRET'})
        self.assertNotIn('TOPSECRET',json.dumps(value));self.assertNotIn('secret',json.dumps(value))
        self.assertIn('https://example.com/a',value['message'])

    @unittest.skipUnless(os.name=='posix','Linux audit lock')
    def test_rotation_caps_bytes_and_expires_old_files(self):
        root=folder();audit=Audit(root,max_bytes=700,max_age=100,segment_bytes=200)
        old=root/'events-19990101-old.jsonl';old.write_text('old');os.utime(old,(0,0))
        for i in range(20):audit.append('exception',index=i,reason='x'*50)
        self.assertFalse(old.exists())
        self.assertLessEqual(sum(p.stat().st_size for p in root.glob('events-*.jsonl')),700)
        rows=[json.loads(line) for p in sorted(root.glob('events-*.jsonl')) for line in p.read_text().splitlines()]
        self.assertEqual(19,rows[-1]['index'])
        self.assertGreater(rows[0]['index'],0)

class QwenTests(unittest.TestCase):
    def setUp(self):
        self.root=folder();self.database=self.root/'db.sqlite3'
        with sqlite3.connect(self.database) as db:
            db.execute('''CREATE TABLE analyses(entry_id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT,url TEXT,
                content_hash TEXT,error TEXT,state TEXT,attempts INTEGER,source_text TEXT,published_at TEXT,next_try REAL,updated_at REAL)''')
            db.execute('INSERT INTO analyses VALUES (1,2,?,?,?,?,?,?,?,?,0,0)',
                ('Title','https://go.dev/blog/test','hash','original_http_503','fetch_error',3,'excerpt','2026'))
        self.items=qe.prepare(self.database,[1],set())
        self.manifest={'batch_id':'batch','manifest_hash':'mh','items':self.items,'model':{'filename':'unchanged-q4'}}
        self.audit=SimpleNamespace(append=lambda *a,**k:None)

    def output(self,action='retry_fetch',confidence=0.9):
        item=self.items[0]
        return [{'id':item['id'],'batch_id':'batch','manifest_hash':'mh','input_hash':item['input_hash'],
            'status':'ok','content':json.dumps({'action':action,'confidence':confidence,'reason':'503可能为临时故障'})}]

    def row(self):
        with qe.connect(self.database) as db:return dict(db.execute('SELECT * FROM analyses').fetchone())

    def test_retry_is_bounded_idempotent_and_audited(self):
        events=[];self.audit.append=lambda event,**data:events.append((event,data))
        result=qe.apply(self.database,self.manifest,self.output(),self.audit)
        self.assertEqual('retry_fetch_scheduled',result[0]['action'])
        self.assertEqual(2,self.row()['attempts'])
        before=self.row()
        self.assertEqual('already_applied',qe.apply(self.database,self.manifest,self.output(),self.audit)[0]['action'])
        self.assertEqual(before,self.row())
        self.assertEqual([],qe.prepare(self.database,[1],set()))
        self.assertEqual('qwen_exception_decision',events[0][0])
        self.assertEqual('qwen_exception_action',events[1][0])

    def test_arbitrary_action_low_confidence_and_changed_source_never_execute(self):
        for action,confidence in [('run_shell',0.99),('retry_fetch',0.1)]:
            self.setUp()
            before=self.row();qe.apply(self.database,self.manifest,self.output(action,confidence),self.audit)
            self.assertEqual(before,self.row())
        self.setUp()
        with sqlite3.connect(self.database) as db:db.execute("UPDATE analyses SET state='done'")
        before=self.row();qe.apply(self.database,self.manifest,self.output(),self.audit)
        self.assertEqual(before,self.row())

    def test_audit_failure_prevents_action(self):
        self.audit.append=lambda *a,**k:(_ for _ in ()).throw(OSError('disk full'))
        before=self.row()
        with self.assertRaises(OSError):qe.apply(self.database,self.manifest,self.output(),self.audit)
        self.assertEqual(before,self.row())

    def test_claimed_articles_are_not_reviewed_twice(self):
        self.assertEqual([],qe.prepare(self.database,[1],{1}))

if __name__=='__main__':unittest.main()
