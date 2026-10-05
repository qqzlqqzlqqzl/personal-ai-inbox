"""Fail closed on wrong Reader CI identity, dependency pins or missing test inputs."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REACTFLUX = '534eeb97723ac11025de4ec1ac56335072e3be52'
HISTORY = ('0d77969743868cd4ff859a4aec2784115c8c49c8',
           'da5a6222779517b92782bf19e2a2166801676fb4',
           '8faaefaf58fa8aff6c598bb5e29c8a3b2224784c')
NODE_BASELINE = ('test_agent_status_cache.mjs', 'test_agent_status_client.mjs', 'test_agent_status_dom.mjs', 'test_agent_status_view.mjs', 'test_ai_pagination.mjs', 'test_ai_scope_state.mjs', 'test_bulk_read_label.mjs', 'test_calendar_lifecycle.mjs', 'test_calendar_retry_and_starred.mjs', 'test_fetch_content.mjs', 'test_kaggle_status.mjs', 'test_note_request_deadline.mjs', 'test_note_session_integration.mjs', 'test_note_session_lifecycle.mjs', 'test_reader_acceptance_gaps.mjs', 'test_reading_calendar.mjs', 'test_reading_session.mjs', 'test_review_reading.mjs', 'test_review_resource_components.mjs', 'test_review_workflows.mjs', 'test_search_focus_component.mjs', 'test_sort_component.mjs', 'test_source_history.mjs', 'test_storage_event_acceptance_gap.mjs')
BROWSER_TESTS = (
    'fulltext_browser_admission.py', 'cjk_font_browser_acceptance.py',
    'history_browser_acceptance.py', 'review_console_browser.py',
    'review_reading_browser.py', 'note_session_browser_acceptance.py',
    'note_request_recovery_browser.py', 'note_legacy_browser_acceptance.py',
    'search_ime_browser_audit.py', 'sort_browser_acceptance.py',
    'reader_panel_transition_browser.py', 'mobile_reading_browser.py',
    'calendar_browser_acceptance.py', 'calendar_identity_browser.py',
    'dev_fixture_workspace_browser_acceptance.py',
    'navigation_focus_transition_browser.py',
    'query_result_ownership_browser.py', 'reading_focus_browser.py',
    'console_header_browser.py', 'native_zoom_browser.py',
    'quality_consumer_browser.py',
    'source_catalog_browser.py',
)


def command(*args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True, timeout=15).strip()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    require(ROOT != Path('/home/ubuntu/ai-news') and not (ROOT / '.private').exists(),
            'CI contract refuses deployment/private configuration')
    require(not os.environ.get('CHROMIUM_EXECUTABLE'), 'Chromium executable override is not permitted in CI')
    head = command('git', 'rev-parse', 'HEAD')
    require(head == args.expected_head, 'Checkout differs from the requested immutable tested commit')
    require(not command('git', 'diff', '--name-only', 'HEAD'), 'Tracked CI input changed before regression')
    require(sys.version_info[:3] == (3, 12, 14), 'Expected Python 3.12.14')
    require(command('node', '--version') == 'v24.21.0', 'Expected Node 24.21.0')
    require(command('pnpm', '--version') == '11.21.0', 'Expected pnpm 11.21.0')
    dependency_versions = {}
    for line in (ROOT / 'requirements.dev.lock.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, expected = line.split('==')
        actual = metadata.version(name)
        require(actual == expected, 'Locked Python dependency mismatch: ' + name)
        dependency_versions[name] = actual
    for ref in HISTORY:
        command('git', 'cat-file', '-e', ref + '^{commit}')
    upstream = ROOT / 'upstream/reactflux'
    require(command('git', 'rev-parse', 'HEAD', cwd=upstream) == REACTFLUX, 'Wrong ReactFlux source pin')
    lock_sha = hashlib.sha256((upstream / 'pnpm-lock.yaml').read_bytes()).hexdigest()
    require(lock_sha == '39c940c62fb66b77e524d30ae7b1dfcd1fb97ac73ceb95722757ed36e79b606b',
            'ReactFlux frozen lock changed')
    import playwright
    browsers = json.loads((Path(playwright.__file__).parent / 'driver/package/browsers.json').read_text())
    chromium = {row['name']: row for row in browsers['browsers']
                if row['name'] in ('chromium', 'chromium-headless-shell')}
    require(set(chromium) == {'chromium', 'chromium-headless-shell'}, 'Missing pinned Chromium descriptors')
    require(all(row['revision'] == '1243' and row['browserVersion'] == '153.0.8010.12'
                for row in chromium.values()), 'Unexpected Playwright Chromium revision')
    for name in BROWSER_TESTS:
        require((ROOT / 'tests' / name).is_file(), 'Required browser test missing: ' + name)
    nodes = sorted(path.name for path in (ROOT / 'tests').glob('test_*.mjs'))
    require(set(NODE_BASELINE).issubset(nodes), 'Required baseline Node/component test is missing')
    require((ROOT / 'tests/source_history_component_acceptance.mjs').is_file(), 'History component test missing')
    report = {'head': head, 'tree': command('git', 'rev-parse', 'HEAD^{tree}'),
              'src_tree': command('git', 'rev-parse', 'HEAD:src'), 'python': sys.version.split()[0],
              'node': '24.21.0', 'pnpm': '11.21.0', 'python_dependencies': dependency_versions,
              'reactflux': REACTFLUX, 'pnpm_lock_sha256': lock_sha,
              'chromium_revision': '1243', 'chromium_version': '153.0.8010.12',
              'browser_test_inventory': list(BROWSER_TESTS), 'node_test_inventory': nodes,
              'browser_launch': 'not claimed by this static identity check', 'passed': True}
    output = ROOT / 'artifacts/ci-reader-identity.json'
    output.parent.mkdir(exist_ok=True)
    with output.open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
