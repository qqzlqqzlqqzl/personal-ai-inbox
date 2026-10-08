"""Finite offline article warming controls; no production access."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from warm_reader_covers import (PAGE_SIZE, HEAD_ENTRIES, MAX_MISSES, CacheFull, SlowSource, bind_jobs,
    database_candidates, database_heads, digest, prune_state, readonly_database, select_candidates, version, warm_round)
from reader_cover_proxy import verified_proxy_target
from stabilize_media import signed_url

KEY = "unit-test-only-key"


def row(eid, **changes):
    return {"entry_id": eid, "user_id": 1, "feed_id": 2, "url": "https://example.org/article/" + str(eid),
            "cover_url": "https://example.org/cover/" + str(eid), "state": "done", "score": 8,
            "published_at": "2026-10-07T00:00:00Z", "content_hash": "input-v1", "updated_at": 1,
            "content_quality": None, **changes}


def quality(source, **_kwargs):
    return {"recommendation_eligible": source.get("eligible")}


def job(eid, source="source", widths=(480, 960)):
    return {"row": row(eid), "key": digest([eid, "input-v1"]), "source": digest(source),
            "path": str(eid), "widths": widths}


class CoverWarmTests(unittest.TestCase):
    def test_live_sort_heads_interleave_deduplicate_and_include_new_sources(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE analyses(entry_id INTEGER,user_id INTEGER,state TEXT,score REAL,"
                   "technical_score REAL,business_score REAL,published_at TEXT,feed_id INTEGER)")
        db.executemany("INSERT INTO analyses VALUES(?,1,'done',?,?,?,?,2)", [
            (1, 10, 5, 5, '2025-01-01'), (2, 8, 10, 5, '2025-01-01'),
            (3, 8, 5, 10, '2025-01-01'), (4, 8, 5, 5, '2026-10-07'),
            (5, 7, 10, 10, '2026-10-08')])
        db.execute("INSERT INTO analyses VALUES(99,2,'done',10,10,10,'2026-10-09',2)")
        feeds = {2: {'hide_globally': False, 'category': {'hide_globally': False}}}
        ids = lambda: [r['entry_id'] for r in database_heads(db, 1, quality, feeds=feeds, limit=1)]
        self.assertEqual(ids(), [4, 1, 2, 3])
        # A newly added source becomes eligible without editing a source/ID list.
        db.execute("INSERT INTO analyses VALUES(6,1,'pending',NULL,NULL,NULL,'2026-10-08',999)")
        feeds[999] = {'hide_globally': False, 'category': {'hide_globally': False}}
        self.assertEqual(ids(), [4, 1, 2, 3])
        db.execute("UPDATE analyses SET state='done',score=9,technical_score=10,business_score=10 WHERE entry_id=6")
        self.assertEqual(ids(), [6, 1])
        self.assertNotIn(1, [r['entry_id'] for r in database_heads(
            db, 1, lambda r: {'recommendation_eligible': r['entry_id'] != 1}, feeds=feeds, limit=1)])

    def test_hidden_feed_or_category_cannot_starve_visible_head_before_limit(self):
        for hidden_by in ('feed', 'category'):
            with self.subTest(hidden_by=hidden_by), sqlite3.connect(':memory:') as db:
                self.addCleanup(db.close)
                db.row_factory = sqlite3.Row
                db.execute('CREATE TABLE analyses(entry_id INTEGER,user_id INTEGER,state TEXT,score REAL,'
                           'technical_score REAL,business_score REAL,published_at TEXT,feed_id INTEGER,'
                           'url TEXT,cover_url TEXT)')
                hidden = [row(i, score=10, technical_score=10, business_score=10,
                              published_at='2026-10-08T00:00:00Z')
                          for i in range(1, HEAD_ENTRIES * 4 + 2)]
                visible = row(9999, feed_id=999, score=9, technical_score=9, business_score=9)
                excluded = [row(10000, feed_id=999, user_id=2), row(10001, feed_id=999, score=7),
                            row(10002, feed_id=999, state='pending'), row(10003, feed_id=998)]
                columns = ('entry_id', 'user_id', 'state', 'score', 'technical_score', 'business_score',
                           'published_at', 'feed_id', 'url', 'cover_url')
                db.executemany('INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?,?)',
                               [[item.get(column) for column in columns] for item in hidden + [visible] + excluded])
                feeds = {2: {'hide_globally': hidden_by == 'feed',
                             'category': {'hide_globally': hidden_by == 'category'}},
                         999: {'hide_globally': False, 'category': {'hide_globally': False}}}
                for _ in range(2):
                    heads = database_heads(db, 1, quality, feeds=feeds)
                    self.assertEqual([item['entry_id'] for item in heads], [9999])
                    metadata = [{'id': item['entry_id'], 'user_id': item['user_id'],
                                 'feed_id': item['feed_id'], 'url': item['url']} for item in heads]
                    jobs = bind_jobs(heads, metadata, feeds, lambda _entry, item: signed_url(item['cover_url'], KEY),
                                     lambda path: verified_proxy_target(path, KEY), quality=quality)
                    self.assertEqual(len(jobs), 1)
                    self.assertEqual(jobs[0]['widths'], [480, 960])
                # The next native snapshot may make the source visible again.
                feeds[2] = {'hide_globally': False, 'category': {'hide_globally': False}}
                restored = database_heads(db, 1, quality, feeds=feeds)
                self.assertEqual(len(restored), HEAD_ENTRIES)
                self.assertEqual({item['feed_id'] for item in restored}, {2})

    def test_no_visible_head_sources_returns_without_querying(self):
        db = Mock()
        db.execute.side_effect = AssertionError('empty visible feed scope queried analyses')
        for feeds in ({}, {2: {'hide_globally': True, 'category': {'hide_globally': False}}},
                      {2: {'hide_globally': False, 'category': {'hide_globally': True}}}):
            with self.subTest(feeds=feeds):
                self.assertEqual(database_heads(db, 1, quality, feeds=feeds), [])
        db.execute.assert_not_called()

    def test_threshold_quality_without_cover_and_without_240_total_cap(self):
        candidates = [row(900, score=6), row(899, state="removed"), row(898, eligible=False),
                      row(897, user_id=2), row(896, cover_url=""), *[row(i) for i in range(500, 0, -1)]]
        selected = select_candidates(candidates, 1, quality)
        self.assertEqual(len(selected), 501)
        self.assertEqual(selected[0]["entry_id"], 896)
        self.assertTrue(all(r["version"] == version(r) for r in selected))

    def test_keyset_pages_continue_beyond_240_and_database_is_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "analysis.sqlite3"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE analyses(entry_id INTEGER, user_id INTEGER, state TEXT, score REAL, published_at TEXT)")
                db.executemany("INSERT INTO analyses VALUES(?,1,'done',8,'2026-10-07T00:00:00Z')",
                               [(i,) for i in range(1, 551)])
            db.close()
            db = readonly_database(path)
            try:
                ids, cursor = [], None
                while True:
                    page, cursor, ended = database_candidates(db, 1, quality, after=cursor)
                    self.assertLessEqual(len(page), PAGE_SIZE)
                    ids.extend(r['entry_id'] for r in page)
                    if ended:
                        break
                self.assertEqual(ids, list(range(550, 0, -1)))
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("DELETE FROM analyses")
            finally:
                db.close()

    def test_current_metadata_identity_visibility_signature_and_cover_dedup(self):
        rows = select_candidates([row(i) for i in range(1, 7)], 1, quality)
        metadata = [{"id": r["entry_id"], "user_id": 1, "feed_id": 2, "url": r["url"]} for r in rows[:-1]]
        metadata[1]["url"] = "https://example.org/changed"
        metadata[2]["user_id"] = 2
        feeds = {2: {"hide_globally": False, "category": {"hide_globally": False}}}
        def sign(entry, source):
            return None if entry["id"] == 4 else signed_url("https://example.org/same-cover.jpg", KEY)
        verify = lambda path: verified_proxy_target(path, KEY)
        jobs = bind_jobs(rows, metadata, feeds, sign, verify, quality=quality)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["row"]["entry_id"], 1)
        self.assertEqual(jobs[0]['widths'], [480, 960])
        feeds[2]["category"]["hide_globally"] = True
        self.assertEqual(bind_jobs(rows, metadata, feeds, sign, verify, quality=quality), [])
        feeds[2]["category"]["hide_globally"] = False
        self.assertEqual(bind_jobs(rows, metadata, feeds, sign, lambda _path: None, quality=quality), [])

    def test_img_srcset_picture_only_verified_raw_originals_and_shared_cover(self):
        urls = {name: signed_url('https://example.org/' + name, KEY)
                for name in ('cover.jpg', 'two.png', 'three.avif', 'bad.svg', 'movie.mp4')}
        r = select_candidates([row(1)], 1, quality)[0]
        entry = {'id': 1, 'user_id': 1, 'feed_id': 2, 'url': r['url'], 'content':
            f"<img src='{urls['cover.jpg']}' srcset='{urls['cover.jpg']} 1x, {urls['two.png']} 2x'>"
            f"<picture><source type='image/avif' srcset='{urls['three.avif']} 2x'></picture>"
            f"<video><source src='{urls['movie.mp4']}'></video><img src='{urls['bad.svg']}'>"
            "<img src='https://outside.example/raw.jpg'><img src='/mf/proxy/forged/image'>"}
        feeds = {2: {'hide_globally': False, 'category': {'hide_globally': False}}}
        jobs = bind_jobs([r], [entry], feeds, lambda *_: urls['cover.jpg'],
                         lambda path: verified_proxy_target(path, KEY), quality=quality)
        self.assertEqual([j['widths'] for j in jobs], [[480, 960, 0], [0], [0]])
        self.assertTrue(all(j['priority'] > 0 for j in jobs))

    def test_hits_never_start_native_http_or_consume_miss_budget(self):
        result = warm_round([job(i) for i in range(40)], {}, lambda *_: True, lambda _row: True,
                            deadline=100, monotonic=lambda: 0)
        self.assertEqual((result['hits'], result['misses'], result['stop']), (80, 0, 'complete'))

    def test_60_misses_resume_whole_article_before_older_article(self):
        state, requests, cached = {}, [], set()
        def fetch(path, width, on_miss):
            if (path, width) not in cached:
                on_miss(); cached.add((path, width)); requests.append((path, width))
            return True
        jobs = [job(1, widths=tuple(range(61))), job(2, widths=(0,))]
        result = warm_round(jobs, state, fetch, lambda _row: True, deadline=100,
                            monotonic=lambda: 0, clock=lambda: 10)
        self.assertEqual(MAX_MISSES, 60)
        self.assertEqual((result['misses'], result['stop']), (60, 'miss_limit'))
        self.assertTrue(all(path == '1' for path, _ in requests))
        requests.clear()
        result = warm_round(jobs, state, fetch, lambda _row: True, deadline=100,
                            monotonic=lambda: 0, clock=lambda: 20)
        self.assertEqual(requests, [('1', 60), ('2', 0)])
        self.assertEqual(result['hits'], 60)

    def test_source_backoff_survives_pagination_and_allows_other_sources(self):
        state, requests = {}, []
        def fetch(path, width, on_miss):
            on_miss(); requests.append((path, width))
            if path == '1': raise SlowSource
            return True
        result = warm_round([job(1, 'slow'), job(2, 'slow'), job(3, 'other')], state, fetch,
                            lambda _row: True, deadline=100, monotonic=lambda: 0, clock=lambda: 10)
        self.assertEqual(requests, [('1', 480), ('3', 480), ('3', 960)])
        self.assertEqual((result['failed'], result['deferred']), (1, 1))
        prune_state(state, 20)
        requests.clear()
        warm_round([job(4, 'slow'), job(5, 'other')], state, fetch,
                   lambda _row: True, deadline=100, monotonic=lambda: 0, clock=lambda: 20)
        self.assertEqual(requests, [('5', 480), ('5', 960)])
        prune_state(state, 611)
        warm_round([job(1, 'slow')], state, fetch,
                   lambda _row: True, deadline=100, monotonic=lambda: 0, clock=lambda: 611)
        self.assertEqual(state['items'][job(1)['key']]['failures'], 2)

    def test_bad_image_does_not_backoff_other_images_on_same_host(self):
        def fetch(path, width, on_miss):
            on_miss(); return path != '1'
        result = warm_round([job(1), job(2)], {}, fetch, lambda _row: True,
                            deadline=100, monotonic=lambda: 0, clock=lambda: 10)
        self.assertEqual((result['failed'], result['fetched']), (1, 2))

    def test_capacity_stops_before_older_work_without_counting_miss(self):
        calls = []
        def full(path, width, on_miss):
            calls.append(path); raise CacheFull
        result = warm_round([job(1), job(2)], {}, full, lambda _row: True,
                            deadline=100, monotonic=lambda: 0)
        self.assertEqual(calls, ['1'])
        self.assertEqual((result['stop'], result['misses']), ('capacity', 0))

    def test_stale_analysis_rejected_and_publication_changes_version(self):
        result = warm_round([job(1)], {}, lambda *_: self.fail('stale fetch'), lambda _row: False,
                            deadline=100, monotonic=lambda: 0)
        self.assertEqual((result['stale'], result['misses']), (1, 0))
        self.assertNotEqual(version(row(1)), version(row(1, published_at='2020-01-01')))

    def test_deadline_stops_between_images(self):
        now, requests = [0], []
        def slow(path, width, on_miss):
            on_miss(); requests.append((path, width)); now[0] = 100
            return True
        result = warm_round([job(1), job(2)], {}, slow, lambda _row: True,
                            deadline=100, monotonic=lambda: now[0])
        self.assertEqual((result['stop'], requests), ('deadline', [('1', 480)]))


if __name__ == '__main__':
    unittest.main()
