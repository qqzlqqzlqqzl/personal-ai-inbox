import ast
import importlib.util
from pathlib import Path
import sqlite3
import sys
import types
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from runtime_resilience import source_retry
from patch_runtime import vendor, preview


class RuntimeTests(unittest.TestCase):
    def test_status_classification_and_no_sensitive_output(self):
        for code, category, minimum in [(403, 'access_denied', 21600), (404, 'source_missing', 86400),
                                        (429, 'rate_limited', 1800), (503, 'upstream_http', 1800)]:
            exc = RuntimeError('secret-token-in-url')
            exc.response = types.SimpleNamespace(status_code=code, headers={})
            result = source_retry(exc, 1, 10000)
            self.assertEqual(result['category'], category)
            self.assertGreaterEqual(result['delay'], minimum)
            self.assertNotIn('secret', str(result))

    def test_retry_after_and_backoff_cap(self):
        exc = RuntimeError()
        exc.response = types.SimpleNamespace(status_code=429, headers={'retry-after': '7200'})
        self.assertEqual(source_retry(exc, 1, 10000)['delay'], 7200)
        self.assertEqual(source_retry(exc, 10000, 10000)['delay'], 86400)
        self.assertEqual(source_retry(ValueError(), 1, 10000)['category'], 'source_format')

    def test_preview_sql_permanent_errors_wait_week_transient_wait_six_hours(self):
        # Exercise the transformed production query function against SQLite.
        source = '''
def pending_previews(now, limit=12, feed_id=None):
    where = ["state <> 'removed'", "(preview_checked_at IS NULL OR (preview_error IS NOT NULL AND preview_checked_at <= ?))"]
    values = [now - RETRY_SECONDS]
    if feed_id is not None:
        where.append("feed_id=?")
        values.append(feed_id)
    with core.connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM analyses WHERE " + " AND ".join(where), values)]
'''
        class Database(sqlite3.Connection):
            def __exit__(self, *args):
                return False
        db = sqlite3.connect(':memory:', factory=Database)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE analyses (entry_id, state, preview_checked_at, preview_error, feed_id)')
        for i, age, error in [(1, 86400, 'HTTPStatusError HTTP 403'), (2, 8*86400, 'HTTPStatusError HTTP 403'),
                              (3, 7*3600, 'TimeoutError'), (4, 3600, 'TimeoutError')]:
            db.execute('INSERT INTO analyses VALUES (?,?,?,?,?)', (i, 'done', 1000000-age, error, 1))
        scope = {'core': types.SimpleNamespace(connect=lambda: db)}
        exec(compile(preview(source), '<patched-preview>', 'exec'), scope)
        self.assertEqual([r['entry_id'] for r in scope['pending_previews'](1000000)], [2, 3])
        db.close()

    def test_unknown_source_shape_refuses_patch(self):
        with self.assertRaises(ValueError):
            vendor('unrecognized source code')
        with self.assertRaises(ValueError):
            preview('unrecognized source code')


if __name__ == '__main__':
    unittest.main()
