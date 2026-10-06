"""Offline bounded tests: no model credentials, browser, Miniflux or pytest needed."""
import asyncio
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import httpx
import core
import bilingual_translation as bilingual


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
        bilingual.migrate()
        self.entry = {'id': 7, 'user_id': 3, 'title': 'Article', 'url': 'https://source.invalid/article',
                      'published_at': '2026-10-06T10:00:00Z',
                      'content': '<h2>A heading</h2><p>An original paragraph.</p>'}
        self.calls = []

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

    async def test_user_and_exact_body_version_isolation_and_restore(self):
        bilingual.enqueue(self.entry)
        await self.run_mock()
        self.assertNotIn('bilingual_html', self.attached(user=4))
        changed = {**self.entry, 'content': '<p>A changed article paragraph.</p>'}
        self.assertNotIn('bilingual_html', self.attached(changed))
        bilingual.enqueue(changed)
        bilingual.enqueue(self.entry)
        self.assertEqual(self.attached()['status'], 'done')
        self.assertEqual((await self.run_mock())['processed'], 0)
        with patch.dict('os.environ', {'BILINGUAL_MODEL': 'different-model'}):
            self.assertNotIn('bilingual_html', self.attached())

    def test_cache_read_never_enqueues_and_list_has_no_html(self):
        self.assertEqual(self.attached()['status'], 'pending')
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bilingual_articles').fetchone()[0], 0)
        deferred = {**self.entry, 'content_deferred': True, 'content': ''}
        self.assertFalse(bilingual.enqueue(deferred))
        self.assertNotIn('bilingual_html', self.attached(deferred))

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

    async def test_truncated_output_is_not_cached_and_has_bounded_retry(self):
        bilingual.enqueue(self.entry)
        for _ in range(bilingual.MAX_ATTEMPTS):
            await self.run_mock(finish='length')
            self.ready_retry()
        self.assertNotIn('bilingual_html', self.attached())
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
        bilingual.enqueue({**self.entry, 'id': 8})
        with patch.dict('os.environ', {'BILINGUAL_DAILY_REQUESTS': '1'}):
            self.assertTrue((await self.run_mock())['budget_paused'])
        self.assertEqual(len(self.calls), 1)

    async def test_exhausted_partial_and_budget_pause_are_terminal_with_html(self):
        bilingual.enqueue(self.entry)
        await self.run_mock(omitted={1})
        self.ready_retry()
        with patch.dict('os.environ', {'BILINGUAL_DAILY_REQUESTS': '1'}):
            self.assertTrue((await self.run_mock())['budget_paused'])
        self.assertEqual(self.attached()['status'], 'budget_paused')
        self.assertIn('bilingual_html', self.attached())
        for _ in range(bilingual.MAX_ATTEMPTS - 1):
            await self.run_mock(omitted={1})
            self.ready_retry()
        self.assertEqual(self.attached()['status'], 'error')
        self.assertIn('bilingual_html', self.attached())

    async def test_changed_prompt_never_bills_old_queued_version(self):
        bilingual.enqueue(self.entry)
        with patch.object(bilingual, 'PROMPT', bilingual.PROMPT + '\nA new instruction.'):
            self.assertEqual(bilingual._next_rows(), [])
            self.assertNotIn('bilingual_html', self.attached())
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
            self.assertIn('<td><span class="reader-translation-target"', html)
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

    async def test_worker_seeds_scored_newest_existing_body_only(self):
        with core.connect() as db:
            for index, score, state in [(11, 9, 'done'), (12, 8, 'done'), (13, 7, 'done'), (14, 9, 'pending')]:
                db.execute('INSERT INTO analyses(entry_id,user_id,state,score,published_at,updated_at) VALUES (?,?,?,?,?,?)',
                           (index, 3, state, score, '2026-10-' + str(index), 1))
        gets = []
        def response(request):
            if request.method == 'GET':
                gets.append(str(request.url))
                return httpx.Response(200, json={**self.entry, 'id': int(request.url.path.rsplit('/', 1)[-1])})
            return self.reply(request)
        def decorate(entry, user_id, include_source_fallback=False):
            self.assertTrue(include_source_fallback)
            self.assertEqual(user_id, 3)
            return {**entry, 'content': '<p>Prepared real source body.</p>', 'ai': {'state': 'done', 'score': 9}}
        with patch.dict('os.environ', {'MINIFLUX_API_KEY': 'offline-miniflux-key'}), \
                patch.dict('sys.modules', {'worker': types.SimpleNamespace(MF='http://miniflux.invalid/mf')}), \
                patch.object(core, 'decorate', decorate):
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                result = await bilingual.run_once(client)
        self.assertEqual(gets, ['http://miniflux.invalid/mf/v1/entries/12', 'http://miniflux.invalid/mf/v1/entries/11'])
        self.assertEqual(result['seeded'], 2)
        self.assertEqual(len(self.calls), 1)
        payload = json.loads(json.loads(self.calls[0].content)['messages'][1]['content'])
        self.assertEqual(payload['items'][0]['text'], 'Prepared real source body.')


if __name__ == '__main__':
    unittest.main()
