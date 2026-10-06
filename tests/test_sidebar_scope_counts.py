"""Pure, offline tests for complete sidebar counts and native scope semantics."""
import json
import unittest

import notes_metadata
import sidebar_scope_counts as counts


class SidebarCountsTests(unittest.TestCase):
    def setUp(self):
        self.categories = {10: {"id": 10, "user_id": 1, "hide_globally": False},
                           20: {"id": 20, "user_id": 1, "hide_globally": True},
                           30: {"id": 30, "user_id": 1, "hide_globally": False}}
        self.feeds = {1: {"id": 1, "user_id": 1, "hide_globally": False, "category": self.categories[10]},
                      2: {"id": 2, "user_id": 1, "hide_globally": False, "category": self.categories[20]},
                      3: {"id": 3, "user_id": 1, "hide_globally": False, "category": self.categories[30]}}
        self.entries = [dict(id=i, user_id=1, feed_id=fid, title=f"Title {i}",
            url=f"https://example.invalid/{i}", published_at=published, changed_at=changed)
            for i, fid, published, changed in (
                (1, 1, "2026-09-28T01:00:00Z", "2026-09-27T01:00:00Z"),
                (2, 1, "2026-09-27T01:00:00Z", "2026-09-28T01:00:00Z"),
                (3, 2, "2026-09-28T02:00:00Z", "2026-09-28T02:00:00Z"))]
        self.params = dict(ai_view="recommended", today_after="1790553600", globally_visible="true")
        self.candidates = {e["id"]: {"entry_id": e["id"], "note": "motor idea"} for e in self.entries}

    def aggregate(self, **changes):
        return counts.aggregate(self.entries, self.feeds, self.categories, self.candidates,
                                {1, 2, 3}, {2}, {2, 3}, {**self.params, **changes})

    def test_all_scopes_and_real_zeros_arrive_together(self):
        self.assertEqual(self.aggregate(), dict(all=2, today=1, starred=1, history=1,
            category={"10": 2, "20": 1, "30": 0}, feed={"1": 2, "2": 1, "3": 0}))
        self.assertEqual(self.aggregate(globally_visible="false")["all"], 3)

    def test_today_cutoff_does_not_filter_all(self):
        result = self.aggregate(today_after="1790640000")
        self.assertEqual(result["today"], 0)
        self.assertEqual(result["all"], 2)

    def test_scope_date_columns_match_native_and_today_ignores_picker(self):
        result = self.aggregate(date_after="1790553600", date_before="1790639999", date_field="published_at")
        self.assertEqual((result["all"], result["starred"], result["history"], result["today"]), (1, 1, 1, 1))
        result = self.aggregate(date_after="1790467200", date_before="1790553599", date_field="changed_at")
        self.assertEqual((result["all"], result["starred"], result["history"], result["today"]), (1, 0, 0, 1))

    def test_changed_boundaries_are_strict_but_published_are_inclusive(self):
        self.entries[0]["published_at"] = "2026-09-28T00:00:00Z"
        self.entries[1]["changed_at"] = "2026-09-28T00:00:00Z"
        result = self.aggregate(date_after="1790553600", date_before="1790639999", date_field="published_at")
        self.assertEqual(result["all"], 1)
        self.assertEqual(result["history"], 0)

    def test_notes_use_current_title_or_owned_note(self):
        self.entries[0]["title"] = "Fresh motor title"
        self.candidates[2]["note"] = "cache"
        self.assertEqual(self.aggregate(ai_view="notes", search="MOTOR")["all"], 1)

    def test_incomplete_parent_mapping_is_unknown_not_zero(self):
        self.entries[0]["feed_id"] = 999
        with self.assertRaises(ValueError):
            self.aggregate()

    def test_unowned_and_duplicate_categories_are_rejected(self):
        for rows in ([{"id": 1, "user_id": 2, "hide_globally": False}],
                     [self.categories[10], self.categories[10]]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                counts.decode_categories(json.dumps(rows).encode(), 1)

    def test_id_pages_require_complete_stable_numeric_snapshot(self):
        self.assertEqual(counts.decode_id_page(b'{"entry_ids":[1,2],"total":3}', 0, set()), ([1, 2], 3))
        self.assertEqual(counts.decode_id_page(b'{"entry_ids":[3],"total":3}', 2, {1, 2}, 3), ([3], 3))
        self.assertEqual(counts.decode_id_page(b'{"entry_ids":[],"total":0}', 0, set()), ([], 0))
        for payload, offset, seen, total in (
            ({"entry_ids": [], "total": 3}, 0, set(), None),
            ({"entry_ids": [True], "total": 1}, 0, set(), None),
            ({"entry_ids": [1, 1], "total": 2}, 0, set(), None),
            ({"entry_ids": [2], "total": 3}, 2, {1, 2}, 3),
            ({"entry_ids": [3], "total": 4}, 2, {1, 2}, 3),
            ({"entry_ids": [1], "total": 0}, 0, set(), None)):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                counts.decode_id_page(json.dumps(payload).encode(), offset, seen, total)

    def test_metadata_rejects_cross_user_and_unexpected_body(self):
        for change in ({"user_id": 2}, {"content": "must never be read"}):
            entry = {key: self.entries[0][key] for key in notes_metadata.FIELDS}
            with self.subTest(change=change), self.assertRaises(ValueError):
                notes_metadata.decode_metadata(json.dumps({"entries": [{**entry, **change}]}), 1, [1])

    def test_invalid_publication_is_unknown_instead_of_a_fake_today_zero(self):
        for value in ("not a date", "2026-09-28T01:00:00"):
            self.entries[0]["published_at"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.aggregate()


if __name__ == "__main__":
    unittest.main()
