"""Dependency-free contract checks, not a substitute for API/BeautifulSoup tests.

Run directly: python -B tests/test_reader_batch_contract_stdlib.py
Actual changed definitions are extracted with AST. SQLite and cache concurrency
are real; excerpt parsing is explicitly stubbed because this runner needs only
the standard library. Full parsing/API equivalence lives in the pytest tests.
"""
import ast
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def definitions(file, names, namespace):
    tree = ast.parse((ROOT / 'src' / file).read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('Expected production definitions are missing')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'src' / file), 'exec'), namespace)
    return namespace


class CacheContractTests(unittest.TestCase):
    def setUp(self):
        self.parsed = []

        def parse(html):
            self.parsed.append(html)
            return 'excerpt-' + hashlib.sha256(html.encode()).hexdigest(), 'source_excerpt'

        self.ns = definitions('card_translation.py', ['SourceExcerptCache', 'source_card'], {
            'OrderedDict': OrderedDict, 'threading': threading, 'sys': sys,
            'hashlib': hashlib, 'json': json, 'VERSION': 'zh-cards-v1',
            '_source_excerpt': parse,
            'core': SimpleNamespace(hash_text=lambda text: hashlib.sha256(text.encode()).hexdigest()),
        })
        self.cache = self.ns['SourceExcerptCache']()

    def card(self, html, owner=1, title='Title', model='model', cache=None):
        return self.ns['source_card']({'user_id': owner, 'content': html, 'title': title}, model,
                                     excerpt_cache=self.cache if cache is None else cache)

    def test_unique_cold_and_next_request_warm(self):
        first = [self.card(f'<p>Body {i}</p>') for i in range(30)]
        self.assertEqual(len(self.parsed), 30)
        self.assertEqual(first, [self.card(f'<p>Body {i}</p>') for i in range(30)])
        self.assertEqual(len(self.parsed), 30)

    def test_owner_source_title_model_version_and_original_fingerprint(self):
        first = self.card('<p>Original</p>')
        changed = self.card('<p>Original</p>', title='Different', model='other')
        self.assertEqual(len(self.parsed), 1)
        self.assertNotEqual(first[3], changed[3])
        self.assertEqual(changed[3], hashlib.sha256(json.dumps(
            ['zh-cards-v1', 'other', 'Different', changed[1]], ensure_ascii=False).encode()).hexdigest())
        self.card('<p>Original</p>', owner=2)
        self.card('<p>Changed!</p>')
        self.ns['VERSION'] = 'changed'
        self.card('<p>Original</p>')
        self.assertEqual(len(self.parsed), 4)

    def test_sha256_key_tracks_the_current_input(self):
        a, b = self.card('same-size-A'), self.card('same-size-B')
        self.assertNotEqual(a, b)
        self.assertEqual(len(self.parsed), 2)
        self.assertEqual({key[-1] for key in self.cache._items}, {
            hashlib.sha256(b'same-size-A').digest(), hashlib.sha256(b'same-size-B').digest()})

    def test_no_complete_html_is_retained_and_capacity_is_bounded(self):
        cache = self.ns['SourceExcerptCache'](max_entries=2, max_bytes=4096)
        for i in range(20):
            self.card(('PRIVATE_BODY_MARKER_' + str(i)) * 2000, cache=cache)
            self.assertLessEqual(len(cache._items), 2)
            self.assertLessEqual(cache._bytes, 4096)
        for key, (value, size) in cache._items.items():
            self.assertEqual(len(key[-1]), 32)
            self.assertNotIn('PRIVATE_BODY_MARKER', repr((key, value)))
            self.assertGreater(size, 0)
        tiny = self.ns['SourceExcerptCache'](max_entries=2, max_bytes=1)
        self.card('uncached', cache=tiny)
        self.assertFalse(tiny._items)

    def test_invalid_owner_bypasses_cache_and_tail_obeys_original_slice(self):
        self.card('same', owner=True)
        self.card('same', owner=True)
        self.assertEqual(len(self.parsed), 2)
        a = self.card('x' * 50000 + 'tail A')
        b = self.card('x' * 50000 + 'tail B')
        self.assertEqual(a, b)
        self.assertEqual(len(self.parsed), 3)

    def test_concurrent_reads_are_bounded_and_consistent(self):
        cache = self.ns['SourceExcerptCache'](max_entries=4, max_bytes=8192)
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda i: self.card('body-' + str(i % 7), cache=cache), range(100)))
        for index, value in enumerate(values):
            self.assertEqual(value, self.card('body-' + str(index % 7), cache=cache))
        self.assertLessEqual(len(cache._items), 4)
        self.assertLessEqual(cache._bytes, 8192)


class BatchSqliteContractTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE analyses(entry_id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT);
            CREATE TABLE entry_notes(user_id INTEGER,entry_id INTEGER,note TEXT,updated_at REAL);
            CREATE TABLE card_translations(entry_id INTEGER PRIMARY KEY,user_id INTEGER,status TEXT,
                title_zh TEXT,summary_zh TEXT,source_kind TEXT,translated_at REAL,original_title TEXT,error TEXT);
            CREATE TABLE prepared_articles(entry_id INTEGER PRIMARY KEY,user_id INTEGER,url TEXT,title TEXT,
                content TEXT,kind TEXT,input_text_hash TEXT,prepared_at REAL,source_receipt TEXT);
        ''')
        self.statements, self.connections = [], []
        self.db.set_trace_callback(self.statements.append)

        @contextmanager
        def connect():
            self.connections.append(True)
            yield self.db

        self.core = definitions('core.py', ['reader_id_batches', 'load_reader_batch'], {'connect': connect})
        self.prepared = definitions('prepared_content.py',
            ['input_hash', 'apply', '_apply_row', 'PreparedBatch', 'prepare_many'], {
                'core': SimpleNamespace(connect=connect, reader_id_batches=self.core['reader_id_batches']),
                'hashlib': hashlib, 'json': json,
                'deepcopy': deepcopy,
                'check': lambda admission: admission() if admission is not None else None,
            })

    def tearDown(self):
        self.db.close()

    def test_batch_selects_are_owner_scoped_and_missing_notes_are_loaded(self):
        self.db.execute("INSERT INTO analyses VALUES (1,1,'Owner one')")
        self.db.executemany('INSERT INTO entry_notes VALUES (?,?,?,?)', [(1, 1, 'private', 11), (2, 1, 'owner two', 22)])
        self.db.execute("INSERT INTO card_translations VALUES (1,1,'done','中文','摘要','source_excerpt',123,'Title',NULL)")
        self.statements.clear()
        entries = [{'id': 1, 'user_id': 1}, {'id': 1, 'user_id': 2}]
        result = self.core['load_reader_batch'](entries, prepared_batch=None, settings_snapshot={})
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(set(result['analyses']), {(1, 1)})
        self.assertEqual(set(result['cards']), {(1, 1)})
        self.assertEqual(result['notes'][(2, 1)]['note'], 'owner two')
        self.assertEqual(result['analyses'][(1, 1)]['note_updated_at'], 11)
        self.assertEqual(len([sql for sql in self.statements if sql.startswith('SELECT')]), 5)

    def test_sqlite_parameter_chunks_are_bounded(self):
        entries = [{'id': i, 'user_id': 1} for i in range(801)]
        groups = list(self.core['reader_id_batches'](entries))
        self.assertEqual([len(ids) for _, ids in groups], [400, 400, 1])
        self.core['load_reader_batch'](entries, prepared_batch=None, settings_snapshot={})
        self.assertEqual(len(self.connections), 1)

    def test_prepared_link_identity_and_source_changes_are_not_hidden(self):
        entry = {'id': 1, 'user_id': 1, 'url': 'https://example.org/a', 'title': 'Title',
                 'content': '<p>Follow <a href="https://source.example/a">source</a></p>'}
        digest = hashlib.sha256(entry['content'].encode()).hexdigest()
        self.db.execute('INSERT INTO prepared_articles VALUES (?,?,?,?,?,?,?,?,?)',
            (1, 1, entry['url'], entry['title'], '<p>Full body</p>', 'adafruit_linked_original', digest, 123,
             json.dumps({'url': 'https://source.example/a'})))
        changed = {**entry, 'content': entry['content'].replace('/a"', '/b"')}
        other = {**entry, 'user_id': 2}
        batch = self.prepared['prepare_many']([entry, changed, other])
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(batch.apply(entry)['content'], '<p>Full body</p>')
        self.assertEqual(batch.apply(changed), changed)
        self.assertEqual(batch.apply(other), other)
        self.assertEqual(len(self.connections), 1)
        entry['url'] += '/changed'
        self.assertEqual(batch.apply(entry), entry)
        self.assertEqual(len(self.connections), 2)

    def test_prepared_reuse_rechecks_admission_and_preserves_other_fields(self):
        entry = {'id': 1, 'user_id': 1, 'url': 'u', 'title': 't', 'content': 'original', 'status': 'unread'}
        self.db.execute('INSERT INTO prepared_articles VALUES (?,?,?,?,?,?,?,?,?)',
            (1, 1, 'u', 't', 'full', 'adafruit_linked_original', hashlib.sha256(b'original').hexdigest(), 123, None))
        batch = self.prepared['prepare_many']([entry])
        entry['status'] = 'read'
        self.assertEqual(batch.apply(entry)['status'], 'read')

        def stop():
            raise RuntimeError('admission stopped')

        with self.assertRaisesRegex(RuntimeError, 'admission stopped'):
            batch.apply(entry, admission=stop)

    def test_mutable_receipt_does_not_poison_next_prepared_result(self):
        entry = {'id': 1, 'user_id': 1, 'url': 'u', 'title': 't', 'content': 'original'}
        receipt = {'url': 'https://original.example', 'nested': {'source': ['verified']}}
        self.db.execute('INSERT INTO prepared_articles VALUES (?,?,?,?,?,?,?,?,?)',
            (1, 1, 'u', 't', 'full', 'adafruit_linked_original', hashlib.sha256(b'original').hexdigest(),
             123, json.dumps(receipt)))
        batch = self.prepared['prepare_many']([entry])
        first = batch.apply(entry)
        first['fulltext_receipt']['nested']['source'].append('untrusted mutation')
        self.assertEqual(batch.apply(entry)['fulltext_receipt'], receipt)
        self.assertEqual(batch.apply(entry), self.prepared['apply'](entry))

    def test_inherited_native_receipt_stays_current(self):
        entry = {'id': 1, 'user_id': 1, 'url': 'u', 'title': 't', 'content': 'original',
                 'fulltext_receipt': {'url': 'https://native.example/a'}}
        self.db.execute('INSERT INTO prepared_articles VALUES (?,?,?,?,?,?,?,?,?)',
            (1, 1, 'u', 't', 'full', 'adafruit_linked_original', hashlib.sha256(b'original').hexdigest(), 123, None))
        batch = self.prepared['prepare_many']([entry])
        entry['fulltext_receipt']['url'] = 'https://native.example/current'
        self.assertEqual(batch.apply(entry), self.prepared['apply'](entry))


if __name__ == '__main__':
    unittest.main(verbosity=2)
