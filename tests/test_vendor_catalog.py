"""Pure cache projections and actual read/catalog functions, without API startup."""
import ast
import asyncio
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from vendor_catalog import annotate_vendor_sources

URL = 'http://127.0.0.1:8092/internal/vendor-feeds/kickstarter.xml'
NOW = 1791158400
CONFIGS = {'kickstarter': {'name': '精选邮件', 'kind': 'newsletter'}}


def project(state, url=URL):
    rows = [{'url': url, 'status': 'ok', 'category': '产品与众筹'}]
    annotate_vendor_sources(rows, CONFIGS, {'kickstarter': state}, now=NOW)
    return rows[0]


def functions(filename, names, namespace):
    tree = ast.parse((ROOT / 'src' / filename).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    for n in nodes:
        n.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec'), namespace)
    return namespace


class VendorCatalogTests(unittest.TestCase):
    def test_cache_200_does_not_hide_upstream_failure_or_old_success(self):
        s = {'seeded': True, 'last_success_at': NOW - 30000, 'last_attempt_at': NOW - 60,
             'next_run_at': NOW + 1740, 'last_error': {'type': 'arbitrary-secret', 'at': NOW - 60, 'http_status': 429}}
        p = project(s)
        self.assertEqual(p['status'], 'ok')  # Reader RSS format succeeded, not the upstream collection.
        h = p['vendor_snapshot']
        self.assertEqual((h['freshness'], h['stale']), ('stale', True))
        self.assertEqual(h['last_error'], {'reason': 'upstream_error', 'at': NOW - 60, 'http_status': 429})
        self.assertNotIn('arbitrary-secret', json.dumps(p))

    def test_recent_error_and_freshness_are_independent(self):
        h = project({'seeded': True, 'last_success_at': NOW - 60, 'last_error': {'at': NOW}})['vendor_snapshot']
        self.assertEqual(h['freshness'], 'fresh')
        self.assertFalse(h['stale'])
        self.assertEqual(h['last_error']['reason'], 'upstream_error')

    def test_missing_invalid_and_unseeded_are_not_fresh(self):
        for state in (None, {}, {'state': 'invalid_state'}, {'seeded': 'true'}):
            with self.subTest(state=state):
                h = project(state)['vendor_snapshot']
                self.assertEqual(h['freshness'], 'unknown')
                self.assertFalse(h['state_available'])
        h = project({'seeded': False})['vendor_snapshot']
        self.assertEqual(h['freshness'], 'never_collected')
        self.assertIsNone(h['stale'])

    def test_contradictory_unseeded_success_remains_unknown(self):
        h = project({'seeded': False, 'last_success_at': NOW - 60})['vendor_snapshot']
        self.assertEqual(h['freshness'], 'unknown')
        self.assertFalse(h['state_available'])
        self.assertIsNone(h['last_success_at'])

    def test_bad_success_time_and_boundary(self):
        for value in (True, '1', math.nan, math.inf, 10 ** 400, -1, 0, NOW + 1):
            with self.subTest(value=value):
                h = project({'seeded': True, 'last_success_at': value})['vendor_snapshot']
                self.assertEqual(h['freshness'], 'unknown')
                self.assertIsNone(h['last_success_at'])
        self.assertEqual(project({'seeded': True, 'last_success_at': NOW - 21600})['vendor_snapshot']['freshness'], 'stale')

    def test_archive_identity_is_not_a_project_or_external_suffix(self):
        h = project({'seeded': True})['vendor_snapshot']
        self.assertEqual(h['source_kind'], 'newsletter_archive')
        self.assertEqual(h['coverage'], 'selected_newsletters')
        self.assertIs(h['project_links_available'], False)
        for url in ('https://www.brandsninja.com/brands/kickstarter/a',
                    'https://www.kickstarter.com/projects/example/project',
                    'https://example.test/internal/vendor-feeds/kickstarter.xml', URL + '?x=1',
                    'http://127.0.0.1:8092/internal/vendor-feeds/unknown.xml'):
            self.assertNotIn('vendor_snapshot', project({}, url))

    def test_catalog_only_changes_one_authored_category(self):
        rows = json.loads((ROOT / 'sources.catalog.json').read_text())
        row, = [x for x in rows if x['url'] == URL]
        self.assertEqual(row['category'], '产品与众筹')
        self.assertIn('项目详情与直达链接未取得', row['note'])
        self.assertFalse(row['original_text_verified'])

    def test_actual_read_only_status_creates_no_directory_or_network(self):
        # The real load/status code runs with synthetic cache files. No module
        # import, parser, Controller, refresh, or provider can be instantiated.
        root = Path(tempfile.mkdtemp(prefix='catalog-readonly-'))
        def forbidden(*args, **kwargs):
            raise AssertionError('read path attempted a mutation or fetch')
        ns = {'ROOT': root, 'CONFIGS': CONFIGS, 'Path': Path, 'json': json,
              'folder': forbidden, 'refresh': forbidden, 'fetch_text': forbidden,
              'allowed_url': lambda *_: URL, 'parsed_date': lambda _: 'date'}
        functions('vendor_sources.py', ['config', 'load', 'status'], ns)
        self.assertFalse((root / 'state').exists())
        with patch.object(Path, 'mkdir', side_effect=forbidden):
            state = ns['status'](read_only=True)
        self.assertFalse((root / 'state').exists())
        self.assertFalse(state['kickstarter']['seeded'])
        folder = root / 'state/vendor-adapters'; folder.mkdir(parents=True)
        path = folder / 'kickstarter.json'
        data = {'entries': [], 'seen': {}, 'seeded': True, 'last_success_at': NOW - 30000,
                'last_attempt_at': NOW, 'last_error': {'type': 'HTTPStatusError', 'at': NOW, 'http_status': 503}}
        path.write_text(json.dumps(data)); before = path.read_bytes()
        with patch.object(Path, 'mkdir', side_effect=forbidden), patch.object(Path, 'write_text', side_effect=forbidden):
            state = ns['status'](read_only=True)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(project(state['kickstarter'])['vendor_snapshot']['freshness'], 'stale')
        path.write_text('{')
        self.assertEqual(ns['status'](read_only=True)['kickstarter'], {'state': 'invalid_state'})

    def test_actual_catalog_route_reads_cache_and_preserves_actual_subscription_category(self):
        root = Path(tempfile.mkdtemp(prefix='catalog-route-'))
        (root / 'sources.catalog.json').write_text(json.dumps([{'name': '邮件归档', 'url': URL,
            'category': '产品与众筹', 'status': 'ok', 'note': '项目直达链接未取得'}]))
        calls = []
        async def authorize(request, **kwargs): calls.append(('authorize', kwargs))
        class Client:
            async def get(self, url, **kwargs):
                calls.append(('GET', url))
                assert url == 'http://mf.test/v1/feeds'
                return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: [
                    {'id': 7, 'feed_url': URL, 'category': {'title': '产品灵感'}, 'parsing_error_message': ''}])
        vendor = types.ModuleType('vendor_sources'); vendor.CONFIGS = CONFIGS
        def status(*, read_only=False):
            self.assertTrue(read_only); calls.append(('cache_status', read_only))
            return {'kickstarter': {'seeded': True, 'last_success_at': NOW - 30000,
                                    'last_error': {'http_status': 429, 'at': NOW - 60}}}
        vendor.status = status
        ns = {'ROOT': root, 'Path': Path, 'json': json, 'MF': 'http://mf.test', 'Request': object,
              'authorize': authorize, 'auth_headers': lambda _: {}, 'time': types.SimpleNamespace(time=lambda: NOW),
              'app': types.SimpleNamespace(state=types.SimpleNamespace(client=Client()))}
        functions('api.py', ['catalog'], ns)
        with patch.dict(sys.modules, {'vendor_sources': vendor}):
            rows = asyncio.run(ns['catalog'](object()))
        self.assertEqual([c[0] for c in calls], ['authorize', 'GET', 'cache_status'])
        self.assertEqual(rows[0]['subscription_category'], '产品灵感')
        self.assertEqual(rows[0]['category'], '产品与众筹')
        self.assertEqual(rows[0]['vendor_snapshot']['freshness'], 'stale')
        self.assertEqual(rows[0]['live_error'], '')
        self.assertEqual(rows[0]['vendor_snapshot']['last_error']['http_status'], 429)


if __name__ == '__main__':
    unittest.main()
