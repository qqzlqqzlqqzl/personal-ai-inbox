"""Offline bounded tests: no model credentials, browser, Miniflux or pytest needed."""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import core
import bilingual_translation as bilingual


class RollingBodyVersionTests(unittest.TestCase):
    """Exercise the real candidate SQL without optional body parsers or disk I/O."""

    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        connection = patch.object(core, 'connect', self.connection)
        connection.start()
        self.addCleanup(connection.stop)
        environment = patch.dict('os.environ', {'BILINGUAL_MODEL': 'offline-model'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.db.executescript('''
          CREATE TABLE analyses(entry_id INTEGER PRIMARY KEY,user_id INTEGER,url TEXT,
            feed_id INTEGER,content_hash TEXT,content_quality TEXT,analyzed_at REAL,
            updated_at REAL,published_at TEXT,state TEXT,score REAL);
          CREATE TABLE prepared_articles(entry_id INTEGER PRIMARY KEY,user_id INTEGER,
            url TEXT,kind TEXT,prepared_at REAL,content TEXT);
        ''')
        bilingual.migrate()
        self.now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        self.old = '<p>The initial source explanation.</p>'
        self.full = '<p>The verified original with restored explanations.</p>'
        self.cfg = bilingual.config()
        self.db.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (7, 3, 'https://source.invalid/article', 1, core.hash_text(self.old), None,
             self.now-10, self.now-10, '2026-10-08T00:00:00Z', 'done', 8))
        digest = bilingual.source_hash(self.old, self.cfg['model'])
        self.db.execute('INSERT INTO bilingual_articles VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (3, 7, digest, self.old, self.cfg['model'], 'done', 10,
             '2026-10-08T00:00:00Z', self.now-10, bilingual._version(), self.now-30))
        self.db.execute('INSERT INTO bilingual_current VALUES(?,?,?)', (3, 7, digest))
        self.db.execute('INSERT INTO prepared_articles VALUES(?,?,?,?,?,?)',
            (7, 3, 'https://source.invalid/article', 'reader_original_html', self.now-20, self.full))

    @contextmanager
    def connection(self):
        yield self.db

    def selected(self, state=None):
        candidates, _, _ = bilingual._rolling_candidates(3, self.now, state or {}, self.cfg)
        return [row['entry_id'] for _, row in candidates]

    def test_restored_body_before_old_completion_is_discovered_in_fresh_and_backlog(self):
        for state in ({}, {'since': self.now+1}):
            with self.subTest(state=state):
                self.assertEqual(self.selected(state), [7])

    def test_older_prepared_body_cannot_replace_newer_native_admission(self):
        self.db.execute('UPDATE bilingual_articles SET requested_at=?', (self.now-15,))
        self.assertEqual(self.selected(), [])

    def test_matching_body_never_reenters_rolling(self):
        self.db.execute('UPDATE prepared_articles SET content=?,prepared_at=?', (self.old, self.now-5))
        self.assertEqual(self.selected(), [])

    def test_legacy_zero_admission_keeps_the_completion_time_guard(self):
        self.db.execute('UPDATE bilingual_articles SET requested_at=0')
        self.assertEqual(self.selected(), [])
        self.db.execute('UPDATE prepared_articles SET prepared_at=?', (self.now-5,))
        self.assertEqual(self.selected(), [7])

    def test_restoration_must_still_match_owner_entry_url_and_kind(self):
        for field, value in (('user_id', 4), ('entry_id', 8), ('url', 'https://other.invalid/'),
                             ('kind', 'body_images_repaired')):
            with self.subTest(field=field):
                self.db.execute('SAVEPOINT mismatch')
                self.db.execute('UPDATE prepared_articles SET '+field+'=?', (value,))
                self.assertEqual(self.selected(), [])
                self.db.execute('ROLLBACK TO mismatch')
                self.db.execute('RELEASE mismatch')


class BilingualTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(core, 'DB', Path(self.temp.name) / 'test.sqlite3')
        self.env_patch = patch.dict('os.environ', {
            'BILINGUAL_ENABLED': 'true', 'BILINGUAL_API_BASE_URL': 'https://model.invalid/v1',
            'BILINGUAL_API_KEY': 'offline-test-key', 'BILINGUAL_MODEL': 'gpt-4o-mini',
        }, clear=True)
        self.db_patch.start()
        self.env_patch.start()
        core.init_db()
        core.init_usage()
        core.migrate()
        bilingual.migrate()
        self.entry = {'id': 7, 'user_id': 3, 'title': 'Article', 'url': 'https://source.invalid/article',
                      'published_at': '2026-10-06T10:00:00Z', 'language': 'en',
                      'content': '<h2>A heading</h2><p>An original paragraph.</p>'}
        self.calls = []
        self.allow(self.entry)

    def allow(self, entry, *, score=8, state='done', user_id=None, quality=None):
        from content_quality import serialized
        row = {'entry_id': entry['id'], 'user_id': entry['user_id'] if user_id is None else user_id,
               'url': entry['url'], 'content_hash': core.hash_text(entry['content']),
               'source_text': entry['content']}
        with core.connect() as db:
            db.execute('''INSERT OR REPLACE INTO analyses
              (entry_id,user_id,url,state,score,content_hash,source_text,content_quality)
              VALUES (?,?,?,?,?,?,?,?)''',
              (row['entry_id'], row['user_id'], row['url'], state, score, row['content_hash'],
               row['source_text'], serialized(quality, row) if quality else None))

    def tearDown(self):
        self.env_patch.stop()
        self.db_patch.stop()
        self.temp.cleanup()

    def reply(self, request, *, omitted=(), finish='stop', transform=None):
        self.calls.append(request)
        body = json.loads(request.content)
        self.assertEqual(body['model'], 'gpt-4o-mini')
        self.assertEqual(str(request.url), 'https://model.invalid/v1/chat/completions')
        items = json.loads(body['messages'][1]['content'])['items']
        output = []
        for item in items:
            if item['id'] in omitted:
                continue
            text = '忠实的中文段落内容。' + '中文' * (len(item['text']) // 5)
            text += ''.join(bilingual.TOKEN.findall(item['text']))
            output.append({'id': item['id'], 'text': transform(text) if transform else text})
        return httpx.Response(200, json={'usage': {'total_tokens': 77}, 'choices': [
            {'finish_reason': finish, 'message': {'content': json.dumps({'items': output}, ensure_ascii=False)}}]})

    async def run_mock(self, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: self.reply(request, **kwargs))) as client:
            return await bilingual.run_once(client)

    def attached(self, entry=None, user=3):
        return bilingual.attach(entry or self.entry, user)['translation']

    def ready_retry(self):
        with core.connect() as db:
            db.execute('UPDATE bilingual_blocks SET next_try=0')

    def rolling_entry(self, entry_id, now, *, age=60, language='en', score=8, state='done', hidden=False):
        feed = {'id': 2 if hidden else 1, 'user_id': 3, 'feed_url': 'https://source.invalid/feed',
                'hide_globally': hidden, 'category': {'id': 1, 'user_id': 3, 'hide_globally': False}}
        entry = {**self.entry, 'id': entry_id, 'url': f'https://source.invalid/article-{entry_id}',
                 'feed_id': feed['id'], 'feed': feed, 'language': language,
                 'published_at': datetime.fromtimestamp(now-age, timezone.utc).isoformat()}
        if language == 'zh':
            entry['content'] = '<p>这是一篇中文文章，不需要再次翻译。</p>'
        self.allow(entry, score=score, state=state)
        with core.connect() as db:
            db.execute('UPDATE analyses SET feed_id=?,published_at=?,analyzed_at=? WHERE entry_id=?',
                       (feed['id'], entry['published_at'], now-10, entry_id))
        return entry

    def rolling_client(self, entries, calls, transform=None):
        feeds = {entry['feed']['id']: entry['feed'] for entry in entries}
        by_id = {entry['id']: entry for entry in entries}
        caller = threading.get_ident()

        def response(request):
            self.assertNotEqual(threading.get_ident(), caller)
            self.assertEqual(request.method, 'GET')
            self.assertEqual((request.url.scheme, request.url.host, request.url.port), ('http', '127.0.0.1', 8091))
            self.assertEqual(request.headers['X-Auth-Token'], 'offline-reader-token')
            calls.append(request.url.path)
            if request.url.path.endswith('/me'):
                return httpx.Response(200, json={'id': 3})
            if request.url.path.endswith('/feeds'):
                return httpx.Response(200, json=list(feeds.values()))
            entry = by_id[int(request.url.path.rsplit('/', 1)[-1])]
            return httpx.Response(200, json=transform(entry) if transform else entry)

        return httpx.Client(transport=httpx.MockTransport(response))

    async def test_rolling_is_opt_in_and_heartbeat_has_only_counts(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: self.fail('Disabled discovery made a request'))) as client:
            result = await bilingual.discover_recent(client, now=1791417600)
        self.assertFalse(bilingual.config()['rolling_enabled'])
        self.assertEqual(result['status'], 'disabled')
        self.assertEqual(core.get_meta('bilingual_rolling_heartbeat'), result)
        self.assertEqual(set(result), {'at', 'status', 'scanned', 'fetched', 'enqueued', 'skipped', 'failed', 'body_unverified'})

    async def test_rolling_global_visible_english_excludes_future_and_deduplicates_skips(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entries = [self.rolling_entry(101, now, age=100), self.rolling_entry(102, now, age=90, language='zh'),
                   self.rolling_entry(103, now, age=80, language='fr'), self.rolling_entry(104, now, age=70, hidden=True),
                   self.rolling_entry(105, now, age=8*86400), self.rolling_entry(106, now, score=7),
                   self.rolling_entry(107, now, state='pending'), self.rolling_entry(108, now, age=50),
                   self.rolling_entry(109, now, age=-86400)]
        calls, state = [], {}
        transform = lambda entry: {**entry, 'url': 'https://wrong.invalid/source'} if entry['id'] == 108 else entry
        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}):
            with self.rolling_client(entries, calls, transform) as client:
                result = await bilingual.discover_recent(client, state=state, now=now)
                self.assertEqual((result['enqueued'], result['failed']), (2, 1))
                self.assertEqual([path for path in calls if '/entries/' in path],
                                 ['/mf/v1/entries/108', '/mf/v1/entries/103', '/mf/v1/entries/102',
                                  '/mf/v1/entries/101', '/mf/v1/entries/105'])
                calls.clear()
                again = await bilingual.discover_recent(client, state=state, now=now+300)
                self.assertEqual(again['fetched'], 0)
                self.assertFalse(any('/entries/' in path for path in calls))
        with core.connect() as db:
            self.assertEqual([row[0] for row in db.execute('SELECT entry_id FROM bilingual_articles ORDER BY entry_id')], [101, 105])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_usage').fetchone()[0], 0)
        self.assertEqual(self.calls, [])

    async def test_rolling_late_analysis_is_not_lost_behind_cursor_and_body_bound(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entries = [self.rolling_entry(111, now, age=2*86400), self.rolling_entry(112, now, age=86400)]
        with core.connect() as db:
            cursor = db.execute("SELECT julianday(?,'unixepoch')", (now-3*86400,)).fetchone()[0]
        state, calls = {'cursor': (cursor, 999), 'since': now-300}, []
        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}), \
                patch.object(bilingual, 'ROLLING_BODIES', 1):
            with self.rolling_client(entries, calls) as client:
                first = await bilingual.discover_recent(client, state=state, now=now)
                self.assertEqual((first['enqueued'], first['status']), (1, 'bounded'))
                second = await bilingual.discover_recent(client, state=state, now=now+300)
                self.assertEqual(second['enqueued'], 1)
        self.assertEqual([path for path in calls if '/entries/' in path], ['/mf/v1/entries/112', '/mf/v1/entries/111'])

    async def test_rolling_current_done_skips_body_but_failed_changed_source_is_discovered(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        done = self.rolling_entry(121, now)
        failed = self.rolling_entry(122, now)
        for entry in (done, failed):
            bilingual.enqueue(entry)
        old_hash = bilingual.source_hash(failed['content'])
        with core.connect() as db:
            db.execute("UPDATE bilingual_articles SET status='done' WHERE entry_id=121")
            db.execute("UPDATE bilingual_blocks SET translated='已有缓存译文' WHERE entry_id=121")
            db.execute("UPDATE bilingual_articles SET status='error' WHERE entry_id=122")
            db.execute('UPDATE bilingual_blocks SET attempts=? WHERE entry_id=122', (bilingual.MAX_ATTEMPTS,))
        failed['content'] = '<p>This is a changed source with a new explanation.</p>'
        core.update(122, content_hash=core.hash_text(failed['content']), source_text=failed['content'])
        calls = []
        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}):
            with self.rolling_client([done, failed], calls) as client:
                result = await bilingual.discover_recent(client, now=now)
        self.assertEqual(result['enqueued'], 1)
        self.assertEqual([path for path in calls if '/entries/' in path], ['/mf/v1/entries/122'])
        with core.connect() as db:
            current = db.execute('SELECT source_hash FROM bilingual_current WHERE entry_id=122').fetchone()[0]
            self.assertNotEqual(current, old_hash)
            self.assertEqual(current, bilingual.source_hash(failed['content']))
            self.assertEqual(db.execute('SELECT MAX(attempts) FROM bilingual_blocks WHERE source_hash=?', (current,)).fetchone()[0], 0)

    async def test_rolling_restored_original_replaces_completed_old_body_once(self):
        from prepared_content import remember
        from content_quality import BODY_POLICY
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entry = self.rolling_entry(123, now)
        bilingual.enqueue(entry)
        old_hash = bilingual.source_hash(entry['content'])
        with core.connect() as db:
            db.execute("UPDATE bilingual_articles SET status='done',updated_at=? WHERE entry_id=123", (now-10,))
            db.execute("UPDATE bilingual_blocks SET translated='旧正文的缓存译文' WHERE entry_id=123")
        full = '<article><p>This is the verified complete original with newly restored details and explanations.</p></article>'
        remember(entry, full, 'reader_original_html', {
            'body_policy': BODY_POLICY, 'requested_url': entry['url'],
            'html_sha256': core.hash_text(full), 'checked_at': now,
        })
        calls = []
        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}):
            with self.rolling_client([entry], calls) as client:
                self.assertEqual((await bilingual.discover_recent(client, now=now))['enqueued'], 1)
                self.assertEqual((await bilingual.discover_recent(client, now=now+300))['enqueued'], 0)
        with core.connect() as db:
            new_hash = db.execute('SELECT source_hash FROM bilingual_current WHERE entry_id=123').fetchone()[0]
            self.assertEqual(new_hash, bilingual.source_hash(full))
            self.assertNotEqual(new_hash, old_hash)
            self.assertEqual(db.execute('SELECT status FROM bilingual_articles WHERE source_hash=?', (old_hash,)).fetchone()[0], 'done')
        self.assertEqual(self.calls, [])

    async def test_rolling_restoration_during_old_translation_survives_late_completion(self):
        from prepared_content import remember
        from content_quality import BODY_POLICY
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entry = self.rolling_entry(124, now)
        full = '<article><p>This verified original restores the full explanation and supporting details.</p></article>'
        old_hash = bilingual.source_hash(entry['content'])
        started, release = asyncio.Event(), asyncio.Event()
        clock = [now-30]

        async def respond(request):
            started.set()
            await release.wait()
            return self.reply(request)

        with patch.object(bilingual.time, 'time', side_effect=lambda: clock[0]):
            bilingual.enqueue(entry)
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                work = asyncio.create_task(bilingual.run_once(client))
                try:
                    await asyncio.wait_for(started.wait(), timeout=2)
                    clock[0] = now-20
                    remember(entry, full, 'reader_original_html', {
                        'body_policy': BODY_POLICY, 'requested_url': entry['url'],
                        'html_sha256': core.hash_text(full), 'checked_at': clock[0],
                    })
                    clock[0] = now-10
                finally:
                    release.set()
                    await asyncio.wait_for(work, timeout=2)

        calls = []
        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}):
            with self.rolling_client([entry], calls) as client:
                self.assertEqual((await bilingual.discover_recent(client, now=now))['enqueued'], 1)
                reads = len(calls)
                self.assertEqual((await bilingual.discover_recent(client, now=now+300))['enqueued'], 0)
                self.assertFalse(any('/entries/' in path for path in calls[reads:]))
        with core.connect() as db:
            current = db.execute('SELECT source_hash FROM bilingual_current WHERE entry_id=124').fetchone()[0]
            self.assertEqual(current, bilingual.source_hash(full))
            self.assertNotEqual(current, old_hash)
            self.assertEqual(db.execute('SELECT status FROM bilingual_articles WHERE source_hash=?', (old_hash,)).fetchone()[0], 'done')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_usage').fetchone()[0], 1)
        self.assertEqual(len(self.calls), 1)  # Only the old in-flight mock request.

    async def test_rolling_analysis_change_after_decoration_cannot_enqueue(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entry = self.rolling_entry(131, now)
        decorate = core.decorate

        def changed(*args, **kwargs):
            result = decorate(*args, **kwargs)
            core.update(131, content_hash='changed-during-preparation')
            return result

        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}), \
                patch.object(core, 'decorate', side_effect=changed):
            with self.rolling_client([entry], []) as client:
                result = await bilingual.discover_recent(client, now=now)
        self.assertEqual(result['enqueued'], 0)
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_articles').fetchone()[0], 0)

    async def test_rolling_uses_decorated_full_body_instead_of_native_teaser(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entry = self.rolling_entry(132, now)
        full = '<article><p>This is the captured full explanation with all original details.</p></article>'
        decorate = core.decorate

        def prepared(item, user_id, *, include_source_fallback):
            self.assertTrue(include_source_fallback)
            return {**decorate(item, user_id, include_source_fallback=include_source_fallback), 'content': full}

        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}), \
                patch.object(core, 'decorate', side_effect=prepared):
            with self.rolling_client([entry], []) as client:
                result = await bilingual.discover_recent(client, now=now)
        self.assertEqual(result['enqueued'], 1)
        with core.connect() as db:
            row = db.execute('SELECT source_html,source_hash FROM bilingual_articles').fetchone()
            self.assertEqual(tuple(row), (full, bilingual.source_hash(full)))

    async def test_rolling_cancel_joins_reader_thread_before_any_enqueue(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entry = self.rolling_entry(141, now)
        entered, release = threading.Event(), threading.Event()

        def blocked(item):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Cancellation watchdog')
            return item

        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}):
            with self.rolling_client([entry], [], blocked) as client:
                work = asyncio.create_task(bilingual.discover_recent(client, now=now))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    work.cancel()
                    await asyncio.sleep(0)
                finally:
                    release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await work
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_articles').fetchone()[0], 0)

    async def test_rolling_preparation_bridge_uses_loop_and_verified_reread(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entry = self.rolling_entry(151, now)
        owner = threading.get_ident()
        prepared_html = '<p>This is the complete verified original explanation.</p>'
        calls = []

        async def prepare(native, current_entry, *, admission):
            self.assertEqual(threading.get_ident(), owner)
            self.assertEqual(await current_entry(), native)
            admission()
            return {**native, 'content': prepared_html, 'prepared_source': 'reader_original_html'}

        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}):
            with self.rolling_client([entry], calls) as client:
                result = await bilingual.discover_recent(client, now=now, prepare=prepare)
        self.assertEqual(result['enqueued'], 1)
        self.assertEqual(sum('/entries/' in path for path in calls), 2)
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT source_html FROM bilingual_articles').fetchone()[0], prepared_html)

    async def test_rolling_unverified_fallback_skips_translation_and_short_term_recheck(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entry = self.rolling_entry(152, now)
        state, prepared = {}, []

        async def prepare(native, current_entry, *, admission):
            prepared.append(native['id'])
            admission()
            return {**native, 'prepared_source': 'analysis_source_fallback'}

        with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}):
            with self.rolling_client([entry], []) as client:
                result = await bilingual.discover_recent(client, now=now, state=state, prepare=prepare)
                self.assertEqual((result['enqueued'], result['body_unverified']), (0, 1))
                again = await bilingual.discover_recent(client, now=now+300, state=state, prepare=prepare)
                self.assertEqual(again['fetched'], 0)
        self.assertEqual(prepared, [152])
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_articles').fetchone()[0], 0)

    async def test_rolling_preparation_timeout_or_cancel_stops_late_enqueue(self):
        now = datetime(2026, 10, 8, 12, tzinfo=timezone.utc).timestamp()
        entry = self.rolling_entry(153, now)
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                entered, cancelled = asyncio.Event(), asyncio.Event()

                async def prepare(native, current_entry, *, admission):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()

                with patch.dict('os.environ', {'BILINGUAL_ROLLING_ENABLED': 'true', 'MINIFLUX_API_KEY': 'offline-reader-token'}), \
                        patch.object(bilingual, 'ROLLING_SECONDS', 2 if cancel else 0.1):
                    with self.rolling_client([entry], []) as client:
                        work = asyncio.create_task(bilingual.discover_recent(client, now=now, prepare=prepare))
                        await asyncio.wait_for(entered.wait(), 1)
                        if cancel:
                            work.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await work
                        else:
                            self.assertEqual((await work)['status'], 'bounded')
                        await asyncio.wait_for(cancelled.wait(), 1)
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_articles').fetchone()[0], 0)

    async def test_cached_read_deduplicates_and_keeps_original(self):
        original = dict(self.entry)
        self.assertTrue(bilingual.enqueue(self.entry, priority=100))
        self.assertEqual((await self.run_mock())['processed'], 2)
        self.assertFalse(bilingual.enqueue(self.entry, priority=100))
        self.assertEqual((await self.run_mock())['processed'], 0)
        self.assertEqual(len(self.calls), 1)
        value = self.attached()
        self.assertEqual(value['status'], 'done')
        self.assertEqual((value['blocks_total'], value['blocks_done']), (2, 2))
        self.assertEqual(bilingual.attach(self.entry, 3)['content'], original['content'])
        self.assertEqual(self.entry, original)
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT actual FROM bilingual_usage').fetchone()[0], 77)

    async def test_done_cache_renders_english_first_without_retranslation(self):
        self.entry['content'] += '<p>See <a id="docs" href="#intro">the documentation</a>' \
                                 '<img src="https://source.invalid/diagram.jpg"></p>'
        self.allow(self.entry)
        bilingual.enqueue(self.entry)
        await self.run_mock()
        with core.connect() as db:
            before = tuple(tuple(row) for row in db.execute('SELECT * FROM bilingual_articles'))
            usage = tuple(tuple(row) for row in db.execute('SELECT * FROM bilingual_usage'))
        value = self.attached()
        html = value['bilingual_html']
        self.assertLess(html.index('reader-translation-original'), html.index('reader-translation-target'))
        self.assertEqual(html.count('<img '), 1)
        self.assertEqual(html.count('id="docs"'), 1)
        self.assertIn('href="#intro"', html)
        self.assertNotIn('reader-translation-original', value['chinese_html'])
        self.assertEqual(value['chinese_html'].count('<img '), 1)
        self.assertEqual(value['source_hash'], bilingual.source_hash(self.entry['content']))
        self.assertEqual((await self.run_mock())['processed'], 0)
        with core.connect() as db:
            self.assertEqual(tuple(tuple(row) for row in db.execute('SELECT * FROM bilingual_articles')), before)
            self.assertEqual(tuple(tuple(row) for row in db.execute('SELECT * FROM bilingual_usage')), usage)
        self.assertEqual(len(self.calls), 1)

    async def test_image_address_rotation_reuses_done_without_new_rows_or_calls(self):
        self.entry['content'] += '<p><a href="https://source.invalid/old">An ordinary link.</a></p><p><a href="https://image.invalid/old"><img src="https://image.invalid/old" alt="Diagram"></a></p>'
        self.allow(self.entry)
        bilingual.enqueue(self.entry)
        await self.run_mock()
        rotated = {**self.entry, 'content': self.entry['content'].replace('https://image.invalid/old', 'https://image.invalid/new')}
        value = self.attached(rotated)
        self.assertEqual(value['status'], 'done')
        self.assertEqual(value['source_hash'], bilingual.source_hash(rotated['content']))
        self.assertIn('https://image.invalid/new', value['bilingual_html'])
        self.assertNotIn('https://image.invalid/old', value['bilingual_html'])
        self.assertFalse(bilingual.enqueue(rotated))
        self.assertEqual((await self.run_mock())['processed'], 0)
        self.assertEqual(len(self.calls), 1)
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_articles').fetchone()[0], 1)
        for changed in (
            rotated['content'].replace('original paragraph', 'changed paragraph'),
            rotated['content'].replace('<h2>', '<h3>').replace('</h2>', '</h3>'),
            rotated['content'].replace('Diagram', 'Changed diagram'),
            rotated['content'].replace('https://source.invalid/old', 'https://source.invalid/new'),
        ):
            self.assertIsNone(self.attached({**rotated, 'content': changed})['bilingual_html'])
        self.assertIsNone(self.attached(rotated, user=4)['bilingual_html'])
        with patch.dict('os.environ', {'BILINGUAL_MODEL': 'different-model'}):
            self.assertIsNone(self.attached(rotated)['bilingual_html'])

    async def test_user_and_exact_body_version_isolation_and_restore(self):
        bilingual.enqueue(self.entry)
        await self.run_mock()
        self.assertIsNone(self.attached(user=4)['bilingual_html'])
        changed = {**self.entry, 'content': '<p>A changed article paragraph.</p>'}
        self.assertIsNone(self.attached(changed)['bilingual_html'])
        bilingual.enqueue(changed)
        bilingual.enqueue(self.entry)
        self.assertEqual(self.attached()['status'], 'done')
        self.assertEqual((await self.run_mock())['processed'], 0)
        with patch.dict('os.environ', {'BILINGUAL_MODEL': 'different-model'}):
            self.assertIsNone(self.attached()['bilingual_html'])

    def test_cache_read_never_enqueues_and_list_has_no_html(self):
        self.assertEqual(self.attached()['status'], 'pending')
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_articles').fetchone()[0], 0)
        deferred = {**self.entry, 'content_deferred': True, 'content': ''}
        self.assertFalse(bilingual.enqueue(deferred))
        self.assertIsNone(self.attached(deferred)['bilingual_html'])

    async def test_partial_retains_untranslated_source_and_retries_only_missing(self):
        bilingual.enqueue(self.entry)
        result = await self.run_mock(omitted={1})
        self.assertEqual((result['processed'], result['failed']), (1, 1))
        value = self.attached()
        self.assertEqual(value['status'], 'partial')
        self.assertIn('An original paragraph.', value['chinese_html'])
        self.assertIn('reader-translation-target', value['chinese_html'])
        self.ready_retry()
        await self.run_mock()
        last = json.loads(json.loads(self.calls[-1].content)['messages'][1]['content'])['items']
        self.assertEqual([item['id'] for item in last], [1])
        self.assertEqual(self.attached()['status'], 'done')

    async def test_segment_recovery_keeps_successful_blocks_and_original_token_order(self):
        self.entry['content'] = ('<p>Already translated paragraph.</p>'
            '<p>Read <a href="https://source.invalid/one">the first report</a> and '
            '<a href="https://source.invalid/two">the second report</a>.</p>'
            '<p>Then <code>run()</code><br>read the final explanation.</p>')
        self.allow(self.entry)
        bilingual.enqueue(self.entry)
        with core.connect() as db:
            db.execute("UPDATE bilingual_blocks SET translated='保留的成功译文。' WHERE block_id=0")
            db.execute("UPDATE bilingual_blocks SET attempts=2,error='missing_or_invalid_blocks' WHERE block_id>0")
        result = await self.run_mock()
        self.assertEqual(result['processed'], 2)
        payload = json.loads(json.loads(self.calls[0].content)['messages'][1]['content'])['items']
        self.assertTrue(all(not bilingual.TOKEN.search(item['text']) for item in payload))
        self.assertEqual(len({item['id'] for item in payload}), len(payload))
        with core.connect() as db:
            rows = db.execute('SELECT * FROM bilingual_blocks ORDER BY block_id').fetchall()
            self.assertEqual((rows[0]['translated'], rows[0]['attempts']), ('保留的成功译文。', 0))
            for row in rows[1:]:
                self.assertEqual(row['attempts'], 3)
                self.assertEqual(bilingual.TOKEN.findall(row['translated']), bilingual.TOKEN.findall(row['source_text']))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_usage').fetchone()[0], 1)
        self.assertEqual(self.attached()['status'], 'done')

    async def test_recovery_missing_segment_exhausts_only_remaining_opportunity(self):
        self.entry['content'] = '<p>Read <a href="https://source.invalid/docs">the documentation</a> for details.</p>'
        self.allow(self.entry)
        bilingual.enqueue(self.entry)
        with core.connect() as db:
            db.execute("UPDATE bilingual_blocks SET attempts=3,error='missing_or_invalid_blocks'")
        result = await self.run_mock(omitted={0})
        self.assertEqual((result['processed'], result['status']), (0, 'error'))
        self.assertEqual((await self.run_mock())['processed'], 0)
        self.assertEqual(len(self.calls), 1)
        with core.connect() as db:
            row = db.execute('SELECT attempts,translated,error FROM bilingual_blocks').fetchone()
            self.assertEqual(tuple(row), (4, None, 'missing_or_invalid_blocks'))

    async def test_recovery_uses_existing_budget_before_request_or_attempt(self):
        bilingual.enqueue(self.entry)
        with core.connect() as db:
            db.execute("UPDATE bilingual_blocks SET attempts=2,error='missing_or_invalid_blocks'")
        with patch.dict('os.environ', {'BILINGUAL_DAILY_TOKENS': '1000'}):
            self.assertTrue((await self.run_mock())['budget_paused'])
        self.assertEqual(self.calls, [])
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT MAX(attempts) FROM bilingual_blocks').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_usage').fetchone()[0], 0)

    async def test_source_only_token_chunk_finishes_without_model_or_budget(self):
        self.entry['content'] = '<p><a href="https://source.invalid/docs">' + 'A' * 3494 + '</a></p>'
        self.allow(self.entry)
        bilingual.enqueue(self.entry)
        with core.connect() as db:
            db.execute("UPDATE bilingual_blocks SET translated=? WHERE block_id=0", ('[[t1]]' + '中文' * 700,))

        async def forbidden(request):
            self.fail('Formatting-only source must not request a model')

        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
            self.assertEqual((await bilingual.run_once(client))['processed'], 1)
        with core.connect() as db:
            row = db.execute('SELECT source_text,translated,attempts FROM bilingual_blocks WHERE block_id=1').fetchone()
            self.assertEqual(tuple(row), ('[[/t1]]', '[[/t1]]', 0))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_usage').fetchone()[0], 0)
        self.assertEqual(self.attached()['status'], 'done')

    async def test_truncated_output_is_not_cached_and_has_bounded_retry(self):
        bilingual.enqueue(self.entry)
        for _ in range(bilingual.MAX_ATTEMPTS):
            await self.run_mock(finish='length')
            self.ready_retry()
        self.assertIsNone(self.attached()['bilingual_html'])
        self.assertEqual(self.attached()['status'], 'error')
        self.assertEqual((await self.run_mock())['processed'], 0)
        self.assertEqual(len(self.calls), bilingual.MAX_ATTEMPTS)

    async def test_notconfigured_never_calls_model_but_can_read_finished_cache(self):
        bilingual.enqueue(self.entry)
        with patch.dict('os.environ', {'BILINGUAL_API_KEY': ''}):
            self.assertTrue((await self.run_mock())['notconfigured'])
            self.assertEqual(self.attached()['status'], 'notconfigured')
        self.assertEqual(self.calls, [])
        await self.run_mock()
        with patch.dict('os.environ', {'BILINGUAL_ENABLED': 'false'}):
            self.assertEqual(self.attached()['status'], 'done')
            self.assertIn('bilingual_html', self.attached())

    async def test_unconfigured_partial_is_terminal_but_preserves_cached_html(self):
        bilingual.enqueue(self.entry)
        await self.run_mock(omitted={1})
        with patch.dict('os.environ', {'BILINGUAL_API_KEY': ''}):
            value = self.attached()
            self.assertEqual(value['status'], 'notconfigured')
            self.assertIn('bilingual_html', value)

    async def test_independent_daily_budget_stops_a_new_request(self):
        bilingual.enqueue(self.entry)
        await self.run_mock()
        other = {**self.entry, 'id': 8}
        self.allow(other)
        bilingual.enqueue(other)
        with patch.dict('os.environ', {'BILINGUAL_DAILY_REQUESTS': '1'}):
            self.assertTrue((await self.run_mock())['budget_paused'])
        self.assertEqual(len(self.calls), 1)

    async def test_shared_token_budget_pause_resumes_after_utc_midnight(self):
        class Clock(datetime):
            current = datetime(2026, 10, 8, 23, 59, 59, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current if tz is not None else cls.current.replace(tzinfo=None)

        other = {**self.entry, 'id': 8}
        self.allow(other)
        bilingual.enqueue(self.entry, priority=100)
        bilingual.enqueue(other, priority=10)
        rows = bilingual._next_rows()
        payload = json.dumps({'items': [{'id': row['block_id'], 'text': row['source_text']} for row in rows]}, ensure_ascii=False)
        maximum = min(12000, max(1200, sum(len(row['source_text']) for row in rows) + 500))
        limit = len(payload.encode()) + len(bilingual.PROMPT.encode()) + maximum

        def response(request):
            reply = self.reply(request)
            body = reply.json()
            body['usage']['total_tokens'] = limit
            return httpx.Response(200, json=body)

        with patch.object(bilingual, 'datetime', Clock), patch.dict('os.environ', {
            'BILINGUAL_DAILY_TOKENS': str(limit), 'BILINGUAL_DAILY_REQUESTS': '1000',
            'BILINGUAL_CONCURRENCY': '3',
        }):
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                first = await bilingual.run_once(client)
                self.assertTrue(first['budget_paused'])
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.attached(other)['status'], 'budget_paused')
                self.assertTrue((await bilingual.run_once(client))['budget_paused'])
                self.assertEqual(len(self.calls), 1)
                Clock.current = datetime(2026, 10, 9, 0, 0, 0, tzinfo=timezone.utc)
                self.assertEqual((await bilingual.run_once(client))['processed'], 2)
                self.assertEqual(len(self.calls), 2)
        with core.connect() as db:
            self.assertEqual([tuple(row) for row in db.execute(
                'SELECT day,COUNT(*),SUM(actual) FROM bilingual_usage GROUP BY day ORDER BY day')],
                [('2026-10-08', 1, limit), ('2026-10-09', 1, limit)])
            self.assertEqual(db.execute('SELECT MAX(attempts) FROM bilingual_blocks').fetchone()[0], 1)

    async def test_exhausted_partial_and_budget_pause_are_terminal_with_html(self):
        bilingual.enqueue(self.entry)
        await self.run_mock(omitted={1})
        self.ready_retry()
        with patch.dict('os.environ', {'BILINGUAL_DAILY_REQUESTS': '1'}):
            self.assertTrue((await self.run_mock())['budget_paused'])
        self.assertEqual(self.attached()['status'], 'budget_paused')
        self.assertIn('bilingual_html', self.attached())
        for _ in range(bilingual.MAX_ATTEMPTS - 1):
            # Recovery numbers text fragments from zero; omit both namespaces
            # so this provider remains broken through the retry ceiling.
            await self.run_mock(omitted={0, 1})
            self.ready_retry()
        self.assertEqual(self.attached()['status'], 'error')
        self.assertIn('bilingual_html', self.attached())

    async def test_changed_prompt_never_bills_old_queued_version(self):
        bilingual.enqueue(self.entry)
        with patch.object(bilingual, 'PROMPT', bilingual.PROMPT + '\nA new instruction.'):
            self.assertEqual(bilingual._next_rows(), [])
            self.assertIsNone(self.attached()['bilingual_html'])
            self.assertEqual((await self.run_mock())['processed'], 0)

    async def test_concurrent_worker_cannot_double_bill(self):
        bilingual.enqueue(self.entry)
        started, release = asyncio.Event(), asyncio.Event()
        async def respond(request):
            started.set()
            await release.wait()
            return self.reply(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            first = asyncio.create_task(bilingual.run_once(client))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                second = await bilingual.run_once(client)
                self.assertTrue(second['busy'])
            finally:
                release.set()
                await asyncio.wait_for(first, timeout=2)
        self.assertEqual(len(self.calls), 1)

    def test_source_markup_sanitizes_active_attributes_and_keeps_responsive_images(self):
        html = ('<p onclick="bad()">Read <a href="javascript:bad()">unsafe link</a>'
                '<a href="https://[invalid">bad URL</a>'
                '<picture><source srcset="https://source.invalid/large.jpg 2x">'
                '<img src="https://source.invalid/small.jpg" onerror="bad()"></picture></p>'
                '<script>steal()</script>')
        root, _, _ = bilingual.extract(html)
        safe = bilingual._html(root)
        for forbidden in ('onclick', 'onerror', 'javascript:', '<script>', 'https://[invalid'):
            self.assertNotIn(forbidden, safe)
        self.assertIn('srcset="https://source.invalid/large.jpg 2x"', safe)

    async def test_structure_images_links_code_chinese_and_escaping(self):
        self.entry['content'] = ('<h2>Headline</h2><blockquote><p>Quoted words.</p></blockquote>'
            '<ul><li>Outer words<ul><li>Inner words</li></ul></li></ul>'
            '<table><tbody><tr><th>Column name</th><td>Cell words</td></tr></tbody></table>'
            '<figure><img src="https://source.invalid/img.jpg"><figcaption>Image caption</figcaption></figure>'
            '<p>Read <a href="https://source.invalid/docs">the docs</a> and <code>foo()</code>.'
            '<img src="https://source.invalid/inline.jpg"></p>'
            '<pre>Do not translate code block</pre><p>中文原段落不需要翻译。</p>')
        _, _, blocks = bilingual.extract(self.entry['content'])
        source = ' '.join(block['text'] for block in blocks)
        self.assertEqual(source.count('Quoted words'), 1)
        self.assertEqual(source.count('Outer words'), 1)
        self.assertEqual(source.count('Inner words'), 1)
        self.assertNotIn('foo()', source)
        self.assertNotIn('Do not translate', source)
        self.assertNotIn('中文原段落', source)
        bilingual.enqueue(self.entry)
        for _ in range(3):
            await self.run_mock(transform=lambda text: text + '<script>bad()</script>')
        value = self.attached()
        self.assertEqual(value['status'], 'done')
        for mode in ('bilingual_html', 'chinese_html'):
            html = value[mode]
            self.assertEqual(html.count('<img '), 2)
            self.assertNotIn('<script>', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertIn('href="https://source.invalid/docs"', html)
            self.assertIn('<pre>Do not translate code block</pre>', html)
            self.assertIn('<code>foo()</code>', html)
            first = 'original' if mode == 'bilingual_html' else 'target'
            self.assertIn('<td><span class="reader-translation-' + first + '"', html)
            self.assertIn('中文原段落不需要翻译。', html)

    def test_native_content_never_enters_model_queue(self):
        self.entry['content'] = '<p>原文是中文，不必重复翻译。</p><pre>print("hi")</pre>'
        bilingual.enqueue(self.entry)
        self.assertEqual(self.attached()['status'], 'native')
        self.assertEqual(bilingual._next_rows(), [])

    def test_internal_api_allows_loopback_http_but_rejects_external_cleartext(self):
        for base, expected in [('http://127.0.0.1:18767/v1', True),
                               ('http://external.invalid/v1', False),
                               ('https://key:secret@external.invalid/v1', False)]:
            with patch.dict('os.environ', {'BILINGUAL_API_BASE_URL': base}):
                self.assertEqual(bilingual.config()['ready'], expected)

    async def test_translated_body_keeps_anchor_targets_without_duplicate_inline_ids(self):
        self.entry['content'] = '<h2 id="intro">Introduction</h2><p>Read <a id="ref" href="#intro">this section</a>.</p>'
        bilingual.enqueue(self.entry)
        await self.run_mock()
        for mode in ('bilingual_html', 'chinese_html'):
            body = self.attached()[mode]
            self.assertEqual(body.count('id="intro"'), 1)
            self.assertLessEqual(body.count('id="ref"'), 1)
            self.assertIn('href="#intro"', body)

    def test_validation_rejects_bad_ids_markers_short_and_english(self):
        rows = [{'block_id': 1, 'source_text': 'Read [[t1]]the full documentation[[/t1]].'}]
        with self.assertRaises(ValueError):
            bilingual._validate('{"items":[{"id":8,"text":"中文"}]}', rows)
        for text in ('中文', 'Read [[t1]]all docs[[/t1]]', '中文[[/t1]][[t1]]'):
            self.assertEqual(bilingual._validate(json.dumps({'items': [{'id': 1, 'text': text}]}), rows), {})
        long = [{'block_id': 1, 'source_text': 'A long source paragraph. ' * 100}]
        self.assertEqual(bilingual._validate('{"items":[{"id":1,"text":"好"}]}', long), {})

    def test_accepts_documented_model_response_aliases(self):
        rows = [{'block_id': 1, 'source_text': 'Short words.'}]
        for key in ('items', 'blocks', 'translations'):
            output = json.dumps({key: [{'id': 1, 'text': '简短的文字。'}]})
            self.assertEqual(bilingual._validate(output, rows), {1: '简短的文字。'})

    def test_mini_closing_marker_typo_is_repaired_from_source_only(self):
        source = '[[t1]]Certificate validation:[[/t1]] Check the local clock.'
        output = '[[t1]]证书验证：[[t1]]检查本地时钟。'
        self.assertEqual(bilingual._validate(json.dumps({'items': [{'id': 1, 'text': output}]}),
            [{'block_id': 1, 'source_text': source}]),
            {1: '[[t1]]证书验证：[[/t1]]检查本地时钟。'})
        self.assertIsNone(bilingual._repair_tokens('[[t9]]内容[[/t9]]', source))
        self.assertIsNone(bilingual._repair_tokens('[[t1]]内容', source))

    def test_natural_sibling_link_order_keeps_source_nesting_and_all_markers(self):
        source = '[[t1]]Issue[[/t1]] by [[t3]]Author[[/t3]]'
        target = '由[[t3]]作者[[t3]]提交[[t1]]问题[[t1]]'
        self.assertEqual(bilingual._repair_tokens(target, source),
                         '由[[t3]]作者[[/t3]]提交[[t1]]问题[[/t1]]')
        nested = '[[t1]]bold [[t3]]link[[/t3]][[/t1]]'
        self.assertIsNone(bilingual._repair_tokens('[[t3]]链接[[/t3]][[t1]]加粗[[/t1]]', nested))

    async def test_technical_names_remain_verbatim_once_in_both_modes(self):
        for literal in ('EmbeddingGemma2', '@vasqu', 'Atmp', '$ chronyc authdata',
                        'key = HKDF-SHA256(master, salt = BE32(day))'):
            self.entry['content'] = '<p>' + literal + '</p>'
            bilingual.enqueue(self.entry)
            def response(request):
                items = json.loads(json.loads(request.content)['messages'][1]['content'])['items']
                return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'items': items})}}]})
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                await bilingual.run_once(client)
            value = self.attached()
            self.assertEqual(value['status'], 'done')
            for mode in ('bilingual_html', 'chinese_html'):
                self.assertEqual(value[mode].count(literal), 1)
        self.assertFalse(bilingual._technical_literal('This natural sentence must be translated.'))

    def test_long_runs_split_without_overlapping_text_or_tokens(self):
        html = '<p>Intro <strong>' + 'A paragraph sentence. ' * 500 + '</strong> end.</p>'
        _, _, blocks = bilingual.extract(html)
        self.assertGreater(len(blocks), 1)
        joined = ''.join(block['text'] for block in blocks)
        self.assertEqual(joined.count('A paragraph sentence.'), 500)
        self.assertEqual(len(bilingual.TOKEN.findall(joined)), 2)
        self.assertTrue(all(len(block['text']) <= bilingual.PART_CHARS for block in blocks))

    async def test_priority_and_admission(self):
        bilingual.enqueue(self.entry)
        other = {**self.entry, 'id': 9, 'content': '<p>Priority paragraph.</p>'}
        self.allow(other)
        bilingual.enqueue(other, priority=100)
        self.assertEqual(bilingual._next_rows()[0]['entry_id'], 9)
        def denied():
            raise bilingual.AdmissionStopped()
        with self.assertRaises(bilingual.AdmissionStopped):
            bilingual.enqueue({**self.entry, 'id': 10}, admission=denied)
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.reply)) as client:
            with self.assertRaises(bilingual.AdmissionStopped):
                await bilingual.run_once(client, admission=denied)
        self.assertEqual(self.calls, [])

    async def test_no_history_scan_or_source_get(self):
        for index in range(11, 20):
            self.allow({**self.entry, 'id': index}, score=9)
        def reject(request):
            self.fail('Reading analysed history must never fetch or call a model')
        with patch.dict('os.environ', {'MINIFLUX_API_KEY': 'offline-miniflux-key'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reject)) as client:
                result = await bilingual.run_once(client)
        self.assertEqual(result['processed'], 0)
        self.assertFalse(hasattr(bilingual, '_seed'))
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_articles').fetchone()[0], 0)

    async def test_legacy_pending_and_budget_paused_require_explicit_demand(self):
        for index, status in [(7, 'pending'), (8, 'budget_paused')]:
            entry = {**self.entry, 'id': index}
            self.allow(entry)
            bilingual.enqueue(entry)
            with core.connect() as db:
                db.execute('UPDATE bilingual_articles SET status=? WHERE entry_id=?', (status, index))
        with core.connect() as db:
            db.execute('ALTER TABLE bilingual_articles DROP COLUMN requested_at')
        bilingual.migrate()
        self.assertEqual((await self.run_mock())['processed'], 0)
        self.assertEqual(self.calls, [])
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT SUM(requested_at) FROM bilingual_articles').fetchone()[0], 0)
        self.assertTrue(bilingual.enqueue(self.entry))
        self.assertEqual((await self.run_mock())['processed'], 2)
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT requested_at FROM bilingual_articles WHERE entry_id=8').fetchone()[0], 0)

    async def test_migration_reads_old_done_without_retranslation_or_mutation(self):
        bilingual.enqueue(self.entry)
        await self.run_mock()
        with core.connect() as db:
            db.execute('ALTER TABLE bilingual_articles DROP COLUMN requested_at')
        bilingual.migrate()
        with core.connect() as db:
            before = tuple(db.execute('SELECT * FROM bilingual_articles').fetchone())
        self.assertEqual(self.attached()['status'], 'done')
        self.assertFalse(bilingual.enqueue(self.entry, priority=100))
        self.assertEqual((await self.run_mock())['processed'], 0)
        with core.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM bilingual_articles').fetchone()), before)
        self.assertEqual(len(self.calls), 1)

    async def test_explicit_request_checks_current_score_state_owner_and_quality(self):
        for score, state, owner in [(7.99, 'done', 3), (9, 'pending', 3), (9, 'done', 4)]:
            self.allow(self.entry, score=score, state=state, user_id=owner)
            self.assertFalse(bilingual.enqueue(self.entry))
        quality = {'policy_version': 'reader-content-quality-v1', 'recommendation_eligible': False,
                   'reason_codes': ['low_information'], 'access': 'public', 'information': 'low_information'}
        self.allow(self.entry, quality=quality)
        self.assertFalse(bilingual.enqueue(self.entry))
        self.assertEqual((await self.run_mock())['processed'], 0)
        self.assertEqual(self.attached()['status'], 'skipped')
        self.assertTrue(self.attached()['source_hash'])
        self.allow(self.entry, score=8)
        self.assertTrue(bilingual.enqueue(self.entry))
        self.assertEqual((await self.run_mock())['processed'], 2)

    def test_uncached_ineligible_attach_skips_language_detection_without_changing_fields(self):
        entry = {**self.entry, 'content': '<p>This is the current full article body.</p>' * 4500}
        quality = {'policy_version': 'reader-content-quality-v1', 'recommendation_eligible': False,
                   'reason_codes': ['low_information'], 'access': 'public', 'information': 'low_information'}
        cases = [{'score': 7.99}, {'state': 'pending'}, {'user_id': 4}, {'quality': quality}, None]
        for case in cases:
            with self.subTest(case=case):
                if case is None:
                    with core.connect() as db:
                        db.execute('DELETE FROM analyses WHERE entry_id=?', (entry['id'],))
                else:
                    self.allow(entry, **case)
                with patch.object(bilingual, '_source_language', side_effect=AssertionError('Ineligible body must not be parsed')):
                    attached = bilingual.attach(entry, 3)
                self.assertEqual(attached, {**entry, 'translation': {
                    'status': 'skipped', 'language': 'zh-CN', 'model': 'gpt-4o-mini',
                    'source_hash': bilingual.source_hash(entry['content']),
                    'blocks_total': 0, 'blocks_done': 0, 'updated_at': None,
                    'bilingual_html': None, 'chinese_html': None,
                }})
        with core.connect() as db:
            for table in ('bilingual_articles', 'bilingual_blocks', 'bilingual_current', 'bilingual_usage'):
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0)
        self.assertEqual(self.calls, [])

    def test_uncached_eligible_attach_still_classifies_current_language(self):
        cases = [('en', '<p>This is the current English article.</p>', 'pending'),
                 ('zh', '<p>这是一篇完整的中文原文，不需要重复翻译。</p>', 'native'),
                 ('fr', '<p>Un article technique en français.</p>', 'skipped')]
        for language, content, expected in cases:
            with self.subTest(language=language):
                entry = {**self.entry, 'language': language, 'content': content}
                self.allow(entry)
                with patch.object(bilingual, '_source_language', wraps=bilingual._source_language) as detect:
                    value = self.attached(entry)
                detect.assert_called_once_with(entry)
                self.assertEqual(value['status'], expected)
                self.assertEqual(value['source_hash'], bilingual.source_hash(content))
        self.assertEqual(self.calls, [])

    async def test_ineligible_current_analysis_preserves_existing_done_translation(self):
        bilingual.enqueue(self.entry)
        await self.run_mock()
        expected = self.attached()
        self.allow(self.entry, score=7)
        with patch.object(bilingual, '_source_language', side_effect=AssertionError('Existing cache must retain its read path')):
            self.assertEqual(self.attached(), expected)
        self.assertEqual(expected['status'], 'done')
        self.assertEqual(len(self.calls), 1)

    async def test_worker_rechecks_score_and_quality_before_spending(self):
        bilingual.enqueue(self.entry)
        self.allow(self.entry, score=7)
        self.assertEqual((await self.run_mock())['processed'], 0)
        self.allow(self.entry, score=8)
        rows = bilingual._next_rows()
        self.allow(self.entry, state='pending')
        self.assertIs(bilingual._reserve(rows, '{}', bilingual.config(), 1200, None), False)
        self.assertEqual(self.calls, [])
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_usage').fetchone()[0], 0)

    async def test_atomic_reservation_rejects_duplicate_done_and_stale_hash(self):
        bilingual.enqueue(self.entry)
        rows = bilingual._next_rows()
        first = bilingual._reserve(rows, '{}', bilingual.config(), 1200, None)
        self.assertIsInstance(first, int)
        self.assertIs(bilingual._reserve(rows, '{}', bilingual.config(), 1200, None), False)
        self.ready_retry()
        changed = {**self.entry, 'content': '<p>The body is a new English version.</p>'}
        bilingual.enqueue(changed)
        self.assertIs(bilingual._reserve(rows, '{}', bilingual.config(), 1200, None), False)
        await self.run_mock()
        current = bilingual._next_rows()
        self.assertEqual(current, [])
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_usage').fetchone()[0], 2)

    async def test_three_concurrent_batches_have_distinct_claims_and_budget(self):
        for index in range(7, 12):
            entry = {**self.entry, 'id': index}
            self.allow(entry)
            bilingual.enqueue(entry)
        active, peak = 0, 0
        started, release = asyncio.Event(), asyncio.Event()
        async def respond(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 3:
                started.set()
            await release.wait()
            active -= 1
            return self.reply(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            work = asyncio.create_task(bilingual.run_once(client))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                self.assertEqual(peak, 3)
            finally:
                release.set()
                result = await asyncio.wait_for(work, timeout=2)
        self.assertEqual(result['processed'], 6)
        self.assertEqual(len(self.calls), 3)
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(DISTINCT entry_id) FROM bilingual_usage').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT MAX(attempts) FROM bilingual_blocks').fetchone()[0], 1)
        with patch.dict('os.environ', {'BILINGUAL_DAILY_REQUESTS': '3'}):
            self.assertTrue((await self.run_mock())['budget_paused'])
        self.assertEqual(len(self.calls), 3)

    async def test_hundred_concurrent_batches_have_distinct_claims(self):
        active, peak = 0, 0
        started, release = asyncio.Event(), asyncio.Event()
        async def respond(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 100:
                started.set()
            await release.wait()
            active -= 1
            return self.reply(request)
        with patch.dict('os.environ', {
            'BILINGUAL_CONCURRENCY': '100',
            'BILINGUAL_DAILY_REQUESTS': '5000',
            'BILINGUAL_DAILY_TOKENS': '1000000',
        }):
            self.assertEqual(bilingual.config()['daily_requests'], 5000)
            for index in range(100, 200):
                entry = {**self.entry, 'id': index}
                self.allow(entry)
                bilingual.enqueue(entry)
            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            with patch.object(bilingual.httpx, 'AsyncClient', return_value=client) as factory:
                work = asyncio.create_task(bilingual.run_once())
                try:
                    await asyncio.wait_for(started.wait(), timeout=15)
                    self.assertEqual(peak, 100)
                finally:
                    release.set()
                    result = await asyncio.wait_for(work, timeout=15)
            self.assertEqual(factory.call_args.kwargs['limits'].max_connections, 100)
            self.assertEqual(factory.call_args.kwargs['limits'].max_keepalive_connections, 100)
            self.assertEqual(result['processed'], 200)
            self.assertEqual(len(self.calls), 100)
            with core.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT entry_id) FROM bilingual_usage').fetchone()[0], 100)
                self.assertEqual(db.execute('SELECT MAX(attempts) FROM bilingual_blocks').fetchone()[0], 1)
            with patch.object(bilingual, '_translate', side_effect=AssertionError('idle task')):
                self.assertEqual((await self.run_mock())['processed'], 0)
        for value, expected in [('0', 1), ('101', 100), ('invalid', 3)]:
            with patch.dict('os.environ', {'BILINGUAL_CONCURRENCY': value}):
                self.assertEqual(bilingual.config()['concurrency'], expected)

    async def test_enqueue_from_thread_wakes_idle_worker(self):
        idle, woke = asyncio.Event(), asyncio.Event()
        count = 0
        async def once():
            nonlocal count
            count += 1
            if count == 1:
                idle.set()
            else:
                woke.set()
            return {'processed': 0}
        with patch.object(bilingual, 'run_once', once):
            worker = asyncio.create_task(bilingual.run_worker())
            try:
                await asyncio.wait_for(idle.wait(), timeout=2)
                await asyncio.to_thread(bilingual.enqueue, self.entry)
                await asyncio.wait_for(woke.wait(), timeout=2)
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker
        self.assertFalse(bilingual._WAKE_WAITERS)

    async def test_only_english_is_admitted_without_paid_language_detection(self):
        samples = [
            ('fr', '<p>This is clearly English but explicitly marked French.</p>', 'skipped'),
            ('', '<p>Bonjour, voici un article sur les nouvelles technologies et les applications.</p>', 'skipped'),
            ('', '<p>Dies ist ein deutscher Artikel über neue technische Entwicklungen und Anwendungen.</p>', 'skipped'),
            ('', '<p>Este artículo trata sobre nuevas tecnologías y sus aplicaciones en sistemas.</p>', 'skipped'),
            ('', '<article lang="fr"><p>This is an article with a declared language.</p></article>', 'skipped'),
            ('', '<p>这是一篇中文文章，其中提到了 Python 和 JavaScript 技术。</p>', 'native'),
        ]
        for language, html, status in samples:
            entry = {**self.entry, 'language': language, 'content': html}
            self.allow(entry)
            self.assertFalse(bilingual.enqueue(entry))
            self.assertEqual(self.attached(entry)['status'], status)
            self.assertTrue(self.attached(entry)['source_hash'])
        english = {**self.entry, 'language': '', 'content': '<p>This article explains how the new system is used for processing events.</p>'}
        self.allow(english)
        self.assertTrue(bilingual.enqueue(english))
        self.assertEqual((await self.run_mock())['processed'], 1)



