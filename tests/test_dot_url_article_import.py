"""Reference-only synthetic integration; original fulltext importer stays unchanged."""
import copy
import datetime as dt
import hashlib
import json
import pathlib
import sqlite3
import sys
import time
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'src'))
import dot_url_article_import as urlimp
import test_dot_article_import as fixture


class URLImportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ImportTests();self.f.setUp()
        self.config=self.f.config;self.db=self.f.db;self.now=time.time()-10
        # Build 12 NULL-source rows with the actual lightweight manifest shape.
        for eid in range(4,13):
            self.f.change('INSERT INTO analyses SELECT ?,user_id,feed_id,?, ?,published_at,state,attempts,next_try,content_hash,source_text,truncated,content_source,source_chars,extracted_at,result,score,technical_score,business_score,model,prompt_hash,tokens,analyzed_at,updated_at,error FROM analyses WHERE entry_id=1',eid,'Original '+str(eid),'https://example.com/'+str(eid))
        self.f.change("UPDATE analyses SET state='pending',updated_at=?,content_hash=NULL,source_text=NULL,content_source=NULL,source_chars=NULL,extracted_at=NULL,analyzed_at=NULL",self.now-1)
        self.f.change('UPDATE card_translations SET updated_at=?',self.now-2)
        self.packet={k:v for k,v in self.f.packet.items() if k not in ('schema','export_hash','articles')}
        self.packet.update(schema='dot-url-manifest-v1',scope_user_id=1,cutoff=self.now,exported_at=self.now+1,entry_ids=list(range(1,13)),articles=[],limit=12)
        self.results={'schema':'dot-url-results-v1','producer':dict(urlimp.PRODUCER),'articles':[]}
        self.upstream={}
        with urlimp.ro(self.db) as db:
            for row in db.execute('SELECT * FROM analyses ORDER BY entry_id'):
                eid=row['entry_id'];snap={k:row[k] for k in urlimp.ANALYSIS_FIELDS}
                card=db.execute('SELECT * FROM card_translations WHERE entry_id=?',(eid,)).fetchone()
                card={k:card[k] for k in urlimp.CARD_FIELDS} if card else None
                a={k:row[k] for k in (*urlimp.IDENTITY_FIELDS,'published_at')}
                a.update(analysis_snapshot=snap,analysis_snapshot_hash=urlimp.digest(snap),card=card,card_snapshot_hash=urlimp.digest(card))
                a['snapshot_hash']=urlimp.digest(a);self.packet['articles'].append(a)
                result=copy.deepcopy(self.f.result['articles'][0]);result.pop('snapshot_hash');result.update(entry_id=eid,requested_url=a['url'],manifest_snapshot_hash=a['snapshot_hash'],status='ok',producer=dict(urlimp.PRODUCER),card=None)
                if card:result['card']={'id':eid,'title':'技术实践的具体步骤','summary':'本文介绍了实现的具体步骤与限制条件。'}
                result['source']={'method':'dot_url_reference','archive_mode':'reference_only','full_article_read':True,'text_file':None,'text_sha256':None,'original_url':a['url'],'final_url':a['url'],'page_title':a['title'],'fetched_at_utc':dt.datetime.fromtimestamp(self.now+2,dt.timezone.utc).isoformat(),'completeness':{'full_article':True,'truncated':False,'paywall_or_challenge':False,'evidence':'Actual worker inspected the full article from introduction through conclusion.'}}
                result['review']={'reviewer':'dot/root','full_article_read_verified':True,'evidence_quote_verified':True,'source_observation':'Root checked the worker observation and exact short quote against the observed source.'}
                self.results['articles'].append(result)
                self.upstream[eid]={k:a[k] for k in urlimp.IDENTITY_FIELDS};self.upstream[eid].update(content_hash=hashlib.sha256(str(eid).encode()).hexdigest(),feed_enabled=True)
                if card:self.upstream[eid]['card_source']={k:card[k] for k in ('source_hash','original_title','excerpt','source_kind')}
        self.rehash()
    def tearDown(self):self.f.tearDown()
    def rehash(self):
        self.packet['manifest_hash']=urlimp.digest({k:v for k,v in self.packet.items() if k!='manifest_hash'});self.results['manifest_hash']=self.packet['manifest_hash']
        for r in self.results['articles']:r['review']['article_result_hash']=urlimp.digest({k:v for k,v in r.items() if k!='review'})
    def run_import(self,**kw):return urlimp.import_batch(self.config,self.packet,self.results,lambda:copy.deepcopy(self.upstream),batch_limit=12,backup=self.f.backup,**kw)
    def clean(self):
        self.f.assert_clean();self.assertEqual(self.f.query("SELECT count(*) FROM sqlite_master WHERE name IN ('dot_url_sources','dot_url_batch_receipts')")[0][0],0)
    def test_twelve_success_no_fulltext_and_replay(self):
        out=self.run_import(apply=True);self.assertEqual((out['analysis_count'],out['card_count']),(12,1))
        self.assertEqual(self.f.query('SELECT DISTINCT source_text,source_chars,extracted_at,content_source,model FROM analyses'),[(None,None,None,'dot_url_reference','dot/gpt-6-astra')])
        self.assertEqual(self.f.query('SELECT count(*) FROM dot_url_sources'),[(12,)])
        self.assertEqual(self.run_import(apply=True)['state'],'already_imported')
        self.assertEqual(len(self.f.backups),1)
    def test_default_three_and_explicit_twelve(self):
        with self.assertRaisesRegex(ValueError,'invalid_bounded_batch'):urlimp.identity(self.packet,self.results)
        for limit in (0,13,True):
            with self.assertRaises(ValueError):urlimp.identity(self.packet,self.results,limit)
        self.results['articles']=self.results['articles'][:3]
        self.assertEqual(len(urlimp.identity(self.packet,self.results)[2]),3)
    def test_dry_run_no_writes(self):
        before=self.db.read_bytes();self.assertEqual(self.run_import()['state'],'validated_no_write');self.assertEqual(before,self.db.read_bytes());self.clean();self.assertEqual(self.f.backups,[])
    def test_subset_batches_and_idempotence(self):
        all_results=copy.deepcopy(self.results['articles']);self.results['articles']=all_results[:3]
        first=self.run_import(apply=True);self.results['articles']=all_results[3:6];second=self.run_import(apply=True)
        self.assertNotEqual(first['batch_id'],second['batch_id']);self.assertEqual(self.run_import(apply=True)['state'],'already_imported')
        self.assertEqual(self.f.query("SELECT count(*) FROM analyses WHERE state='done'"),[(6,)])
    def test_changed_result_reusing_review_rejected(self):
        self.results['articles'][0]['analysis']['summary']='更改的摘要。'
        with self.assertRaisesRegex(ValueError,'review_not_bound'):self.run_import(apply=True)
        self.clean()
    def test_changed_reviewed_result_cannot_overwrite(self):
        self.run_import(apply=True);self.results['articles'][0]['analysis']['summary']='更改的摘要。';self.rehash()
        with self.assertRaisesRegex(ValueError,'import_identity_collision'):self.run_import(apply=True)
    def test_reference_cannot_claim_fulltext(self):
        for key,value in [('text_file','/tmp/source.txt'),('text_sha256','a'*64),('text','source'),('full_article_read',False),('archive_mode','fulltext')]:
            with self.subTest(key=key):
                r=self.results['articles'][0];old=copy.deepcopy(r['source']);r['source'][key]=value;self.rehash()
                with self.assertRaises(ValueError):self.run_import(apply=True)
                r['source']=old;self.rehash();self.clean()
    def test_missing_review_or_incomplete_source_blocks(self):
        r=self.results['articles'][0]
        for section,key,value in [('review','full_article_read_verified',False),('review','evidence_quote_verified',False),('review','source_observation',''),('source','original_url','https://example.com/other'),('source','fetched_at_utc','2999-01-01T00:00:00Z')]:
            old=r[section][key];r[section][key]=value;self.rehash()
            with self.assertRaises(ValueError):self.run_import(apply=True)
            r[section][key]=old;self.rehash();self.clean()
        r['source']['completeness']['full_article']=False;self.rehash()
        with self.assertRaisesRegex(ValueError,'full_read_observation'):self.run_import(apply=True)
    def test_quote_word_and_character_limits(self):
        for quote in ('a '*26,'中'*121):
            self.results['articles'][0]['analysis']['evidence']=quote.strip();self.rehash()
            with self.assertRaises(ValueError):self.run_import(apply=True)
            self.clean()
    def test_card_id_checked_without_mutating_input(self):
        r=next(r for r in self.results['articles'] if r['card']);r['card']['id']=999;self.rehash()
        with self.assertRaisesRegex(ValueError,'card_id_mismatch'):self.run_import(apply=True)
        self.clean()
    def test_source_or_updated_at_change_blocks(self):
        for field,value in [('source_text','new body'),('updated_at',self.now+100),('content_source','different')]:
            self.f.change('UPDATE analyses SET '+field+'=? WHERE entry_id=12',value)
            with self.assertRaisesRegex(ValueError,'analysis_source_snapshot_changed'):self.run_import(apply=True)
            self.f.change('UPDATE analyses SET '+field+'=? WHERE entry_id=12',self.now-1 if field=='updated_at' else None);self.clean()
    def test_claim_lease_and_done_block(self):
        self.f.change('INSERT INTO kaggle_prepare_leases VALUES (12,?,?)','other',time.time()+660)
        with self.assertRaisesRegex(ValueError,'entry_claimed_or_leased'):self.run_import(apply=True)
        self.f.change('DELETE FROM kaggle_prepare_leases');self.f.change("UPDATE analyses SET state='done' WHERE entry_id=12")
        with self.assertRaisesRegex(ValueError,'existing_result_preserved'):self.run_import(apply=True)
        self.f.change("UPDATE analyses SET state='pending' WHERE entry_id=12")
        with sqlite3.connect(self.f.peer/'batches.sqlite3') as c:c.executescript("INSERT INTO batches VALUES ('unknown','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','submit_unknown',NULL,NULL,0);INSERT INTO batch_claims VALUES ('unknown',12);")
        with self.assertRaisesRegex(ValueError,'entry_claimed_or_leased'):self.run_import(apply=True)
        self.clean()
    def test_last_article_failure_rolls_back_every_table(self):
        self.f.change("CREATE TRIGGER stop_last BEFORE UPDATE ON analyses WHEN NEW.entry_id=12 BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.run_import(apply=True)
        self.clean();self.assertEqual(self.f.query('SELECT status FROM card_translations'),[('pending',)])
    def test_fresh_upstream_after_backup(self):
        calls=[]
        def reader():
            calls.append(1);v=copy.deepcopy(self.upstream)
            if len(calls)>1:v[12]['title']='changed'
            return v
        with self.assertRaisesRegex(ValueError,'upstream_changed'):urlimp.import_batch(self.config,self.packet,self.results,reader,apply=True,batch_limit=12,backup=self.f.backup)
        self.assertEqual(len(calls),2);self.clean()
    def test_prompt_paid_workers_card_versions_protected(self):
        self.f.change('UPDATE settings SET value=?',json.dumps({**self.f.settings,'enabled':True}))
        with self.assertRaisesRegex(ValueError,'existing_paid_workers_enabled'):self.run_import(apply=True)
        self.f.change('UPDATE settings SET value=?',json.dumps(self.f.settings));self.upstream[2]['card_source']['source_hash']='changed'
        with self.assertRaisesRegex(ValueError,'live_card_source_changed'):self.run_import(apply=True)
        self.clean()
    def test_wrong_manifest_hash_or_cutoff_rejected(self):
        self.packet['manifest_hash']='bad'
        with self.assertRaisesRegex(ValueError,'manifest_integrity'):self.run_import(apply=True)
        self.packet['cutoff']=time.time()+10000;self.rehash()
        with self.assertRaisesRegex(ValueError,'invalid_frozen_cutoff'):self.run_import(apply=True)
    def test_unsafe_urls_rejected(self):
        for value in ('http://localhost/a','http://127.0.0.1/a','https://u:p@example.com/a','https://example.com/a?token=secret','https://example.com:8443/a'):
            with self.assertRaises(ValueError):urlimp.public_url(value)
    def test_explicit_skipped_receipt_never_writes_skipped_entry(self):
        self.results['articles']=self.results['articles'][:-1]
        self.results['skipped']=[{'entry_id':12,'status':'source_unavailable','reason':'cookie_permission_pending'}]
        before=self.f.query('SELECT * FROM analyses WHERE entry_id=12')
        out=self.run_import(apply=True);self.assertEqual(out['analysis_count'],11)
        self.assertEqual(before,self.f.query('SELECT * FROM analyses WHERE entry_id=12'))
        self.assertEqual(json.loads(self.f.query('SELECT skipped FROM dot_url_batch_receipts')[0][0]),self.results['skipped'])
    def test_overlap_or_foreign_skipped_id_rejected(self):
        for eid in (1,999):
            self.results['skipped']=[{'entry_id':eid,'status':'source_unavailable','reason':'blocked'}]
            with self.assertRaisesRegex(ValueError,'invalid_skipped'):self.run_import(apply=True)
            self.clean()
    def test_unknown_source_data_cannot_archive_body(self):
        self.results['articles'][0]['source']['raw_html']='unrequested archived content';self.rehash()
        with self.assertRaisesRegex(ValueError,'unknown_source_fields'):self.run_import(apply=True)
        self.clean()
    def test_claim_during_backup_aborts_all_writes(self):
        def backup(*args):
            with sqlite3.connect(self.f.peer/'batches.sqlite3') as db:
                db.executescript("INSERT INTO batches VALUES ('late','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','submit_unknown',NULL,NULL,0);INSERT INTO batch_claims VALUES ('late',12);")
            return 'b'*64
        with self.assertRaisesRegex(ValueError,'entry_claimed_or_leased'):
            urlimp.import_batch(self.config,self.packet,self.results,lambda:copy.deepcopy(self.upstream),apply=True,batch_limit=12,backup=backup)
        self.clean()
    def test_missing_lane_fails_closed(self):
        self.config['peer_state_roots'].append(str(self.f.root/'absent'))
        with self.assertRaises(sqlite3.OperationalError):self.run_import(apply=True)
        self.clean()
    def test_prior_card_version_preserved(self):
        self.f.change("INSERT INTO card_translation_versions VALUES (2,1,'preserve-source-hash','Original 2','旧中文标题','旧中文摘要','source_excerpt','old-model',1)")
        with self.assertRaisesRegex(ValueError,'existing_translation_version_preserved'):self.run_import(apply=True)
        self.assertEqual(self.f.query('SELECT model FROM card_translation_versions'),[('old-model',)])
        self.assertEqual(self.f.query("SELECT count(*) FROM analyses WHERE state='done'"),[(0,)])
    def test_read_must_be_after_frozen_snapshot(self):
        self.results['articles'][0]['source']['fetched_at_utc']=dt.datetime.fromtimestamp(self.now-1,dt.timezone.utc).isoformat();self.rehash()
        with self.assertRaisesRegex(ValueError,'invalid_read_timestamp'):self.run_import(apply=True)
        self.clean()
    def test_reference_done_survives_discovery_without_fake_cache_coverage(self):
        import ast
        from contextlib import contextmanager
        from unittest.mock import patch
        self.run_import(apply=True)
        self.f.change('ALTER TABLE analyses ADD COLUMN canonical TEXT')
        @contextmanager
        def connect():
            with sqlite3.connect(self.db) as db:
                db.row_factory=sqlite3.Row
                yield db
        core_path=pathlib.Path(__file__).resolve().parents[1]/'src/core.py'
        namespace={'time':time,'connect':connect}
        exec(fixture.functions(core_path,{'canonical_url','discover'}),namespace)
        before=self.f.query('SELECT state,result,score,model,updated_at FROM analyses WHERE entry_id=1')
        with patch('card_translation.enqueue',create=True):
            namespace['discover']([{'id':1,'user_id':1,'feed_id':1,'title':'Original 1','url':'https://example.com/1','published_at':'2026-10-01T00:00:00Z'}])
        self.assertEqual(before,self.f.query('SELECT state,result,score,model,updated_at FROM analyses WHERE entry_id=1'))
        constants=[n.value for n in ast.walk(ast.parse(core_path.read_text())) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
        coverage=next(s for s in constants if 'AS ai_done' in s and 'AS source_text_ready' in s)
        with connect() as db:
            counts=dict(db.execute(coverage,(1,)).fetchone())
        self.assertEqual((counts['ai_done'],counts['source_text_ready'],counts['substantial_source_text']),(12,0,0))
        api_path=pathlib.Path(__file__).resolve().parents[1]/'src/api.py'
        api_text=api_path.read_text();node=next(n for n in ast.parse(api_text).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='ai_entries')
        route=ast.get_source_segment(api_text,node)
        self.assertIn('"state=\'done\'", "score>=?"',route)
        self.assertNotIn('source_text',route)
    def test_real_backup_and_receipt(self):
        out=urlimp.import_batch(self.config,self.packet,self.results,lambda:copy.deepcopy(self.upstream),batch_limit=12,apply=True)
        folder=self.f.root/'dot-article-imports'/out['batch_id'];receipt=json.loads((folder/'backup-receipt.json').read_text())
        self.assertEqual(receipt['sha256'],hashlib.sha256((folder/'before-import.sqlite3').read_bytes()).hexdigest())
        self.assertEqual(json.loads((folder/'export.json').read_text())['schema'],'dot-url-manifest-v1')


if __name__=='__main__':unittest.main()
