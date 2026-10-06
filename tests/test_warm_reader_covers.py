"""Finite offline controls; no native API, credentials or production files."""
import unittest
from warm_reader_covers import (MAX_CANDIDATES, MAX_MISSES, SlowSource, bind_jobs, digest,
                                select_candidates, version, warm_round)


def row(eid, **changes):
    return {"entry_id": eid, "user_id": 1, "feed_id": 2, "url": "https://example.org/article/" + str(eid),
            "cover_url": "https://example.org/cover/" + str(eid), "state": "done", "score": 8,
            "content_hash": "input-v1", "updated_at": 1, "content_quality": None, **changes}


def quality(source, **_kwargs):
    return {"recommendation_eligible": source.get("eligible")}


def job(eid, source="source"):
    return {"row": row(eid), "key": digest([eid, "input-v1"]), "source": digest(source), "path": str(eid)}


class CoverWarmTests(unittest.TestCase):
    def test_newest_list_threshold_quality_and_240_limit(self):
        candidates = [row(900, score=6), row(899, state="removed"), row(898, eligible=False),
                      row(897, user_id=2), row(896, cover_url=""), *[row(i) for i in range(500, 0, -1)]]
        selected = select_candidates(candidates, 1, quality)
        self.assertEqual(MAX_CANDIDATES, 240)
        self.assertEqual([r["entry_id"] for r in selected], list(range(500, 260, -1)))
        self.assertTrue(all(r["version"] == version(r) for r in selected))

    def test_current_metadata_identity_visibility_signature_and_same_url_dedup(self):
        rows = select_candidates([row(i) for i in range(1, 7)], 1, quality)
        metadata = [{"id": r["entry_id"], "user_id": 1, "feed_id": 2, "url": r["url"]} for r in rows[:-1]]
        metadata[1]["url"] = "https://example.org/changed"
        metadata[2]["user_id"] = 2
        feeds = {2: {"hide_globally": False, "category": {"hide_globally": False}}}
        def sign(entry, source):
            return None if entry["id"] == 4 else "/mf/proxy/same-image"
        calls = []
        def verify(path):
            calls.append(path)
            return "https://example.org/same-cover"
        jobs = bind_jobs(rows, metadata, feeds, sign, verify, quality=quality)
        self.assertEqual(len(jobs), 1)  # 1 and 5 share a cover; 6 was omitted/removed.
        self.assertEqual(jobs[0]["row"]["entry_id"], 1)
        self.assertEqual(calls, ["proxy/same-image", "proxy/same-image"])
        feeds[2]["category"]["hide_globally"] = True
        self.assertEqual(bind_jobs(rows, metadata, feeds, sign, verify, quality=quality), [])
        feeds[2]["category"]["hide_globally"] = False
        self.assertEqual(bind_jobs(rows, metadata, feeds, sign, lambda _path: None, quality=quality), [])

    def test_hits_never_start_native_http_or_consume_miss_budget(self):
        state, requests = {}, []
        def cached(path, width, on_miss):
            requests.append((path, width))
            return True  # Same boundary as fetch_variant's cached return.
        result = warm_round([job(i) for i in range(40)], state, cached, lambda _row: True,
                            deadline=100, monotonic=lambda: 0)
        self.assertEqual(result["hits"], 80)
        self.assertEqual(result["misses"], 0)
        self.assertEqual(result["stop"], "complete")

    def test_actual_misses_cap_30_and_later_unattempted_candidates_progress(self):
        state, requests = {}, []
        def miss(path, width, on_miss):
            on_miss()
            requests.append((path, width))
            return True
        jobs = [job(i, str(i)) for i in range(40)]
        result = warm_round(jobs, state, miss, lambda _row: True, deadline=100,
                            monotonic=lambda: 0, clock=lambda: 10)
        self.assertEqual(MAX_MISSES, 30)
        self.assertEqual(result["misses"], 30)
        self.assertEqual(len(requests), 30)
        self.assertEqual(result["stop"], "miss_limit")
        self.assertEqual(len(state["items"]), 15)
        requests.clear()
        warm_round(jobs, state, miss, lambda _row: True, deadline=100,
                   monotonic=lambda: 0, clock=lambda: 20)
        self.assertEqual(requests[0], ("15", 480), "later pages precede already attempted hot rows")

    def test_failure_source_backoff_is_bounded_and_does_not_starve_other_source(self):
        state, requests = {}, []
        def fetch(path, width, on_miss):
            on_miss();requests.append((path, width))
            if path == "1":
                raise SlowSource
            return True
        jobs = [job(1, "slow"), job(2, "slow"), job(3, "other")]
        result = warm_round(jobs, state, fetch, lambda _row: True, deadline=100,
                            monotonic=lambda: 0, clock=lambda: 10)
        self.assertEqual(requests, [("1", 480), ("3", 480), ("3", 960)])
        self.assertEqual((result["failed"], result["deferred"]), (1, 1))
        state["items"]["obsolete-input"] = {"retry_at": 999999}
        state["sources"]["obsolete-source"] = {"retry_at": 999999}
        requests.clear()
        warm_round(jobs, state, fetch, lambda _row: True, deadline=100,
                   monotonic=lambda: 0, clock=lambda: 20)
        self.assertTrue(all(path == "3" for path, _width in requests))
        self.assertNotIn("obsolete-input", state["items"])
        self.assertNotIn("obsolete-source", state["sources"])
        changed = {**jobs[0], "key": digest("new-input"), "source": digest("new-source")}
        requests.clear()
        warm_round([changed], state, fetch, lambda _row: True, deadline=100,
                   monotonic=lambda: 0, clock=lambda: 20)
        self.assertEqual(requests, [("1", 480)], "new input is independently eligible")

    def test_bad_individual_image_does_not_backoff_other_images_on_same_host(self):
        state, requests = {}, []
        def fetch(path, width, on_miss):
            on_miss();requests.append((path, width))
            return path != "1"
        result = warm_round([job(1), job(2)], state, fetch, lambda _row: True,
                            deadline=100, monotonic=lambda: 0, clock=lambda: 10)
        self.assertEqual(requests, [("1", 480), ("2", 480), ("2", 960)])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(state["sources"], {})

    def test_stale_analysis_is_rejected_before_fetch(self):
        def forbidden(*_args):
            self.fail("stale metadata cannot initiate image HTTP")
        result = warm_round([job(1)], {}, forbidden, lambda _row: False,
                            deadline=100, monotonic=lambda: 0)
        self.assertEqual(result["stale"], 1)
        self.assertEqual(result["misses"], 0)
        self.assertNotEqual(version(row(1)), version(row(1, content_hash="new-input")))

    def test_deadline_prevents_first_and_subsequent_downloads(self):
        def forbidden(*_args):
            self.fail("expired round cannot start native HTTP")
        result = warm_round([job(1)], {}, forbidden, lambda _row: True,
                            deadline=100, monotonic=lambda: 100)
        self.assertEqual(result["stop"], "deadline")
        now, requests = [0], []
        def slow(path, width, on_miss):
            on_miss();requests.append((path, width));now[0] = 100
            return True
        result = warm_round([job(1), job(2)], {}, slow, lambda _row: True,
                            deadline=100, monotonic=lambda: now[0])
        self.assertEqual(result["stop"], "deadline")
        self.assertEqual(requests, [("1", 480)])


if __name__ == "__main__":
    unittest.main()