class TechnicalLiteralTests(unittest.TestCase):
    """Pure validator regressions; no database, provider, or optional parser."""
    LABELS = (
        'DX-M1M (NPU)', 'DX-M1 (NPU)', 'mobilenet_v2, 240×240',
        'deeplabv3plus, 512×512', '2361 FPS', 'dxrt-cli / dxtop',
        'GGML_SCHED_DEBUG_REALLOC=1 ./bin/llama-batched-bench\n'
        '-hf ggml-org/GLM-5.3-Flash-GGUF:Q2_K -npp 2500 -ntg 32 -npl 1,2\n'
        '-c 32768 -pps -kvu',
        'Windows PowerShell', 'macOS, Linux, WSL:', 'Homebrew (macOS/Linux):',
        'Windows CMD:', 'Ubuntu x64 (CPU)', 'Ubuntu arm64 (CPU)',
        'Ubuntu s390x (CPU)', 'Ubuntu x64 (Vulkan)', 'Ubuntu arm64 (Vulkan)',
        'Ubuntu x64 (ROCm 10.0)', 'Ubuntu x64 (OpenVINO)', 'Ubuntu x64 (SYCL FP16)',
        'Android arm64 (CPU)', 'Windows arm64 (CPU)', 'Windows arm64 (OpenCL Adreno)',
        'Windows arm64 (CUDA 13) - CUDA 13.4 DLLs', 'Windows x64 (SYCL)',
        'Windows arm64 (Vulkan)', 'openEuler x86 (310p)', 'Core i9-14900K ($550)',
        'iOS XCFramework', 'Ubuntu x64 (SYCL FP32)',
        'Windows x64 (CUDA 12) - CUDA 12.4 DLLs', 'openEuler aarch64 (310p)',
        'NVIDIA MIG', 'Zen 5 X3D', 'Ryzen 9 7950X3D ($700)',
        'Core Ultra 7 270K Plus ($300)', 'Raptor Lake Refresh', 'Arrow Lake Refresh',
        'GeForce GTX 970, 980, 980 Ti; GTX TITAN X (Maxwell).',
        'GeForce GTX 1060 (3/5/6 GB), 1070, 1070 Ti, 1080, 1080 Ti; TITAN X (Pascal), TITAN Xp.',
        'Quadro M4000, M5000, M6000, M6000 24GB; P2000, P2200, P4000, P5000, P6000.',
        '0F FA', '0F FB', '0F FC', '0F FD', '0F FE', '0F FF',
        '500 GiB', '250 GiB', '50 GiB', '25 GB', '$0.04 / GB',
        'AWS SigV4', 'Surface Laptop Ultra', 'Zen 3 / Vermeer',
        'Ryzen 9 5900X3D (AMD)', 'Ryzen 9 5900X3D (Chiphell)',
        'macOS Intel (x64)', 'Workers KV', 'Python 3.12.', 'ndcg@10',
        'ACS URL', 'ACS URL:', 'ACS URL: .', '#### API',
        'EP 3 909 047', 'DE 20 2021 004 551 U1',
        '2x16GB G.Skill Trident Z Neo RGB DDR5-7200',
        '4x8GB G.Skill Trident Z RGB DDR4-3200', 'AMD AM5 (Zen 5, Zen 4)',
        'AMD AM4 (Zen 3)', '2TB Sabrent Rocket 4 Plus',
        'pp8192   1583.9 -> 1657.1 tok/s (+4.6%)\npp64000   610.0 -> 684.5 tok/s (+12.2%)',
        '![Image 3: logo](https://example.org/logo.svg)', '[](https://example.org/share?article=123)',
    )

    def validate(self, source, target):
        return bilingual._validate(json.dumps({'items': [{'id': 1, 'text': target}]}),
                                   [{'block_id': 1, 'source_text': source}])

    def test_exact_platform_product_and_gpu_labels_are_accepted(self):
        for label in self.LABELS:
            with self.subTest(label=label):
                self.assertTrue(bilingual._technical_literal(label))
                self.assertEqual(self.validate(label, label), {1: label})

    def test_changed_labels_and_untranslated_prose_are_rejected(self):
        for label in self.LABELS:
            with self.subTest(changed=label):
                self.assertEqual(self.validate(label, label + ' Pro'), {})
        for sentence in (
            'This natural sentence must be translated.', 'Short words.',
            'Windows PowerShell Is Better', 'Windows PowerShell installation guide',
            'Windows PowerShell is fast.', 'A New Era For Windows',
            'NVIDIA MIG Improves Performance', 'NVIDIA MIG is available.',
            'Ryzen 9 7950X3D Is Faster', 'Raptor Lake Refresh Benchmarks',
            'GeForce GTX 970 supports the latest features.',
            'GeForce GTX 970 Versus Quadro M4000', 'The Future Of Computing',
            'Paul Graham:', 'Windows PowerShell\nWindows PowerShell',
            '500 GiB is enough.', '$0.04 / GB is cheap.', 'AWS SigV4 is enabled.',
            'Surface Laptop Ultra is faster.', 'Zen 3 / Vermeer benchmarks',
            'Workers KV is available.', 'Python 3.12 is fast.', 'ndcg@10 improves ranking.',
            '0F FA means a supported operation.',
            'AMD AM5 (Zen 5, Zen 4) improves compatibility.',
            '2TB Sabrent Rocket 4 Plus is faster.', 'EP 3 909 047 covers a useful invention.',
            '#### API overview', 'pp8192 1583.9 -> 1657.1 tok/s (+4.6%) explains the improvement.',
        ):
            with self.subTest(prose=sentence):
                self.assertFalse(bilingual._technical_literal(sentence))
                self.assertEqual(self.validate(sentence, sentence), {})
        self.assertFalse(bilingual._technical_literal('GeForce GTX ' + '970, ' * 80))

    def test_label_exception_keeps_marker_identity_and_markup_safety(self):
        for label in self.LABELS:
            marked = '[[t1]]' + label + '[[/t1]]'
            with self.subTest(label=label):
                self.assertEqual(self.validate(marked, marked), {1: marked})
                self.assertEqual(self.validate(marked, label), {})
                self.assertEqual(self.validate(marked, marked.replace('t1', 't9')), {})
                self.assertEqual(self.validate(marked, '[[t1]]' + label), {})
                self.assertEqual(self.validate(label, '<b>' + label + '</b>'), {})
        nested = '[[t1]]NVIDIA [[t3]]MIG[[/t3]][[/t1]]'
        self.assertEqual(self.validate(nested, '[[t3]]NVIDIA [[t1]]MIG[[/t1]][[/t3]]'), {})

    def test_labels_render_verbatim_once_without_changing_source_markup(self):
        for label in self.LABELS:
            if '\n' in label:
                continue
            html = '<p><a href="https://source.invalid/docs">' + label + '</a></p>'
            _, _, blocks = bilingual.extract(html)
            raw = json.dumps({'items': blocks})
            rows = [{'block_id': block['id'], 'source_text': block['text']} for block in blocks]
            translated = bilingual._validate(raw, rows)
            with self.subTest(label=label):
                self.assertEqual(len(translated), 1)
                for body in bilingual.render(html, translated):
                    self.assertEqual(body.count(label), 1)
                    self.assertIn('href="https://source.invalid/docs"', body)


