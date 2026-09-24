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

    async def prepare(self,fetch,excluded=(),allowed=None):
        with patch('cloud_bridge.load_inbox',return_value=(self.core,self.worker,self.cards)), \
             patch.dict('sys.modules',{'content_input':SimpleNamespace(content_text=lambda text:(text,0),is_our_social_feed=lambda url:False),
                                      'product_source':SimpleNamespace(is_product_entry=lambda entry:False),
                                      'prepared_content':SimpleNamespace(apply=lambda entry:entry)}), \
             patch('fulltext_source.fetch',fetch):
            return await prepare_sample('.',1,excluded,allowed)

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

    async def test_concurrent_completed_result_is_preserved(self):
        async def fetch(url):
            self.db.execute("UPDATE analyses SET state='done'")
            self.db.commit()
            return self.body
        sample=await self.prepare(fetch)
        self.assertEqual([],sample['samples'])
        self.assertEqual('source_changed_during_fulltext',sample['skipped'][0]['state'])
        self.assertEqual('done',self.db.execute('SELECT state FROM analyses').fetchone()[0])

if __name__=='__main__':
    unittest.main()
