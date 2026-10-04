"""Pure fixture DTO controls; no listener, network, browser or product mutation."""
import copy
import unittest

from reader_loading_fixture import Fixture, SCENARIO


class FeedShapeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = object.__new__(Fixture)
        self.fixture.base = 'http://127.0.0.1:31415'

    def assert_feed(self, feed):
        self.assertEqual(feed['icon'], {'feed_id': 7, 'icon_id': 0})
        self.assertEqual(feed['icon']['feed_id'], feed['id'])
        self.assertEqual(feed['category'], {'id': 1, 'title': '合成分类'})

    def test_all_list_and_detail_entries_have_native_icon_object(self):
        for number in range(1, 73):
            for deferred in (False, True):
                with self.subTest(number=number, deferred=deferred):
                    entry = self.fixture.entry(number, deferred)
                    self.assert_feed(entry['feed'])
                    self.assertEqual(entry['feed_id'], entry['feed']['id'])
                    self.assertEqual(entry['content_deferred'], deferred)
                    self.assertEqual(entry['content'] == '', deferred)

    def test_feed_list_and_entry_payloads_agree(self):
        status, feeds, label, delay = self.fixture.api('/mf/v1/feeds', 'GET', {}, None)
        self.assertEqual((status, label, delay), (200, 'feeds', 0))
        self.assert_feed(feeds[0])
        for number in (1, 24, 72):
            _, entry, _, _ = self.fixture.api('/mf/v1/entries/' + str(number), 'GET', {}, None)
            self.assertEqual(entry['feed'], feeds[0])

    def test_pages_still_have_24_deferred_entries_and_72_total(self):
        for offset in (0, 24, 48):
            status, body, label, delay = self.fixture.api('/mf/v1/entries', 'GET', {'limit': ['24'], 'offset': [str(offset)]}, None)
            self.assertEqual((status, body['total'], len(body['entries']), delay), (200, 72, 24, 150))
            self.assertEqual(label, f'list:{offset}:24')
            self.assertTrue(all(row['content_deferred'] and not row['content'] for row in body['entries']))
            for row in body['entries']:self.assert_feed(row['feed'])
        with self.assertRaises(ValueError):self.fixture.api('/mf/v1/entries', 'GET', {'limit': ['25']}, None)

    def test_payloads_do_not_share_mutable_icon_objects(self):
        one = self.fixture.entry(1)
        before = copy.deepcopy(one['feed'])
        one['feed']['icon']['icon_id'] = 999
        self.assertEqual(self.fixture.entry(1)['feed'], before)
        self.assertEqual(self.fixture.entry(2)['feed'], before)

    def test_no_icon_route_or_scenario_policy_is_added(self):
        self.assertEqual(self.fixture.api('/mf/v1/icons/0', 'GET', {}, None),
                         (501, {'error_message': 'unsupported_fixture_api'}, 'unsupported-api', 0))
        self.assertEqual(SCENARIO, {'schema': 1, 'entries': 72, 'page_size': 24, 'body_images': 6,
            'list_delay_ms': 150, 'detail_delay_ms': 200, 'image_delay_ms': 150,
            'image_cache_seconds': 3600, 'api_cache': 'no-store'})


if __name__ == '__main__':unittest.main()