class SegmentRecoveryTests(unittest.TestCase):
    """Pure source-token recovery; no database, provider or optional parser."""

    def plan(self, html):
        _, _, blocks = bilingual.extract(html)
        rows = [{'block_id': block['id'], 'source_text': block['text']} for block in blocks]
        items, plans = bilingual._recovery_plan(rows)
        output = [{'id': item['id'], 'text': '译文' * max(2, len(item['text']) // 5)} for item in items]
        return rows, items, plans, output

    def test_omitted_middle_link_recovers_with_every_token_from_source(self):
        html = ('<p>The <a href="https://source.invalid/first">first report</a> includes '
                '<a href="https://source.invalid/methods">the methods</a> and '
                '<a href="https://source.invalid/results">the results</a>.</p>')
        rows, items, plans, output = self.plan(html)
        source = rows[0]['source_text']
        self.assertIn('[[t3]]', source)
        missing = source.replace('[[t3]]', '').replace('[[/t3]]', '') + '中文译文'
        self.assertEqual(bilingual._validate(json.dumps({'items': [{'id': 0, 'text': missing}]}), rows), {})
        translated = bilingual._validate_recovery(json.dumps({'items': output}), rows, items, plans)
        self.assertEqual(bilingual.TOKEN.findall(translated[0]), bilingual.TOKEN.findall(source))
        self.assertTrue(all(not bilingual.TOKEN.search(item['text']) for item in items))
        for rendered in bilingual.render(html, translated):
            for part in ('first', 'methods', 'results'):
                self.assertIn('href="https://source.invalid/' + part + '"', rendered)

    def test_void_opaque_and_nested_markers_are_never_reordered(self):
        html = '<p>Read <a href="https://source.invalid/docs">the <code>tool()</code> docs</a><br>then continue.</p>'
        rows, items, plans, output = self.plan(html)
        translated = bilingual._validate_recovery(json.dumps({'items': output}), rows, items, plans)
        self.assertEqual(bilingual.TOKEN.findall(translated[0]), bilingual.TOKEN.findall(rows[0]['source_text']))
        for rendered in bilingual.render(html, translated):
            self.assertIn('<code>tool()</code>', rendered)
            self.assertIn('href="https://source.invalid/docs"', rendered)
            self.assertIn('<br>', rendered)

    def test_missing_duplicate_unknown_ids_or_model_markup_fail_closed(self):
        rows, items, plans, output = self.plan('<p>Read <a href="https://source.invalid/docs">the documentation</a> for details.</p>')
        self.assertEqual(bilingual._validate_recovery(json.dumps({'items': output[1:]}), rows, items, plans), {})
        for values in (output + [output[0]], output + [{'id': 999, 'text': '中文'}]):
            with self.assertRaises(ValueError):
                bilingual._validate_recovery(json.dumps({'items': values}), rows, items, plans)
        for unsafe in ('中文<script>bad()</script>', '中文[[t999]]', '中文<a href="https://attacker.invalid">链接</a>'):
            values = [{**output[0], 'text': unsafe}, *output[1:]]
            self.assertEqual(bilingual._validate_recovery(json.dumps({'items': values}), rows, items, plans), {})

    def test_missing_segment_rejects_only_its_complete_source_block(self):
        rows, items, plans, output = self.plan('<p>The first natural paragraph.</p><p>The second natural paragraph.</p>')
        translated = bilingual._validate_recovery(json.dumps({'items': output[1:]}), rows, items, plans)
        self.assertEqual(set(translated), {1})

    def test_format_only_cross_chunk_closing_token_is_exact_and_not_new_prose(self):
        parts = bilingual._parts('[[t1]]' + 'A' * 3494 + '[[/t1]]')
        self.assertEqual([len(part) for part in parts], [3500, 7])
        rows = [{'block_id': 1, 'source_text': parts[1]}]
        self.assertEqual(bilingual._validate(json.dumps({'items': [{'id': 1, 'text': parts[1]}]}), rows), {1: parts[1]})
        self.assertEqual(bilingual._validate(json.dumps({'items': [{'id': 1, 'text': parts[1] + '编造文字'}]}), rows), {})
        items, plans = bilingual._recovery_plan(rows)
        self.assertEqual(items, [])
        self.assertEqual(bilingual._validate_recovery('{"items":[]}', rows, items, plans), {1: '[[/t1]]'})
        self.assertIsNone(bilingual._repair_tokens('[[/t1]]中文[[t2]]', '[[t2]]source[[/t1]]'))


if __name__ == '__main__':
    unittest.main()
