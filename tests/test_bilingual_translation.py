"""Offline bounded tests: no model credentials, browser, Miniflux or pytest needed."""
import asyncio
import json
from pathlib import Path
import tempfile
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
            'BILINGUAL_DAILY_REQUESTS': '1000',
            'BILINGUAL_DAILY_TOKENS': '1000000',
        }):
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


if __name__ == '__main__':
    unittest.main()
