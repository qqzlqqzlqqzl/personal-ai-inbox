from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import unittest
import uuid
from import_results import import_validated

class ImportTests(unittest.TestCase):
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def setUp(self):
        root=Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        root.mkdir(parents=True)
        self.path=root/'inbox.sqlite3'
        with self.db() as db:
            db.executescript('''CREATE TABLE analyses(entry_id INTEGER PRIMARY KEY,user_id INTEGER,
                content_hash TEXT,source_text TEXT,state TEXT,result TEXT,score REAL,technical_score REAL,
                business_score REAL,model TEXT,prompt_hash TEXT,tokens INTEGER,analyzed_at REAL,updated_at REAL,
                attempts INTEGER,next_try REAL,error TEXT);
                CREATE TABLE settings(name TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE card_translations(entry_id INTEGER PRIMARY KEY,user_id INTEGER,source_hash TEXT,
                original_title TEXT,excerpt TEXT,source_kind TEXT,title_zh TEXT,summary_zh TEXT,status TEXT,
                model TEXT,attempts INTEGER,next_try REAL,error TEXT,updated_at REAL,translated_at REAL);
                CREATE TABLE card_translation_versions(entry_id INTEGER,user_id INTEGER,source_hash TEXT,
                original_title TEXT,title_zh TEXT,summary_zh TEXT,source_kind TEXT,model TEXT,translated_at REAL,
                PRIMARY KEY(entry_id,user_id,source_hash));''')
            db.execute("ALTER TABLE analyses ADD COLUMN url TEXT DEFAULT 'https://example.org/article'")
            db.execute('ALTER TABLE analyses ADD COLUMN content_quality TEXT')
            db.execute('INSERT INTO settings VALUES (?,?)',('preferences',json.dumps({
                'prompt':'existing prompt','enabled':False,'translation_enabled':False,'daily_articles':300})))
            db.execute('INSERT INTO analyses(entry_id,user_id,content_hash,source_text,state) VALUES (1,2,?,?,?)',
                       ('html-v1','Original technical evidence.','waiting_model'))
        self.ref={'entry_id':1,'user_id':2,'content_hash':'html-v1','source_text':'Original technical evidence.'}
        self.item={'id':'analysis-1','kind':'analysis','input_hash':'input-v1','source_refs':[self.ref],
                   'messages':[{'role':'system','content':'existing prompt'}]}
        self.manifest={'batch_id':'batch-1','manifest_hash':'manifest-1',
            'model':{'filename':'Qwen.gguf','model_revision':'revision123456789'},'items':[self.item]}
        self.validation={'batch_id':'batch-1','manifest_hash':'manifest-1','items':[
            {'id':'analysis-1','valid':True,'input_hash':'input-v1','tokens':42,
             'result':{'score':7,'technical_score':8,'business_score':5,'evidence':'Original technical evidence.'}}]}

    def read(self,table='analyses',entry=1):
        with self.db() as db:
            db.row_factory=sqlite3.Row
            return dict(db.execute('SELECT * FROM '+table+' WHERE entry_id=?',(entry,)).fetchone())

    def apply(self,upstream=(1,)):
        return import_validated(self.path,self.manifest,self.validation,set(upstream))

    def test_import_is_idempotent_and_leaves_settings_unchanged(self):
        self.assertEqual('imported',self.apply()['items'][0]['state'])
        first=self.read()
        self.assertEqual('done',first['state'])
        self.assertEqual(42,first['tokens'])
        self.assertEqual('already_imported',self.apply()['items'][0]['state'])
        self.assertEqual(first,self.read())
        with self.db() as db:
            cfg=json.loads(db.execute('SELECT value FROM settings').fetchone()[0])
        self.assertFalse(cfg['enabled'])
        self.assertFalse(cfg['translation_enabled'])
        self.assertEqual(300,cfg['daily_articles'])

    def test_changed_article_is_not_overwritten(self):
        with self.db() as db:
            db.execute("UPDATE analyses SET content_hash='html-v2',source_text='new input'")
        self.assertEqual('source_changed',self.apply()['items'][0]['state'])
        self.assertIsNone(self.read()['result'])

    def test_diagnostic_interruption_results_never_enter_product(self):
        self.manifest['diagnostic']={'must_not_import':True}
        before=self.read()
        with self.assertRaisesRegex(ValueError,'Diagnostic batch'):
            self.apply()
        self.assertEqual(before,self.read())

    def test_unverified_upstream_cannot_be_imported(self):
        self.assertEqual('upstream_changed_or_unavailable',self.apply(())['items'][0]['state'])
        self.assertIsNone(self.read()['result'])

    def test_existing_success_is_preserved(self):
        with self.db() as db:
            db.execute("UPDATE analyses SET state='done',result='previous-result'")
        self.assertEqual('existing_result_preserved',self.apply()['items'][0]['state'])
        self.assertEqual('previous-result',self.read()['result'])

    def test_import_preserves_current_source_bound_exclusion_and_duplicate_receipt(self):
        from content_quality import assess, serialized, public_for_row
        row=self.read()
        record=assess(url=row['url'],html_body='<div class="paywall">This article is for paid subscribers only.</div>',extraction_state='available',observed_at=10)
        with self.db() as db:
            db.execute('UPDATE analyses SET content_quality=?',(serialized(record,row),))
        self.assertEqual('imported',self.apply()['items'][0]['state'])
        before=self.read()
        self.assertFalse(public_for_row(before)['recommendation_eligible'])
        self.assertEqual('already_imported',self.apply()['items'][0]['state'])
        self.assertEqual(before,self.read())

    def test_quality_receipt_failure_rolls_back_result_and_import_marker(self):
        with self.db() as db:
            db.execute("CREATE TRIGGER reject_quality BEFORE UPDATE OF content_quality ON analyses BEGIN SELECT RAISE(ABORT,'synthetic refusal'); END")
        original=self.read()
        with self.assertRaises(sqlite3.IntegrityError):
            self.apply()
        self.assertEqual(original,self.read())
        with self.db() as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM kaggle_imports').fetchone()[0])

    def test_prompt_change_rejects_stale_result(self):
        with self.db() as db:
            db.execute('UPDATE settings SET value=?',(json.dumps({'prompt':'new prompt'}),))
        self.assertEqual('prompt_changed',self.apply()['items'][0]['state'])

    def cards(self):
        refs=[]
        with self.db() as db:
            for entry in (10,11):
                ref={'entry_id':entry,'user_id':2,'source_hash':'card-'+str(entry),
                     'original_title':'Original','excerpt':'Original excerpt','source_kind':'source_excerpt'}
                refs.append(ref)
                db.execute('''INSERT INTO card_translations(entry_id,user_id,source_hash,original_title,
                    excerpt,source_kind,status) VALUES (?,?,?,?,?,?,'pending')''',tuple(ref.values()))
        self.manifest['items']=[{'id':'cards-0','kind':'translation','input_hash':'cards-v1','source_refs':refs}]
        self.validation['items']=[{'id':'cards-0','valid':True,'input_hash':'cards-v1',
            'result':{'10':['中文标题甲','中文简介甲'],'11':['中文标题乙','中文简介乙']}}]

    def test_translation_updates_cache_and_repeated_import_changes_nothing(self):
        self.cards()
        self.assertEqual('imported',self.apply((10,11))['items'][0]['state'])
        before=self.read('card_translations',10)
        self.assertEqual('done',before['status'])
        self.assertEqual('中文标题甲',before['title_zh'])
        with self.db() as db:
            self.assertEqual(2,db.execute('SELECT count(*) FROM card_translation_versions').fetchone()[0])
        self.assertEqual('already_imported',self.apply((10,11))['items'][0]['state'])
        self.assertEqual(before,self.read('card_translations',10))

    def test_translation_group_rolls_back_on_incomplete_validated_data(self):
        self.cards()
        del self.validation['items'][0]['result']['11']
        with self.assertRaises(ValueError):
            self.apply((10,11))
        self.assertEqual('pending',self.read('card_translations',10)['status'])
        self.assertIsNone(self.read('card_translations',10)['title_zh'])

    def test_changed_translation_source_rejects_group(self):
        self.cards()
        with self.db() as db:
            db.execute("UPDATE card_translations SET source_hash='new' WHERE entry_id=11")
        self.assertEqual('source_changed',self.apply((10,11))['items'][0]['state'])
        self.assertEqual('pending',self.read('card_translations',10)['status'])

if __name__=='__main__':
    unittest.main()
