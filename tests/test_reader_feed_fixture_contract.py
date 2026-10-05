import copy
import unittest
from reader_feed_fixture_contract import producers, validate


class FeedProducerContract(unittest.TestCase):
    def test_real_producers_have_renderable_feed_icon(self):
        rows = producers()
        self.assertEqual(set(rows), {'harness-default', 'harness-subscribe', 'query', 'reading', 'performance', 'quality'})
        self.assertEqual([len(v) for v in rows.values()], [1, 2, 24, 1, 8, 7])
        for label, feeds in rows.items():
            for feed in feeds:
                with self.subTest(producer=label, id=feed['id']):
                    validate(feed, full_native_icon=label.startswith('harness-'))
        self.assertEqual([f['id'] for f in rows['harness-subscribe']], [8, 9])

    def test_missing_null_wrong_owner_and_bool_ids_are_rejected(self):
        feed = producers()['harness-default'][0]
        for icon in (None, {}, {'feed_id': 8, 'icon_id': 0}, {'feed_id': True, 'icon_id': 0},
                     {'feed_id': 7, 'icon_id': False}, {'feed_id': 7, 'icon_id': 0, 'external_icon_id': None}):
            with self.subTest(icon=icon), self.assertRaises(AssertionError):
                validate({**feed, 'icon': icon})


if __name__ == '__main__': unittest.main()
