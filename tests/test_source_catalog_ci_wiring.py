"""Mandatory consumer wiring and actual shared-producer capabilities."""
import ast
import copy
from pathlib import Path

from ci_prepare_evidence import UPLOAD_PATHS, matches
from ci_reader_contract import BROWSER_TESTS
from reader_feed_fixture_contract import seed_harness
from vendor_catalog import annotate_subscription_support, summary_policy_ready, SUMMARY_FEEDS

ROOT = Path(__file__).resolve().parents[1]


def test_source_catalog_is_required_in_identity_and_upload_contracts():
    assert 'source_catalog_browser.py' in BROWSER_TESTS
    assert 'runtime/source-catalog/' in UPLOAD_PATHS
    for context in ('1440x960-light', '1440x960-dark', '390x844-light', '390x844-dark'):
        for file in ('calls.json', 'result.json', 'failure.png', 'stale-archive.png', 'unknown-archive.png'):
            assert any(matches(f'runtime/source-catalog/{context}/{file}', pattern) for pattern in UPLOAD_PATHS)


def test_source_catalog_is_an_unconditional_workflow_step_with_artifacts():
    workflow = (ROOT / '.github/workflows/reader-regression.yml').read_text()
    block = next(block for block in workflow.split('      - name: ')
                 if 'python tests/source_catalog_browser.py' in block)
    assert 'AI_NEWS_TEST_BUILD: runtime/browser-build' in block
    assert 'timeout --signal=TERM --kill-after=10s 600s python tests/source_catalog_browser.py' in block
    assert all(value not in block for value in ('if:', 'continue-on-error', '||'))
    assert '            runtime/source-catalog/\n' in workflow


def test_shared_catalog_has_own_capabilities_matching_real_projection():
    harness, cls = seed_harness()
    init = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '__init__')
    statement, = [node for node in init.body if isinstance(node, ast.Assign)
                  and len(node.targets) == 1 and ast.unparse(node.targets[0]) == 'self.catalog']
    exec(compile(ast.Module(body=[statement], type_ignores=[]), '<actual shared catalog>', 'exec'), {'self': harness})
    assert len(harness.catalog) == 31
    assert all(type(row.get('subscription_supported')) is bool for row in harness.catalog)
    expected = copy.deepcopy(harness.catalog)
    annotate_subscription_support(expected, summary_ready=False)
    assert harness.catalog == expected
    assert sum(row['subscription_supported'] for row in harness.catalog) == 29


def test_source_browser_keeps_four_contexts_sandbox_and_zero_write_checks():
    source = (ROOT / 'tests/source_catalog_browser.py').read_text()
    tree = ast.parse(source)
    loop = next(node for node in tree.body if isinstance(node, ast.For))
    assert ast.literal_eval(loop.iter) == [(1440, 960, 'light'), (1440, 960, 'dark'),
                                         (390, 844, 'light'), (390, 844, 'dark')]
    assert 'sandboxed=True' in source
    assert 'no_refresh_or_api_write' in source
    assert 'unverified_http_identity_cannot_enter_confirmation' in source
    assert 'live_project_collection_verified' in source


def test_current_backend_policy_and_restored_consumer_agree_without_io():
    import feed_consumption
    assert summary_policy_ready() is True
    assert SUMMARY_FEEDS == feed_consumption.SUMMARY_FEEDS
    rows = [{'url': url, 'status': 'ok'} for url in SUMMARY_FEEDS]
    annotate_subscription_support(rows, summary_ready=summary_policy_ready())
    for row in rows:
        assert row['subscription_supported'] is True
        assert row['analysis_supported'] is False
        assert feed_consumption.summary_feed_policy({'feed': {'feed_url': row['url']}}) == 'summary_only'
        assert feed_consumption.summary_feed_policy({'feed': {'feed_url': row['url'].replace('https:', 'http:')}}) == 'identity_unverified'
