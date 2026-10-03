"""Synthetic URL exporter contract: SQLite snapshots, zero DML and real importer."""
import copy
import datetime as dt
import hashlib
import io
import json
import pathlib
import sqlite3
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import test_dot_url_article_import as fixture

import dot_url_article_import as importer
import export_dot_urls_readonly as exporter


class URLExportTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.URLImportTests()
        self.f.setUp()
        self.config, self.db = self.f.config, self.f.db
        self.feeds = [{'id': 1, 'user_id': 1, 'disabled': False}]

    def tearDown(self):
        self.f.tearDown()

    def export(self, **kw):
        return exporter.export(self.config, **{'scope_user_id': 1, 'limit': 12,
            'exclude_entry_ids': [], 'feed_reader': lambda _: copy.deepcopy(self.feeds), **kw})

    def change(self, sql, *args):
        self.f.f.change(sql, *args)

    def bind_results(self, packet):
        existing = {r['entry_id']: r for r in self.f.results['articles']}
        self.f.packet = packet
        self.f.results['articles'] = []
        for article in packet['articles']:
            result = copy.deepcopy(existing[article['entry_id']])
            result['manifest_snapshot_hash'] = article['snapshot_hash']
            result['card'] = result['card'] if article['card'] is not None else None
            result['source']['fetched_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
            self.f.results['articles'].append(result)
        self.f.rehash()

    def add_lane(self, name):
        root = self.f.f.root / name
        root.mkdir()
        with sqlite3.connect(root / 'batches.sqlite3') as db:
            db.executescript('CREATE TABLE batches ( id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, state TEXT NOT NULL, remote_status TEXT, error TEXT, updated REAL NOT NULL); CREATE TABLE batch_claims (batch_id TEXT NOT NULL, entry_id INTEGER NOT NULL, PRIMARY KEY(batch_id,entry_id));')
        self.config['peer_state_roots'].append(str(root))
        return root

    def claim(self, root, eid, state='running'):
        with sqlite3.connect(root / 'batches.sqlite3') as db:
            db.execute("INSERT INTO batches (id,state,manifest_hash,updated) VALUES (?,?,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',0)", ('batch-' + str(eid), state))
            db.execute('INSERT INTO batch_claims VALUES (?,?)', ('batch-' + str(eid), eid))

    def test_roundtrip_twelve_through_real_importer_dry_run(self):
        packet = self.export()
        self.assertEqual(packet['entry_ids'], list(range(12, 0, -1)))
        self.assertEqual(packet['selection']['status'], 'full')
        self.bind_results(packet)
        before = self.db.read_bytes()
        result = self.f.run_import()
        self.assertEqual((result['state'], result['analysis_count'], result['card_count']), ('validated_no_write', 12, 1))
        self.assertEqual(before, self.db.read_bytes())
        self.f.clean()
        self.assertEqual(self.f.f.backups, [])

    def test_no_dml_network_or_output_leaks(self):
        self.change("CREATE TABLE entry_notes(note TEXT)")
        self.change("INSERT INTO entry_notes VALUES ('PRIVATE_NOTE_SENTINEL')")
        settings = dict(self.f.f.settings, api_key='SECRET_SENTINEL', base_url='CONFIG_SENTINEL')
        self.change("UPDATE settings SET value=?", json.dumps(settings))
        before = {p: p.read_bytes() for p in self.f.f.root.rglob('*') if p.is_file()}
        statements, actions = [], []
        original_ro = exporter.ro
        writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                  sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_TABLE,
                  sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TRIGGER,
                  sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH}
        def readonly(path):
            db = original_ro(path)
            db.set_trace_callback(statements.append)
            def authorize(action, *_):
                actions.append(action)
                return sqlite3.SQLITE_DENY if action in writes else sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            return db
        with mock.patch.object(exporter, 'ro', side_effect=readonly), mock.patch('socket.socket', side_effect=AssertionError('network_forbidden')):
            packet = self.export()
        self.assertFalse(writes.intersection(actions))
        self.assertTrue(any(s == 'BEGIN' for s in statements))
        self.assertFalse(any('entry_notes' in s for s in statements))
        for p, content in before.items():
            self.assertEqual(p.read_bytes(), content)
        self.assertEqual(set(before), {p for p in self.f.f.root.rglob('*') if p.is_file()})
        output = json.dumps(packet)
        for secret in ('PRIVATE_NOTE_SENTINEL', 'SECRET_SENTINEL', 'CONFIG_SENTINEL', str(self.db)):
            self.assertNotIn(secret, output)
        self.assertNotIn('source_text', packet['articles'][0]['analysis_snapshot'])

    def test_one_wal_snapshot_freezes_settings_analysis_and_card(self):
        with sqlite3.connect(self.db) as db:
            db.execute('PRAGMA journal_mode=WAL')
        read = exporter.legacy.read_context
        def concurrent_writer(config, db):
            context = read(config, db)
            with sqlite3.connect(self.db) as other:
                other.execute("UPDATE analyses SET title='new title' WHERE entry_id=2")
                other.execute("UPDATE card_translations SET excerpt='new excerpt' WHERE entry_id=2")
                other.execute("UPDATE settings SET value=?", (json.dumps(dict(self.f.f.settings, prompt='new prompt')),))
            return context
        with mock.patch.object(exporter.legacy, 'read_context', side_effect=concurrent_writer):
            packet = self.export()
        a = next(a for a in packet['articles'] if a['entry_id'] == 2)
        self.assertEqual(a['title'], 'Original 2')
        self.assertEqual(a['card']['excerpt'], 'Original source excerpt')
        self.assertEqual(packet['analysis_prompt'], self.f.f.settings['prompt'])
        again = self.export()
        b = next(a for a in again['articles'] if a['entry_id'] == 2)
        self.assertEqual((b['title'], b['card']['excerpt'], again['analysis_prompt']), ('new title', 'new excerpt', 'new prompt'))
        self.assertNotEqual(a['snapshot_hash'], b['snapshot_hash'])

    def test_raw_prompt_nulls_and_complete_hash_chain(self):
        prompt = '  中文 e\u0301\r\nreading-priority-v2\n'
        self.change('UPDATE settings SET value=?', json.dumps(dict(self.f.f.settings, prompt=prompt)))
        packet = self.export(exclude_entry_ids=[99, 12])
        self.assertEqual(packet['hash_spec'], exporter.HASH_SPEC)
        self.assertEqual(packet['analysis_prompt'], prompt)
        self.assertEqual(packet['analysis_prompt_sha256'], hashlib.sha256(prompt.encode('utf-8')).hexdigest())
        self.assertEqual(packet['excluded_entry_ids_hash'], exporter.digest([12, 99]))
        self.assertEqual(packet['manifest_hash'], exporter.digest({k: v for k, v in packet.items() if k != 'manifest_hash'}))
        for a in packet['articles']:
            self.assertEqual(set(a['analysis_snapshot']), set(importer.ANALYSIS_FIELDS))
            self.assertIsNone(a['analysis_snapshot']['content_hash'])
            self.assertEqual(a['analysis_snapshot_hash'], exporter.digest(a['analysis_snapshot']))
            self.assertEqual(a['card_snapshot_hash'], exporter.digest(a['card']))
            self.assertEqual(a['snapshot_hash'], exporter.digest({k: v for k, v in a.items() if k != 'snapshot_hash'}))
        self.assertEqual(exporter.digest(None), hashlib.sha256(b'null').hexdigest())

    def test_pre_limit_exclusions_and_deterministic_published_order(self):
        packet = self.export(limit=3, exclude_entry_ids=[12, 11, 10])
        self.assertEqual(packet['entry_ids'], [9, 8, 7])
        self.assertEqual(packet['selection']['skip_counts']['explicitly_excluded'], 3)
        self.change("UPDATE analyses SET published_at='2026-10-02T00:00:00Z' WHERE entry_id=1")
        self.change("UPDATE analyses SET published_at=NULL WHERE entry_id=12")
        self.assertEqual(self.export(limit=3)['entry_ids'], [1, 11, 10])

    def test_explicit_user_and_exclusion_validation(self):
        for value in (0, -1, True, '1', 1.0, None, 2):
            with self.subTest(user=value), self.assertRaisesRegex(ValueError, 'explicit_scope_user'):
                self.export(scope_user_id=value)
        self.config.pop('scope_user_id')
        with self.assertRaises(ValueError):
            self.export()
        self.config['scope_user_id'] = 1
        for values in (None, {}, '1', [True], [0], [-1], [1.0], ['1'], [1, 1], [2**63]):
            with self.subTest(exclusions=values), self.assertRaises(ValueError):
                self.export(exclude_entry_ids=values)

    def test_limit_bounds_and_honest_short_and_empty_batches(self):
        for value in (0, 13, True, 1.0, '3', None):
            with self.subTest(limit=value), self.assertRaisesRegex(ValueError, 'invalid_batch_limit'):
                self.export(limit=value)
        packet = self.export(exclude_entry_ids=list(range(2, 13)))
        self.assertEqual(packet['entry_ids'], [1])
        self.assertEqual(packet['selection']['status'], 'short')
        packet = self.export(exclude_entry_ids=list(range(1, 13)))
        self.assertEqual(packet['entry_ids'], [])
        self.assertEqual(packet['selection']['status'], 'empty')
        with self.assertRaisesRegex(ValueError, 'invalid_bounded_batch'):
            importer.identity(packet, {'schema': 'dot-url-results-v1', 'manifest_hash': packet['manifest_hash'],
                                      'producer': importer.PRODUCER, 'articles': []}, batch_limit=12)
        self.feeds = []
        packet = self.export()
        self.assertEqual(packet['selection']['skip_counts'], {'feed_not_current_enabled_owned': 12})

    def test_other_users_disabled_unowned_and_removed_feeds(self):
        self.change('UPDATE analyses SET user_id=2 WHERE entry_id=12')
        self.change('UPDATE analyses SET feed_id=2 WHERE entry_id=11')
        self.change('UPDATE analyses SET feed_id=3 WHERE entry_id=10')
        self.change('UPDATE analyses SET feed_id=4 WHERE entry_id=9')
        self.feeds += [{'id': 2, 'user_id': 1, 'disabled': True}, {'id': 3, 'user_id': 2, 'disabled': False}]
        packet = self.export()
        self.assertEqual(packet['enabled_feed_ids'], [1])
        self.assertEqual(packet['entry_ids'], list(range(8, 0, -1)))
        self.assertEqual(packet['selection']['scoped_row_count'], 11)
        self.assertEqual(packet['selection']['skip_counts'], {'feed_not_current_enabled_owned': 3})
        for feeds in (None, [{}], [{'id': 1, 'user_id': 1}], [{'id': True, 'user_id': 1, 'disabled': False}], self.feeds + [self.feeds[0]]):
            with self.subTest(feeds=feeds), self.assertRaisesRegex(ValueError, 'invalid_live_feed_catalog'):
                self.export(feed_reader=lambda _, catalog=feeds: catalog)

    def test_unsafe_urls_never_exported(self):
        bad = ['http://127.0.0.1/a', 'http://10.1.2.3/a', 'http://localhost/a', 'http://name.internal/a',
               'https://user:pass@example.com/a', 'https://example.com:8080/a', 'https://example.com/a?token=abc',
               'file:///tmp/a', 'https://example.com/a?%74oken=abc', 'http://127.1/a', 'http://0177.0.0.1/a',
               'http://0x7f.0.0.1/a', 'https://example.com\\@private/a', 'https://example.com/a\n',
               'https://[::1]/a', 'http://169.254.169.254/a', 'https://example..com/a', 'http://999.1.2.3/a',
               'https://example.com/a#access_token=secret', 'https://example.com/a?session=secret']
        for value in bad:
            with self.subTest(url=value):
                self.change('UPDATE analyses SET url=? WHERE entry_id=12', value)
                packet = self.export()
                self.assertNotIn(12, packet['entry_ids'])
                self.assertEqual(packet['selection']['skip_counts']['unsafe_url'], 1)

    def test_second_explicit_user_and_invalid_entry_identity(self):
        self.config['scope_user_id'] = 2
        self.feeds.append({'id': 2, 'user_id': 2, 'disabled': False})
        self.change('UPDATE analyses SET user_id=2,feed_id=2 WHERE entry_id=12')
        packet = self.export(scope_user_id=2)
        self.assertEqual(packet['scope_user_id'], 2)
        self.assertEqual(packet['entry_ids'], [12])
        self.assertEqual(packet['enabled_feed_ids'], [2])
        self.change('UPDATE analyses SET entry_id=0 WHERE entry_id=12')
        packet = self.export(scope_user_id=2)
        self.assertEqual(packet['entry_ids'], [])
        self.assertEqual(packet['selection']['skip_counts'], {'invalid_analysis_identity': 1})

    def test_all_peer_claims_and_live_lease_boundaries(self):
        self.claim(self.f.f.peer, 12)
        for name, eid in [('second', 11), ('third', 10), ('fourth', 9), ('fifth', 8)]:
            self.claim(self.add_lane(name), eid, 'submit_unknown')
        self.claim(self.f.f.peer, 6, 'imported')
        now = time.time()
        self.change('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)', 7, 'other', now + 10)
        self.change('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)', 5, 'other', now)
        with mock.patch.object(exporter.time, 'time', return_value=now):
            packet = self.export()
        self.assertEqual(packet['entry_ids'], [6, 5, 4, 3, 2, 1])
        self.assertEqual(packet['selection']['skip_counts'], {'lane_claimed': 5, 'prepare_lease_live': 1})

    def test_old_manifest_claims_and_second_lane_scan(self):
        root = self.f.f.peer
        manifest = {'items': [{'id': 'legacy-item', 'source_refs': [{'entry_id': 12}]}]}
        fingerprint = exporter.digest(manifest)
        with sqlite3.connect(root / 'batches.sqlite3') as db:
            db.execute('INSERT INTO batches VALUES (?,?,?,NULL,NULL,0)', ('old', fingerprint, 'running'))
        (root / 'old').mkdir()
        (root / 'old/manifest.json').write_text(json.dumps({**manifest, 'batch_id': 'old', 'manifest_hash': fingerprint}))
        original = exporter.lane_claims
        calls = []
        def changed(config):
            calls.append(1)
            if len(calls) == 2:
                self.claim(root, 11)
            return original(config)
        with mock.patch.object(exporter, 'lane_claims', side_effect=changed):
            packet = self.export()
        self.assertEqual(len(calls), 2)
        self.assertEqual(packet['entry_ids'], list(range(10, 0, -1)))

    def test_missing_or_corrupt_lane_state_and_missing_lease_table_fail_closed(self):
        self.config['peer_state_roots'].append(str(self.f.f.root / 'missing'))
        with self.assertRaises(sqlite3.OperationalError):
            self.export()
        self.assertFalse((self.f.f.root / 'missing').exists())
        self.config['peer_state_roots'].pop()
        with sqlite3.connect(self.f.f.peer / 'batches.sqlite3') as db:
            db.execute("INSERT INTO batch_claims VALUES ('orphan',12)")
        with self.assertRaisesRegex(ValueError, 'orphan_lane_claim'):
            self.export()
        with sqlite3.connect(self.f.f.peer / 'batches.sqlite3') as db:
            db.execute('DELETE FROM batch_claims')
            db.execute("INSERT INTO batches VALUES ('../escape','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','running',NULL,NULL,0)")
        with self.assertRaisesRegex(ValueError, 'invalid_lane_state'):
            self.export()
        with sqlite3.connect(self.f.f.peer / 'batches.sqlite3') as db:
            db.execute('DELETE FROM batches')
            db.execute("INSERT INTO batches VALUES ('incomplete','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','running',NULL,NULL,0)")
        with self.assertRaises(FileNotFoundError):
            self.export()
        with sqlite3.connect(self.f.f.peer / 'batches.sqlite3') as db:
            db.execute('DELETE FROM batches')
        self.change('DROP TABLE kaggle_prepare_leases')
        with self.assertRaises(sqlite3.OperationalError):
            self.export()

    def test_workers_must_already_be_disabled_and_lock_must_exist(self):
        for key in ('enabled', 'translation_enabled'):
            for value in (True, 0, None):
                self.change('UPDATE settings SET value=?', json.dumps(dict(self.f.f.settings, **{key: value})))
                with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'existing_paid_workers_enabled'):
                    self.export()
        self.change('UPDATE settings SET value=?', json.dumps(self.f.f.settings))
        (self.f.f.coord / 'bridge.lock').unlink()
        with self.assertRaises(FileNotFoundError):
            self.export()
        self.assertFalse((self.f.f.coord / 'bridge.lock').exists())

    def test_analysis_retry_boundaries_and_null_only_body(self):
        now = time.time()
        self.change('UPDATE analyses SET attempts=2,next_try=?,updated_at=? WHERE entry_id=12', now, now)
        with mock.patch.object(exporter.time, 'time', return_value=now):
            self.assertIn(12, self.export()['entry_ids'])
            mutations = [('attempts', 3), ('attempts', -1), ('attempts', None), ('next_try', now + 0.01),
                         ('next_try', None), ('updated_at', now + 0.01), ('updated_at', None), ('state', 'done'),
                         ('source_text', ''), ('content_hash', ''), ('content_source', ''), ('source_chars', 0),
                         ('extracted_at', 0), ('analyzed_at', 0), ('truncated', 1), ('truncated', None)]
            for key, value in mutations:
                self.change(f'UPDATE analyses SET {key}=? WHERE entry_id=12', value)
                with self.subTest(key=key, value=value):
                    self.assertNotIn(12, self.export()['entry_ids'])
                original = {'attempts': 2, 'next_try': now, 'updated_at': now, 'state': 'pending', 'truncated': 0}.get(key)
                self.change(f'UPDATE analyses SET {key}=? WHERE entry_id=12', original)

    def test_ineligible_cards_are_omitted_and_preserved(self):
        for field, value, reason in [('attempts', 3, 'card_attempts_ineligible'), ('next_try', time.time()+600, 'card_retry_not_due'),
                                     ('updated_at', None, 'card_updated_after_cutoff'), ('status', 'done', 'card_state_ineligible')]:
            original = self.f.f.query(f'SELECT {field} FROM card_translations WHERE entry_id=2')[0][0]
            self.change(f'UPDATE card_translations SET {field}=? WHERE entry_id=2', value)
            packet = self.export()
            self.assertIsNone(next(a for a in packet['articles'] if a['entry_id'] == 2)['card'])
            self.assertEqual(packet['selection']['omitted_card_counts'][reason], 1)
            self.change(f'UPDATE card_translations SET {field}=? WHERE entry_id=2', original)
        self.change("INSERT INTO card_translation_versions(entry_id,user_id,source_hash) VALUES (2,1,'preserve-source-hash')")
        self.assertEqual(self.export()['selection']['omitted_card_counts']['card_version_preserved'], 1)

    def test_importer_rejects_changed_analysis_card_settings_claims_leases_and_feed(self):
        for target in ('analysis', 'card', 'settings', 'claim', 'lease', 'feed'):
            with self.subTest(target=target):
                packet = self.export()
                self.bind_results(packet)
                if target == 'analysis':
                    self.change("UPDATE analyses SET title='changed' WHERE entry_id=2")
                elif target == 'card':
                    self.change("UPDATE card_translations SET updated_at=0 WHERE entry_id=2")
                elif target == 'settings':
                    self.change('UPDATE settings SET value=?', json.dumps(dict(self.f.f.settings, prompt='changed')))
                elif target == 'claim':
                    self.claim(self.f.f.peer, 2)
                elif target == 'lease':
                    self.change('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)', 2, 'other', time.time()+660)
                else:
                    self.f.upstream[2]['feed_enabled'] = False
                before = self.db.read_bytes()
                with self.assertRaises(ValueError):
                    self.f.run_import()
                self.assertEqual(before, self.db.read_bytes())
                # Each case starts from a fresh synthetic contract fixture.
                self.f.tearDown()
                self.f = fixture.URLImportTests()
                self.f.setUp()
                self.config, self.db = self.f.config, self.f.db

    def test_cli_required_inputs_and_sanitized_failures(self):
        root = self.f.f.root
        (root / 'config.json').write_text(json.dumps(self.config))
        (root / 'exclude.json').write_text('[12,11]')
        argv = ['--config', str(root / 'config.json'), '--scope-user-id', '1', '--limit', '3',
                '--exclude-entry-ids-file', str(root / 'exclude.json')]
        actual = exporter.export
        def run(config, **kw):
            return actual(config, feed_reader=lambda _: self.feeds, **kw)
        output = io.StringIO()
        with mock.patch.object(exporter, 'export', side_effect=run), mock.patch('sys.stdout', output):
            self.assertEqual(exporter.main(argv), 0)
        self.assertEqual(json.loads(output.getvalue())['entry_ids'], [10, 9, 8])
        output = io.StringIO()
        with mock.patch.object(exporter, 'export', side_effect=RuntimeError('SECRET_SENTINEL')), mock.patch('sys.stdout', output):
            self.assertEqual(exporter.main(argv), 1)
        self.assertEqual(json.loads(output.getvalue()), {'read_only': True, 'state': 'blocked', 'error': 'RuntimeError'})
        for failure, expected in [(ValueError('secret_sentinel'), 'ValueError'),
                                  (ValueError('invalid_batch_limit'), 'invalid_batch_limit')]:
            output = io.StringIO()
            with mock.patch.object(exporter, 'export', side_effect=failure), mock.patch('sys.stdout', output):
                self.assertEqual(exporter.main(argv), 1)
            self.assertEqual(json.loads(output.getvalue())['error'], expected)


if __name__ == '__main__':
    unittest.main()
