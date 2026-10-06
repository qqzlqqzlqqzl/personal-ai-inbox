"""Pure fixture/assertion rejection controls. These are NOT browser/product passes."""
from copy import deepcopy
import struct
import unittest
import zlib

from quality_consumer_fixture import (CASES, MINIMUM, RECOMMENDED_IDS,
                                      QualityConsumerFixture, make_entries,
                                      validate_badge, validate_capture_png)


def png(width=8, height=8, *, blank=False, alpha=None):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend((255, 255, 255) if blank else (x * 23 % 256, y * 29 % 256, (x+y) * 17 % 256))
            if alpha is not None:
                row.append(alpha)
        rows.append(bytes(row))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6 if alpha is not None else 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows))) + chunk(b'IEND', b''))


class QualityFixtureControls(unittest.TestCase):
    def setUp(self):
        self.entries = make_entries({'id': 7, 'title': 'Synthetic', 'icon': {'feed_id': 7, 'icon_id': 0}})
        self.fixture = QualityConsumerFixture(self.entries)

    def test_originals_retain_all_seven_but_recommendation_total_excludes_ineligible(self):
        original = self.fixture.respond('/mf/v1/entries', 'GET', {'limit': ['24']})[1]
        recommended = self.fixture.respond('/mf/v1/entries', 'GET', {'ai_view': ['recommended'], 'ai_min': ['8'], 'limit': ['24']})[1]
        self.assertEqual(original['total'], 7)
        self.assertEqual([e['id'] for e in original['entries']], list(CASES))
        self.assertEqual(recommended['total'], 2)
        self.assertEqual([e['id'] for e in recommended['entries']], [704, 706])
        self.assertEqual(self.fixture.principal_requests('recommended')[-1]['query']['ai_min'], ['8'])

    def test_total_precedes_pagination_and_id_scope_agrees(self):
        query = {'ai_view': ['recommended'], 'ai_min': ['8'], 'limit': ['1'], 'offset': ['1']}
        page = self.fixture.select(query)
        self.assertEqual((page['total'], [e['id'] for e in page['entries']]), (2, [706]))
        ids = self.fixture.select({'ai_view': ['recommended'], 'ai_min': ['8']}, ids=True)
        self.assertEqual(ids, {'total': 2, 'entry_ids': [706, 704]})

    def test_scope_count_batch_uses_recommendation_lens_and_keeps_known_zeros(self):
        entries = deepcopy(self.entries)
        for entry in entries:
            entry['feed']['category'] = {'id': 1}
        fixture = QualityConsumerFixture(entries, categories=[{'id': 1}, {'id': 2}], feeds=[{'id': 7}, {'id': 8}])
        query = {'ai_view': ['recommended'], 'ai_min': ['8'], 'today_after': ['1791160000'], 'globally_visible': ['true']}
        status, body = fixture.respond('/mf/v1/ai/scope-counts', 'GET', query)
        self.assertEqual(status, 200)
        self.assertEqual(body, {'scope_counts': {'all': 2, 'today': 0, 'starred': 0, 'history': 2,
                                               'category': {'1': 2, '2': 0}, 'feed': {'7': 2, '8': 0}}})
        unread = fixture.respond('/mf/v1/ai/scope-counts', 'GET', {**query, 'status': ['unread']})[1]['scope_counts']
        self.assertEqual((unread['all'], unread['history'], unread['feed']['7']), (0, 2, 0))
        self.assertEqual(fixture.entries, entries)

    def test_scope_count_batch_rejects_invalid_queries_and_mutations(self):
        valid = {'ai_view': ['recommended'], 'today_after': ['1791160000']}
        for query in ({}, {**valid, 'today_after': ['-1']}, {**valid, 'today_after': ['x']},
                      {**valid, 'today_after': ['1', '2']}, {**valid, 'ai_view': ['all']},
                      {**valid, 'unknown': ['x']}, {**valid, 'limit': ['24']}, {**valid, 'status': ['broken']}):
            with self.subTest(query=query), self.assertRaises((ValueError, TypeError)):
                self.fixture.respond('/mf/v1/ai/scope-counts', 'GET', query)
        self.assertIsNone(self.fixture.respond('/mf/v1/ai/scope-counts', 'POST', valid, {}))

    def test_versioned_recommendation_pages_and_changed_results(self):
        query = {'ai_view': ['recommended'], 'ai_min': ['8'], 'limit': ['1'], 'ai_revision': ['initial']}
        first = self.fixture.select(query)
        self.assertEqual(first['entries'][0]['id'], 704)
        query.update(offset=['1'], ai_revision=[first['ai_revision']])
        second = self.fixture.select(query)
        self.assertEqual(second['entries'][0]['id'], 706)
        self.assertEqual(first['ai_revision'], second['ai_revision'])
        self.fixture.entries[3]['ai']['score'] = 7
        changed = self.fixture.select(query)
        self.assertEqual(changed['entries'], [])
        self.assertTrue(changed['ai_result_changed'])
        self.assertNotEqual(changed['ai_revision'], first['ai_revision'])
        with self.assertRaises(ValueError):
            self.fixture.select({**query, 'ai_revision': ['broken']})

    def test_actual_point_api_is_needed_for_body_and_preserves_quality(self):
        cards = self.fixture.select({})['entries']
        for card in cards:
            with self.subTest(entry=card['id']):
                self.assertTrue(card['content_deferred'])
                self.assertEqual(card['content'], '')
                status, detail = self.fixture.respond(f"/mf/v1/entries/{card['id']}", 'GET', {})
                self.assertEqual(status, 200)
                self.assertIn('<p>', detail['content'])
                self.assertEqual(card['ai'], detail['ai'])
                detail['ai']['state'] = 'tampered-response'
        self.assertEqual(self.fixture.entries, self.entries)

    def test_publisher_metadata_signal_is_not_a_visible_paywall(self):
        for entry in self.entries[:2]:
            with self.subTest(entry=entry['id']):
                q = entry['ai']['content_quality']
                self.assertEqual(q['access'], 'unknown')
                self.assertIn('publisher_nonfree_pending_review', q['reason_codes'])
                self.assertFalse(q['recommendation_eligible'])
                self.assertTrue(entry['url'].startswith('https://www.theverge.com/'))
                self.assertIn('合成', entry['content'])
        self.assertEqual(self.entries[-1]['ai']['content_quality']['access'], 'paid_fulltext')

    def test_short_substantive_and_legacy_are_not_excluded_by_length_or_null(self):
        short = self.entries[3]
        self.assertLess(len(short['content']), 80)
        self.assertIsNone(self.entries[5]['ai']['content_quality'])
        self.assertEqual([e['id'] for e in self.fixture.select({'ai_view': ['recommended']})['entries']], RECOMMENDED_IDS)

    def test_fetch_failure_does_not_become_low_information_or_scored(self):
        failed = self.entries[4]['ai']
        self.assertEqual(failed['processing']['reason_code'], 'extraction_failed')
        self.assertEqual(failed['content_quality']['information'], 'unknown')
        self.assertIsNone(failed['content_quality']['recommendation_eligible'])
        self.assertNotIn('score', failed)
        self.assertNotEqual(failed['state'], 'content_excluded')

    def test_read_star_date_and_score_filters_have_real_effect(self):
        self.assertEqual(self.fixture.select({'status': ['unread']})['total'], 0)
        self.assertEqual(self.fixture.select({'starred': ['true']})['total'], 0)
        self.assertEqual(self.fixture.select({'published_after': ['1791160000']})['total'], 0)
        self.assertEqual(self.fixture.select({'ai_view': ['recommended'], 'ai_min': ['9']})['total'], 1)

    def test_raw_roundtrip_does_not_mutate_store(self):
        original = deepcopy(self.fixture.entries)
        self.fixture.select({'ai_view': ['recommended']})
        self.fixture.select({'limit': ['1']})['entries'][0]['ai']['state'] = 'mutated'
        self.assertEqual(self.fixture.entries, original)
        self.assertEqual(self.fixture.select({})['total'], 7)

    def test_reject_invalid_or_unbounded_query_instead_of_hidden_empty_success(self):
        for query in ({'ai_view': ['pending']}, {'ai_view': ['all', 'recommended']},
                      {'ai_view': 'all'}, {'limit': ['0']}, {'limit': ['25']},
                      {'offset': ['-1']}, {'offset': ['10001']}, {'ai_min': ['11']},
                      {'ai_min': ['eight']}, {'unknown': ['x']}, {'status': ['broken']},
                      {'published_before': ['-1']}, {'direction': ['sideways']}, {'ai_sort': ['invented']}):
            with self.subTest(query=query), self.assertRaises((ValueError, TypeError)):
                self.fixture.select(query)

    def test_unknown_detail_and_mutations_are_not_generic_success(self):
        self.assertEqual(self.fixture.respond('/mf/v1/entries/999', 'GET', {})[0], 404)
        self.assertIsNone(self.fixture.respond('/mf/v1/entries', 'PUT', {}, {}))
        self.assertIsNone(self.fixture.respond('/mf/v1/ai/settings', 'PUT', {}, {}))
        with self.assertRaises(ValueError):
            self.fixture.respond('/mf/v1/entries/701', 'GET', {'secret': ['x']})
        with self.assertRaises(ValueError):
            self.fixture.respond('/mf/v1/ai/reading-session', 'POST', {}, {'entry_id': 999, 'action': 'open'})

    def test_permitted_telemetry_records_only_synthetic_entry(self):
        before = deepcopy(self.fixture.entries)
        for action in ('open', 'heartbeat', 'close'):
            self.assertEqual(self.fixture.respond('/mf/v1/ai/reading-session', 'POST', {}, {'entry_id': 701, 'action': action}), (200, {'ok': True}))
        self.assertEqual(len(self.fixture.telemetry), 3)
        self.assertEqual(before, self.fixture.entries)

    def test_badge_assertion_rejects_missing_caution_and_wrong_semantics(self):
        for entry_id, case in CASES.items():
            good = ' · '.join(case['required'])
            with self.subTest(entry=entry_id):
                self.assertEqual(validate_badge(entry_id, good), good)
                with self.assertRaises(AssertionError):
                    validate_badge(entry_id, 'unrelated badge')
            for wrong in case['forbidden'] + ['$2', '两美元']:
                with self.subTest(entry=entry_id, wrong=wrong), self.assertRaises(AssertionError):
                    validate_badge(entry_id, good + ' ' + wrong)

    def test_capture_pixels_dimensions_crc_and_blank_rejection(self):
        for alpha in (None, 255):
            self.assertGreaterEqual(validate_capture_png(png(alpha=alpha), (8, 8))['distinct_colors_lower_bound'], 32)
        for data, size in ((png(blank=True), (8, 8)), (png(), (16, 8)), (png()[:-8], (8, 8)),
                           (png() + b'extra', (8, 8)), (png(alpha=0), (8, 8)), (b'not an image', (8, 8))):
            with self.subTest(size=size, length=len(data)), self.assertRaises(AssertionError):
                validate_capture_png(data, size)
        broken = bytearray(png()); broken[-5] ^= 1
        with self.assertRaisesRegex(AssertionError, 'CRC'):
            validate_capture_png(bytes(broken), (8, 8))


if __name__ == '__main__':
    unittest.main()
