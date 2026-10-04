import sqlite3
import hashlib
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from cloud_bridge import prepare_sample
from fulltext_source import FulltextUnavailable


class FulltextBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
            CREATE TABLE analyses(entry_id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT,url TEXT,
              state TEXT,next_try REAL,attempts INTEGER,published_at TEXT,truncated INTEGER,
              content_hash TEXT,source_text TEXT,content_source TEXT,source_chars INTEGER,
              input_chars INTEGER,image_count INTEGER,extracted_at REAL,updated_at REAL,error TEXT);
            INSERT INTO analyses VALUES(1,2,'Title','https://go.dev/blog/article','waiting_model',0,0,'2026',
              0,'old-hash','Old RSS excerpt','original_url',15,15,0,0,0,NULL);
            CREATE TABLE card_translations(entry_id INTEGER,user_id INTEGER,status TEXT,next_try REAL,attempts INTEGER);
        ''')
        self.db.execute('ALTER TABLE analyses ADD COLUMN content_quality TEXT')
        self.entry={'id':1,'user_id':2,'title':'Title','url':'https://go.dev/blog/article','content':'Old RSS excerpt'}
        @contextmanager
        def connect():
            with self.db:
                yield self.db
        self.core=SimpleNamespace(connect=connect,settings=lambda:{'prompt':'score','max_output_tokens':1500})
        self.worker=SimpleNamespace(process_one=AsyncMock(),mf_get=AsyncMock(return_value=self.entry),
                                    hash_text=lambda text:hashlib.sha256(text.encode()).hexdigest())
        self.enqueued=[]
        self.cards=SimpleNamespace(PROMPT='translate',enqueue=self.enqueued.append)
        self.body={'source_text':'Complete publisher article','image_count':2,
                   'receipt':{'body_sha256':'body-hash','selector':'.Article .markdown'}}

    def tearDown(self):
        self.db.close()

    async def prepare(self,fetch,excluded=(),allowed=None,independent=False):
        with patch('cloud_bridge.load_inbox',return_value=(self.core,self.worker,self.cards)), \
             patch.dict('sys.modules',{'content_input':SimpleNamespace(content_text=lambda text:(text,0),is_our_social_feed=lambda url:False),
                                      'product_source':SimpleNamespace(is_product_entry=lambda entry:False),
                                      'prepared_content':SimpleNamespace(apply=lambda entry:entry)}), \
             patch('fulltext_source.fetch',fetch):
            return await prepare_sample('.',1,excluded,allowed,independent_cards=independent)

    async def test_pilot_allowlist_excludes_other_queue_entries(self):
        fetch=AsyncMock(return_value=self.body)
        for allowed in ([],[999]):
            sample=await self.prepare(fetch,allowed=allowed)
            self.assertEqual([],sample['samples'])
        fetch.assert_not_called()

    async def test_other_account_claim_prevents_duplicate_fetch(self):
        fetch=AsyncMock(return_value=self.body)
        sample=await self.prepare(fetch,excluded={1})
        self.assertEqual([],sample['samples'])
        self.assertEqual(0,sample['considered'])
        fetch.assert_not_called()

    async def test_model_receives_publisher_body_and_receipt(self):
        sample=await self.prepare(AsyncMock(return_value=self.body))
        self.assertEqual('Complete publisher article',sample['samples'][0]['source_text'])
        self.assertEqual(self.body['receipt'],sample['samples'][0]['fulltext_receipt'])
        self.assertEqual('Complete publisher article',self.db.execute('SELECT source_text FROM analyses').fetchone()[0])
        self.assertEqual(1,len(self.enqueued))
        self.worker.process_one.assert_not_called()

    async def test_edited_headline_does_not_block_unfinished_article(self):
        self.entry['title']='Updated title'
        sample=await self.prepare(AsyncMock(return_value=self.body),independent=True)
        self.assertEqual('Updated title',sample['samples'][0]['title'])
        self.assertEqual('Complete publisher article',sample['samples'][0]['source_text'])
        self.assertEqual('Updated title',self.db.execute('SELECT title FROM analyses').fetchone()[0])

    async def test_title_refresh_does_not_accept_changed_url_or_owner(self):
        for field,value in (('url','https://other.example/article'),('user_id',9)):
            with self.subTest(field=field):
                original=self.entry.copy()
                self.entry.update(title='Updated title',**{field:value})
                fetch=AsyncMock(return_value=self.body)
                sample=await self.prepare(fetch,independent=True)
                self.assertEqual([],sample['samples'])
                fetch.assert_not_called()
                self.assertEqual('Title',self.db.execute('SELECT title FROM analyses').fetchone()[0])
                self.entry.clear();self.entry.update(original)

    async def test_empty_rss_does_not_prevent_fetching_publisher_body(self):
        self.entry['content']=''
        self.db.execute("UPDATE analyses SET source_text=NULL,content_hash=NULL,state='pending'")
        self.db.commit()
        sample=await self.prepare(AsyncMock(return_value=self.body))
        self.assertEqual('Complete publisher article',sample['samples'][0]['source_text'])
        self.assertEqual('waiting_model',self.db.execute('SELECT state FROM analyses').fetchone()[0])

    async def test_unavailable_fulltext_never_queues_excerpt_or_cards(self):
        sample=await self.prepare(AsyncMock(side_effect=FulltextUnavailable('missing_body')))
        self.assertEqual([],sample['samples'])
        self.assertEqual([],self.enqueued)
        self.assertEqual('requires_fulltext_adapter',self.db.execute('SELECT state FROM analyses').fetchone()[0])

    async def test_upstream_change_during_fetch_is_rejected(self):
        self.worker.mf_get.side_effect=[self.entry,{**self.entry,'content':'Changed upstream'}]
        sample=await self.prepare(AsyncMock(return_value=self.body))
        self.assertEqual([],sample['samples'])
        self.assertEqual('upstream_changed_during_fulltext',sample['skipped'][0]['state'])
        self.assertEqual('Old RSS excerpt',self.db.execute('SELECT source_text FROM analyses').fetchone()[0])

    async def test_independent_card_translation_never_scores_missing_body(self):
        self.db.execute("INSERT INTO card_translations VALUES(1,2,'pending',0,0)")
        sample=await self.prepare(AsyncMock(side_effect=FulltextUnavailable('missing_body')),independent=True)
        self.assertEqual(1,len(sample['samples']))
        self.assertTrue(sample['samples'][0]['skip_analysis'])
        self.assertEqual('pending',sample['samples'][0]['card']['status'])
        self.assertEqual('requires_fulltext_adapter',self.db.execute('SELECT state FROM analyses').fetchone()[0])

    async def test_blocked_analysis_can_retry_card_without_fetching_body(self):
        self.db.execute("UPDATE analyses SET state='requires_fulltext_adapter'")
        self.db.execute("INSERT INTO card_translations VALUES(1,2,'pending',0,0)")
        fetch=AsyncMock(side_effect=AssertionError('Must not score excerpt'))
        sample=await self.prepare(fetch,independent=True)
        self.assertTrue(sample['samples'][0]['skip_analysis'])
        fetch.assert_not_called()

    async def test_transient_fetch_failure_has_bounded_delayed_retry(self):
        import time
        sample=await self.prepare(AsyncMock(side_effect=FulltextUnavailable('original_fetch_ReadTimeout')))
        row=self.db.execute('SELECT state,attempts,next_try FROM analyses').fetchone()
        self.assertEqual('fetch_error',row['state'])
        self.assertEqual(1,row['attempts'])
        self.assertGreaterEqual(row['next_try'],time.time()+650)
        self.assertEqual([],sample['samples'])

    async def test_concurrent_completed_result_is_preserved(self):
        async def fetch(url):
            self.db.execute("UPDATE analyses SET state='done'")
            self.db.commit()
            return self.body
        sample=await self.prepare(fetch)
        self.assertEqual([],sample['samples'])
        self.assertEqual('source_changed_during_fulltext',sample['skipped'][0]['state'])
        self.assertEqual('done',self.db.execute('SELECT state FROM analyses').fetchone()[0])

    async def test_quality_receipt_is_persisted_after_exact_source_cas(self):
        from content_quality import assess, public_for_row
        body={**self.body,'content_quality':assess(url=self.entry['url'],
            html_body='<script type="application/ld+json">{"@type":"NewsArticle","url":"https://go.dev/blog/article","isAccessibleForFree":false}</script><article>Preview.</article>',
            extraction_state='available')}
        sample=await self.prepare(AsyncMock(return_value=body))
        self.assertEqual([],sample['samples'])
        row=dict(self.db.execute('SELECT * FROM analyses').fetchone())
        self.assertEqual('content_excluded',row['state'])
        self.assertFalse(public_for_row(row)['recommendation_eligible'])
        self.assertEqual('unknown',public_for_row(row)['access'])

    async def test_changed_database_identity_or_source_does_not_half_write_quality(self):
        for column,value in [('user_id',9),('url','https://changed.example/article'),('content_hash','concurrent-hash')]:
            with self.subTest(column=column):
                self.db.execute("UPDATE analyses SET user_id=2,url='https://go.dev/blog/article',content_hash='old-hash',state='waiting_model',source_text='Old RSS excerpt',content_quality=NULL")
                self.db.commit()
                async def fetch(url):
                    self.db.execute(f'UPDATE analyses SET {column}=?',(value,))
                    self.db.commit()
                    return self.body
                sample=await self.prepare(fetch)
                self.assertEqual([],sample['samples'])
                row=self.db.execute('SELECT source_text,content_quality FROM analyses').fetchone()
                self.assertEqual(('Old RSS excerpt',None),tuple(row))

    async def test_receipt_transaction_failure_preserves_previous_source(self):
        self.db.execute("CREATE TRIGGER reject_quality BEFORE UPDATE OF content_quality ON analyses BEGIN SELECT RAISE(ABORT,'synthetic refusal'); END")
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError,'synthetic refusal'):
            await self.prepare(AsyncMock(return_value=self.body))
        row=self.db.execute('SELECT source_text,content_hash,content_quality FROM analyses').fetchone()
        self.assertEqual(('Old RSS excerpt','old-hash',None),tuple(row))

if __name__=='__main__':
    unittest.main()
