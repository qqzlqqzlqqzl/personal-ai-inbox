"""Synthetic SQLite integration; no model, provider, network or production files."""
import ast
import copy
import hashlib
import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import export_dot_articles_readonly as exporter
import dot_article_import as imp

FIXTURES=ROOT/'src'


def functions(path,names):
    text=path.read_text(); tree=ast.parse(text)
    return '\n\n'.join(ast.get_source_segment(text,node) for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.saved_path=list(sys.path)
        self.module_names=['worker','card_translation','kaggle_batch','kaggle_batch.scoring_policy']
        self.saved_modules={name:sys.modules.get(name) for name in self.module_names}
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
        self.source=self.root/'src';(self.source/'kaggle_batch').mkdir(parents=True)
        (self.source/'kaggle_batch/__init__.py').write_text('')
        (self.source/'worker.py').write_text(functions(FIXTURES/'worker.py',{'validate_result'}))
        (self.source/'card_translation.py').write_text('import re,json\nPROMPT="真实卡片提示"\nVERSION="zh-cards-v1"\n'+functions(FIXTURES/'card_translation.py',{'is_chinese','chinese_translation','validate_items'}))
        (self.source/'core.py').write_text('DEFAULT_PROMPT="reading-priority-v2 当前真实评分提示"\n')
        (self.source/'kaggle_batch/build_manifest.py').write_text('ANALYSIS_FIDELITY="逐字引用"\nTRANSLATION_FIDELITY="忠实翻译"\n')
        (self.source/'kaggle_batch/scoring_policy.py').write_text((FIXTURES/'kaggle_batch/scoring_policy.py').read_text())
        for name in ['worker','card_translation','kaggle_batch','kaggle_batch.scoring_policy']:
            sys.modules.pop(name,None)
        self.db=self.root/'analysis.db'
        c=sqlite3.connect(self.db)
        c.executescript('''
CREATE TABLE settings(name TEXT PRIMARY KEY,value TEXT);
CREATE TABLE analyses(entry_id INTEGER PRIMARY KEY,user_id INTEGER,feed_id INTEGER,title TEXT,url TEXT,published_at TEXT,state TEXT,attempts INTEGER,next_try REAL,content_hash TEXT,source_text TEXT,truncated INTEGER,content_source TEXT,source_chars INTEGER,extracted_at REAL,result TEXT,score REAL,technical_score REAL,business_score REAL,model TEXT,prompt_hash TEXT,tokens INTEGER,analyzed_at REAL,updated_at REAL,error TEXT);
CREATE TABLE card_translations(entry_id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,source_hash TEXT,original_title TEXT,excerpt TEXT,source_kind TEXT,attempts INTEGER,next_try REAL,title_zh TEXT,summary_zh TEXT,model TEXT,error TEXT,updated_at REAL,translated_at REAL);
CREATE TABLE card_translation_versions(entry_id INTEGER,user_id INTEGER,source_hash TEXT,original_title TEXT,title_zh TEXT,summary_zh TEXT,source_kind TEXT,model TEXT,translated_at REAL,PRIMARY KEY(entry_id,user_id,source_hash));
CREATE TABLE kaggle_prepare_leases(entry_id INTEGER,owner TEXT,expires REAL);
CREATE TABLE kaggle_imports(batch_id TEXT,item_id TEXT,input_hash TEXT,imported_at REAL);
INSERT INTO kaggle_imports VALUES ('old-kaggle','old-item','old-hash',1);
''')
        self.settings={'prompt':'reading-priority-v2 当前真实评分提示','enabled':False,'translation_enabled':False,'max_output_tokens':1500,'model':'unchanged-paid-setting','daily_tokens':1234}
        c.execute('INSERT INTO settings VALUES (?,?)',('preferences',json.dumps(self.settings)))
        for eid in (1,2,3):
            text='This is an exact evidence sentence for article '+str(eid)+'. 本文提供了可核验的实现步骤以及限制条件。'
            c.execute('INSERT INTO analyses(entry_id,user_id,feed_id,title,url,published_at,state,attempts,next_try,content_hash,source_text,truncated,content_source,source_chars,extracted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(eid,1,1,'Original '+str(eid),'https://example.com/'+str(eid),'2026-10-01T00:00:00Z','waiting_model',0,0,'html-hash-'+str(eid),text,0,'original_url_site_rule',len(text),100.0))
        c.execute('INSERT INTO card_translations(entry_id,user_id,status,source_hash,original_title,excerpt,source_kind,attempts,next_try) VALUES (?,?,?,?,?,?,?,?,?)',(2,1,'pending','preserve-source-hash','Original 2','Original source excerpt','source_excerpt',0,0))
        c.commit();c.close()
        self.peer=self.root/'lane';self.peer.mkdir();c=sqlite3.connect(self.peer/'batches.sqlite3');c.executescript('CREATE TABLE batches(id TEXT,state TEXT);CREATE TABLE batch_claims(batch_id TEXT,entry_id INTEGER);');c.commit();c.close()
        self.coord=self.root/'coord';self.coord.mkdir();(self.coord/'bridge.lock').write_text('')
        self.config={'source':str(self.source),'database':str(self.db),'peer_state_roots':[str(self.peer)],'state_root':str(self.peer),'scope_user_id':1,'coordination_root':str(self.coord)}
        self.packet=exporter.export(self.config,enabled_feed_ids={1})
        self.result={'schema':'dot-article-results-v1','export_hash':self.packet['export_hash'],'producer':dict(imp.PRODUCER),'articles':[]}
        self.upstream={}
        for a in self.packet['articles']:
            self.result['articles'].append({'entry_id':a['entry_id'],'snapshot_hash':a['snapshot_hash'],
                'analysis':{'score':7.5,'technical_score':8.0,'business_score':3.0,'summary':'文章介绍了有明确步骤和限制的技术实践。','reason':'优先看：正文给出了可以实践的具体步骤和条件','evidence':'This is an exact evidence sentence','content_type':'教程','worth_reading':True,'tags':['技术实践']},
                'card':{'title':'技术实践的具体步骤','summary':'本文介绍了实现的具体步骤与限制条件。'} if a['card'] else None})
            self.upstream[a['entry_id']]={key:a[key] for key in ('entry_id','user_id','feed_id','title','url','content_hash')};self.upstream[a['entry_id']]['feed_enabled']=True
            if a['card']:self.upstream[a['entry_id']]['card_source']={k:a['card'][k] for k in ('source_hash','original_title','excerpt','source_kind')}
        self.backups=[]
    def tearDown(self):
        self.temp.cleanup()
        sys.path[:]=self.saved_path
        for name,previous in self.saved_modules.items():
            if previous is None:sys.modules.pop(name,None)
            else:sys.modules[name]=previous
    def backup(self,*args):self.backups.append(1);return 'b'*64
    def run_import(self,**kwargs):return imp.import_batch(self.config,self.packet,self.result,lambda:copy.deepcopy(self.upstream),backup=self.backup,**kwargs)
    def query(self,sql,*args):
        with sqlite3.connect(self.db) as c:return c.execute(sql,args).fetchall()
    def change(self,sql,*args):
        with sqlite3.connect(self.db) as c:c.execute(sql,args)
    def assert_clean(self):
        self.assertEqual(self.query("SELECT count(*) FROM analyses WHERE state='done'")[0][0],0)
        self.assertEqual(self.query("SELECT count(*) FROM sqlite_master WHERE name='dot_imports'")[0][0],0)
        self.assertEqual(self.query('SELECT * FROM kaggle_imports'),[('old-kaggle','old-item','old-hash',1.0)])
        self.assertEqual(self.query('SELECT count(*) FROM card_translation_versions')[0][0],0)
    def test_dry_run_no_database_or_backup_write(self):
        before=hashlib.sha256(self.db.read_bytes()).hexdigest();out=self.run_import()
        self.assertEqual(out['state'],'validated_no_write');self.assertEqual(self.backups,[])
        self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest());self.assert_clean()
    def test_success_and_idempotent_replay(self):
        original=self.query('SELECT value FROM settings')[0][0]
        out=self.run_import(apply=True);self.assertEqual((out['analysis_count'],out['card_count']),(3,1))
        self.assertEqual(self.query('SELECT DISTINCT model FROM analyses'),[('dot/gpt-6-astra',)])
        self.assertEqual(self.query('SELECT model,reasoning FROM dot_imports'),[('dot/gpt-6-astra','xhigh')])
        self.assertEqual(self.query('SELECT tokens FROM analyses'),[(None,),(None,),(None,)])
        self.assertEqual(self.query('SELECT value FROM settings')[0][0],original)
        self.assertEqual(self.query('SELECT source_hash FROM card_translations'),[('preserve-source-hash',)])
        self.assertEqual(self.query('SELECT count(*) FROM kaggle_imports')[0][0],1)
        out=self.run_import(apply=True);self.assertEqual(out['state'],'already_imported');self.assertEqual(len(self.backups),1)
    def test_same_batch_changed_result_is_rejected(self):
        self.run_import(apply=True);self.result['articles'][0]['analysis']['reason']='优先看：修改了结果但不能覆盖原导入'
        with self.assertRaisesRegex(ValueError,'import_identity_collision'):self.run_import(apply=True)
    def test_existing_done_is_preserved(self):
        self.change("UPDATE analyses SET state='done',result='user existing',model='existing' WHERE entry_id=2")
        with self.assertRaisesRegex(ValueError,'existing_result_preserved'):self.run_import(apply=True)
        self.assertEqual(self.query('SELECT result,model FROM analyses WHERE entry_id=2'),[('user existing','existing')])
        self.assertEqual(self.query("SELECT count(*) FROM analyses WHERE state='done'")[0][0],1)
    def test_claim_after_export_blocks_entire_batch(self):
        c=sqlite3.connect(self.peer/'batches.sqlite3');c.execute("INSERT INTO batches VALUES ('unknown','submit_unknown')");c.execute("INSERT INTO batch_claims VALUES ('unknown',2)");c.commit();c.close()
        with self.assertRaisesRegex(ValueError,'entry_claimed_or_leased'):self.run_import(apply=True)
        self.assert_clean()
    def test_active_lease_after_export_blocks(self):
        self.change('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)',2,'other',time.time()+660)
        with self.assertRaisesRegex(ValueError,'entry_claimed_or_leased'):self.run_import(apply=True)
        self.assert_clean()
    def test_claim_during_backup_is_caught_again_inside_transaction(self):
        def racing(*args):
            c=sqlite3.connect(self.peer/'batches.sqlite3');c.execute("INSERT INTO batches VALUES ('unknown','submit_unknown')");c.execute("INSERT INTO batch_claims VALUES ('unknown',1)");c.commit();c.close();return 'b'*64
        with self.assertRaisesRegex(ValueError,'entry_claimed_or_leased'):
            imp.import_batch(self.config,self.packet,self.result,lambda:copy.deepcopy(self.upstream),apply=True,backup=racing)
        self.assert_clean()
    def test_source_body_change_despite_same_html_hash_blocks(self):
        self.change("UPDATE analyses SET source_text=source_text||' changed' WHERE entry_id=1")
        with self.assertRaisesRegex(ValueError,'analysis_source_snapshot_changed'):self.run_import(apply=True)
        self.assert_clean()
    def test_prompt_change_blocks(self):
        cfg={**self.settings,'prompt':'changed'};self.change('UPDATE settings SET value=?',json.dumps(cfg))
        with self.assertRaisesRegex(ValueError,'prompt_or_policy_changed'):self.run_import(apply=True)
        self.assert_clean()
    def test_enabled_paid_worker_blocks(self):
        self.change('UPDATE settings SET value=?',json.dumps({**self.settings,'enabled':True}))
        with self.assertRaisesRegex(ValueError,'existing_paid_workers_enabled'):self.run_import(apply=True)
        self.assert_clean()
    def test_upstream_change_and_disabled_feed_block(self):
        for field,value in [('content_hash','changed'),('feed_enabled',False),('title','changed'),('user_id',2)]:
            with self.subTest(field=field):
                before=self.upstream[1][field];self.upstream[1][field]=value
                with self.assertRaisesRegex(ValueError,'upstream_changed_or_unavailable'):self.run_import(apply=True)
                self.upstream[1][field]=before;self.assert_clean()
    def test_card_changed_after_export_blocks(self):
        self.change("UPDATE card_translations SET excerpt='changed' WHERE entry_id=2")
        with self.assertRaisesRegex(ValueError,'card_source_snapshot_changed'):self.run_import(apply=True)
        self.assert_clean()
    def test_forged_model_or_hash_blocks(self):
        self.result['producer']['model']='qwen'
        with self.assertRaisesRegex(ValueError,'wrong_execution_provenance'):self.run_import(apply=True)
        self.result['producer']=dict(imp.PRODUCER);self.result['articles'][0]['snapshot_hash']='wrong'
        with self.assertRaisesRegex(ValueError,'result_input_version_mismatch'):self.run_import(apply=True)
        self.assert_clean()
    def test_strict_output_rejects_silent_truncation_and_nonexact_quote(self):
        for field,value in [('summary','中'*121),('reason','中'*101),('evidence','not in original text'),('score',float('nan')),('worth_reading',False),('tags',['t']*7)]:
            with self.subTest(field=field):
                before=self.result['articles'][0]['analysis'][field];self.result['articles'][0]['analysis'][field]=value
                with self.assertRaises(ValueError):self.run_import(apply=True)
                self.result['articles'][0]['analysis'][field]=before;self.assert_clean()
    def test_missing_article_or_card_blocks_all(self):
        old=self.result['articles'].pop()
        with self.assertRaisesRegex(ValueError,'incomplete_result_batch'):self.run_import(apply=True)
        self.result['articles'].append(old)
        for r in self.result['articles']:
            if r['card'] is not None:r['card']=None
        with self.assertRaisesRegex(ValueError,'card_schema_mismatch'):self.run_import(apply=True)
        self.assert_clean()
    def test_mid_transaction_card_failure_rolls_back_analyses_and_receipt(self):
        self.change("CREATE TRIGGER prevent_card BEFORE INSERT ON card_translation_versions BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.run_import(apply=True)
        self.assert_clean();self.assertEqual(self.query('SELECT status FROM card_translations'),[('pending',)])
    def test_real_backup_is_verified_and_retryable_without_database_mutation(self):
        bid,rhash=imp.identity(self.packet,self.result)
        checksum=imp.backup_database(self.config,self.packet,self.result,bid)
        path=self.root/'dot-article-imports'/bid/'before-import.sqlite3'
        self.assertEqual(checksum,hashlib.sha256(path.read_bytes()).hexdigest())
        with sqlite3.connect(path) as c:self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0],'ok')
        self.assertEqual(imp.backup_database(self.config,self.packet,self.result,bid),checksum)
        self.assert_clean()

    def test_live_card_hash_changed_blocks(self):
        self.upstream[2]['card_source']['source_hash']='changed'
        with self.assertRaisesRegex(ValueError,'live_card_source_changed'):self.run_import(apply=True)
        self.assert_clean()
    def test_existing_card_version_is_never_replaced(self):
        self.change("INSERT INTO card_translation_versions VALUES (2,1,'preserve-source-hash','Original 2','已有中文标题','已有中文简介','source_excerpt','old-model',1)")
        with self.assertRaisesRegex(ValueError,'existing_translation_version_preserved'):self.run_import(apply=True)
        self.assertEqual(self.query('SELECT title_zh,model FROM card_translation_versions'),[('已有中文标题','old-model')])
        self.assertEqual(self.query("SELECT count(*) FROM analyses WHERE state='done'")[0][0],0)
    def test_upstream_refresh_after_backup_catches_change(self):
        calls=[]
        def reader():
            calls.append(1);snapshot=copy.deepcopy(self.upstream)
            if len(calls)==2:snapshot[1]['content_hash']='changed-during-backup'
            return snapshot
        with self.assertRaisesRegex(ValueError,'upstream_changed_or_unavailable'):
            imp.import_batch(self.config,self.packet,self.result,reader,apply=True,backup=self.backup)
        self.assertEqual(len(calls),2);self.assertEqual(len(self.backups),1);self.assert_clean()

    def test_own_lane_claim_is_checked_when_peer_list_omits_own(self):
        other=self.root/'other-lane';other.mkdir();c=sqlite3.connect(other/'batches.sqlite3')
        c.executescript('CREATE TABLE batches(id TEXT,state TEXT); CREATE TABLE batch_claims(batch_id TEXT,entry_id INTEGER);');c.commit();c.close()
        self.config['peer_state_roots']=[str(other)]
        c=sqlite3.connect(self.peer/'batches.sqlite3');c.execute("INSERT INTO batches VALUES ('own-unknown','submit_unknown')");c.execute("INSERT INTO batch_claims VALUES ('own-unknown',2)");c.commit();c.close()
        with self.assertRaisesRegex(ValueError,'entry_claimed_or_leased'):self.run_import(apply=True)
        self.assert_clean()

if __name__=='__main__':unittest.main()
