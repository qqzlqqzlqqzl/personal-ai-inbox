"""Integration tests against deployed code, using isolated temporary state only."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SOURCES = (Path('/home/ubuntu/ai-news/src'), Path('/home/ubuntu/website-release-20261004/reader-3349cf2-source/src'))


@unittest.skipUnless(all((root/'runtime_resilience.py').exists() for root in SOURCES), 'requires deployed Linux sources')
class DeployedRuntimeTests(unittest.TestCase):
    def test_both_source_trees_preserve_feed_and_reset_backoff_after_success(self):
        import httpx
        for index, root in enumerate(SOURCES):
            with self.subTest(source=str(root)), tempfile.TemporaryDirectory() as temporary:
                sys.path.insert(0, str(root))
                spec = importlib.util.spec_from_file_location(f'isolated_vendor_{index}', root/'vendor_sources.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.ROOT = Path(temporary)
                fixture = '<section><a href="/cn/technology/casestudy/test">Article</a></section>'
                good = lambda _: fixture
                self.assertEqual(module.refresh('nidec', fetch=good, now=1000000)['state'], 'ok')
                before = module.load('nidec')['entries']
                def blocked(_):
                    response = httpx.Response(403, request=httpx.Request('GET', 'https://example.com/?token=secret'))
                    response.raise_for_status()
                for attempt, delay in [(1, 21600), (2, 43200)]:
                    now = 1000000+attempt
                    module.refresh('nidec', force=True, fetch=blocked, now=now)
                    saved = module.load('nidec')
                    self.assertEqual(saved['entries'], before)
                    self.assertEqual(saved['consecutive_failures'], attempt)
                    self.assertEqual(saved['next_run_at']-now, delay)
                    self.assertNotIn('secret', str(saved['last_error']))
                module.refresh('nidec', force=True, fetch=good, now=1000010)
                self.assertEqual(module.load('nidec')['consecutive_failures'], 0)
                self.assertIsNone(module.load('nidec')['last_error'])
                sys.path.pop(0)


if __name__ == '__main__':
    unittest.main()
