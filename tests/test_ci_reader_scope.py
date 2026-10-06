"""Only selector/CI wiring tests; tiny Git fixtures never run product suites."""
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ci_reader_scope import BACKUP, FULLTEXT, IMAGES, IMAGE_HISTORY, IMAGE_HELPER, IMAGE_KEY, PIN_FILE, PERFORMANCE, PYTHON_TESTS, classify, pin_only, select

ROOT = Path(__file__).resolve().parents[1]


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='reader-scope-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        self.write(IMAGE_HISTORY, '{}')
        self.write(IMAGE_HELPER, '// original image helper\n')
        for path in FULLTEXT | BACKUP | {'src/api.py', 'src/notes_metadata.py', 'src/core.py'}:
            self.write(path, '# synthetic original\n')
        self.git('add', '.')
        src = self.git('rev-parse', self.git('write-tree')+':src')
        self.write(PIN_FILE, '# fixed fixture body\nSRC = "'+src+'"\nGUARD = True\n')
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'user.name=Scope fixture',
            '-c', 'user.email=scope@example.invalid', *args], cwd=self.root,
            stderr=subprocess.DEVNULL, text=True, timeout=10).strip()

    def write(self, path, value):
        target = self.root/path; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def commit(self, rebind=False):
        self.git('add', '.')
        if rebind:
            src = self.git('rev-parse', self.git('write-tree')+':src')
            p = self.root/PIN_FILE
            p.write_text(re.sub(r'SRC = "[a-f0-9]{40}"', 'SRC = "'+src+'"', p.read_text()))
            self.git('add', '.')
        self.git('commit', '-qm', 'synthetic change')
        return self.git('rev-parse', 'HEAD')

    def pr(self, head, base=None):
        return select(self.root, 'pull_request', {'pull_request': {
            'base': {'sha': base or self.base}, 'head': {'sha': head}}}, head)

    def test_actual_fulltext_pattern_and_exact_src_pin(self):
        self.write('src/kaggle_batch/fulltext_source.py', '# updated extractor\n')
        self.write('src/kaggle_batch/test_fulltext_source.py', '# updated source test\n')
        head = self.commit(rebind=True)
        result = self.pr(head)
        self.assertEqual(result['scope'], 'fulltext')
        self.assertTrue(result['verified_src_pin_only'])
        self.assertFalse(result['native'])
        self.assertEqual(len(result['changes']), 3)

    def test_backup_two_files_select_operational_module(self):
        self.assertEqual(classify([{'path': 'src/reader_image_proxy.py', 'status': 'M'}]), 'images')
        for path in BACKUP: self.write(path, '# changed backup\n')
        result = self.pr(self.commit())
        self.assertEqual(result['scope'], 'backup')
        self.assertEqual(PYTHON_TESTS['backup'], ['tests/test_operational_cli_admission.py'])

    def test_actual_date_native_pattern_keeps_interface_and_native_gates(self):
        self.assertEqual(classify([{'path': 'src/reader_image_proxy.py', 'status': 'M'},
                                  {'path': 'src/api.py', 'status': 'M'}]), 'full')
        paths = ['src/api.py', 'src/notes_metadata.py', 'tests/test_notes_metadata.py',
                 'tests/test_notes_pair_metadata_url_contract.py', 'tests/notes_pair_acceptance.py',
                 'ops/miniflux-metadata/miniflux-2.3.3-entry-metadata.patch',
                 'ops/miniflux-metadata/pins.json',
                 'ops/miniflux-metadata/tests/internal/api/entry_metadata_postgres_test.go',
                 'ops/miniflux-metadata/tests/internal/storage/entry_metadata_test.go']
        for path in paths: self.write(path, '# changed date/native contract\n')
        result = self.pr(self.commit(rebind=True))
        self.assertEqual(result['scope'], 'interfaces')
        self.assertTrue(result['native'])
        self.assertTrue(result['verified_src_pin_only'])

    def test_unknown_and_cross_module_changes_select_full(self):
        self.assertEqual(classify([{'path': IMAGE_HISTORY, 'status': 'M'}]), 'full')
        for paths in [[], ['config.json'], ['src/core.py'],
                      ['src/backup.py', 'src/api.py'],
                      ['src/kaggle_batch/fulltext_source.py', 'src/api.py'],
                      ['.github/workflows/reader-regression.yml']]:
            with self.subTest(paths=paths):
                self.assertEqual(classify([{'path': p, 'status': 'M'} for p in paths]), 'full')

    def test_documentation_and_mixed_code_from_complete_event_diff(self):
        self.assertEqual(PYTHON_TESTS['images'], [
            'tests/test_reader_image_proxy.py', 'tests/test_reader_image_cache.py',
            'tests/test_reader_cover_proxy.py', 'tests/test_api.py',
            'tests/test_ci_reader_scope.py',
            'tests/test_frontend_overlay_rebuild.py', 'tests/test_source_catalog_overlay.py',
        ])
        self.write('README.md', '# usage update\n')
        self.write('docs/ops/PRODUCT-ACCEPTANCE.md', '# acceptance update\n')
        head = self.commit()
        result = self.pr(head)
        self.assertEqual(result['scope'], 'docs')
        self.assertEqual(result['reason'], 'documentation_only')
        self.assertFalse(result['native'])
        self.assertEqual(select(self.root, 'push', {'before': self.base, 'after': head}, head)['scope'], 'docs')
        self.write('src/backup.py', '# backup update with documentation\n')
        self.assertEqual(self.pr(self.commit())['scope'], 'backup')
        self.write('src/backup.py', '# synthetic original\n')
        self.write('src/api.py', '# interface update with documentation\n')
        result = self.pr(self.commit())
        self.assertEqual(result['scope'], 'interfaces')
        self.assertTrue(result['native'])
        self.write('src/core.py', '# shared logic update\n')
        self.assertEqual(self.pr(self.commit())['scope'], 'full')
        for row in ({'path': 'docs/ops/guide.py', 'status': 'M'},
                    {'path': 'docs/ops/guide.md', 'status': 'D'},
                    {'path': '.github/workflows/reader-regression.yml', 'status': 'M'}):
            self.assertEqual(classify([row]), 'full')

    def test_image_pipeline_ci_support_needs_real_owned_image_changes(self):
        support = [
            {'path': path, 'status': 'M'} for path in (
                'tests/ci_reader_scope.py', 'tests/test_ci_reader_scope.py',
                '.github/workflows/reader-regression.yml',
            )
        ]
        self.assertEqual(classify(support), 'full')
        owned = [{'path': 'patches/ProgressiveLoadMore.jsx', 'status': 'M'},
                 {'path': 'src/reader_image_cache.py', 'status': 'A'}]
        self.assertEqual(classify(owned + support), 'images')
        self.assertEqual(classify(owned + support + [
            {'path': 'src/api.py', 'status': 'M'}]), 'full')
        self.assertEqual(classify(owned + support + [
            {'path': 'src/core.py', 'status': 'M'}]), 'full')

    def test_image_prefetch_composition_paths_keep_unrelated_fallback(self):
        affected = {
            'src/patch_frontend.py', 'tests/test_frontend_overlay_rebuild.py',
            'tests/test_source_catalog_overlay.py',
        }
        self.assertTrue(affected <= IMAGES)
        changes = [{'path': path, 'status': 'M'} for path in affected]
        self.assertEqual(classify(changes), 'images')
        for path in ('src/api.py', 'src/core.py', 'src/patch_scope_ai_filters.py',
                     'tests/test_frontend_overlay_unreviewed.py'):
            with self.subTest(path=path):
                self.assertEqual(classify(changes + [{'path': path, 'status': 'M'}]), 'full')
        for status in ('D', 'T', 'R100'):
            self.assertEqual(classify([{'path': 'src/patch_frontend.py', 'status': status}]), 'full')

    def test_performance_allowlist_and_python_contracts_are_exact(self):
        self.assertEqual(PERFORMANCE, {
            'tests/fixtures/reader-loading-product-ab-inputs.json',
            'tests/test_reader_product_ab_profile.py',
            'tests/reader_loading_ab_ci.py',
            'tests/test_reader_loading_ab_ci.py',
        })
        self.assertEqual(PYTHON_TESTS['performance'], [
            'tests/test_reader_product_ab_profile.py',
            'tests/test_reader_loading_ab_ci.py',
        ])
        for size in range(1, len(PERFORMANCE) + 1):
            for paths in itertools.combinations(sorted(PERFORMANCE), size):
                for status in ('A', 'M'):
                    with self.subTest(paths=paths, status=status):
                        changes = [{'path': p, 'status': status} for p in paths]
                        self.assertEqual(classify(changes), 'performance')
                        self.assertEqual(classify(changes + [
                            {'path': 'docs/ops/benchmark.md', 'status': 'M'}]), 'performance')

    def test_performance_mixed_product_unknown_workflow_and_structural_changes_are_full(self):
        benchmark = {'path': 'tests/reader_loading_ab_ci.py', 'status': 'M'}
        other_paths = FULLTEXT | BACKUP | IMAGES | {
            'src/api.py', 'src/core.py', PIN_FILE, 'requirements.dev.lock.txt',
            'tests/ci_reader_scope.py', 'tests/test_ci_reader_scope.py',
            '.github/workflows/reader-regression.yml', '.github/workflows/reader-loading-ab.yml',
            'tests/fixtures/reader-loading-ab-inputs.json', 'tests/reader_loading_ci.py',
            'tests/reader_loading_performance.py', 'tests/test_reader_loading_future.py',
        }
        for path in other_paths:
            with self.subTest(path=path):
                self.assertEqual(classify([benchmark, {'path': path, 'status': 'M'}],
                                          verified_pin=True, verified_image_history=True), 'full')
        for path in PERFORMANCE:
            for status in ('D', 'T', 'R100', 'C100', 'U'):
                with self.subTest(path=path, status=status):
                    self.assertEqual(classify([{'path': path, 'status': status}]), 'full')

    def test_performance_complete_event_diff_routes_only_benchmark_changes(self):
        for path in PERFORMANCE:
            self.write(path, '# synthetic benchmark baseline\n')
        base = self.commit()
        for path in PERFORMANCE:
            self.write(path, '# synthetic benchmark update\n')
        head = self.commit()
        events = [
            ('pull_request', {'pull_request': {'base': {'sha': base}, 'head': {'sha': head}}}),
            ('push', {'before': base, 'after': head}),
            ('workflow_dispatch', {'inputs': {'scope': 'auto', 'base_sha': base}}),
        ]
        for event_name, event in events:
            with self.subTest(event=event_name):
                result = select(self.root, event_name, event, head)
                self.assertEqual(result['scope'], 'performance')
                self.assertFalse(result['native'])
                self.assertEqual({row['path'] for row in result['changes']}, PERFORMANCE)
                self.assertEqual({row['status'] for row in result['changes']}, {'M'})
        self.assertEqual(select(self.root, 'workflow_dispatch', {}, head)['scope'], 'full')
        # The whole push includes this earlier product change, despite a final benchmark commit.
        self.write('src/core.py', '# changed product\n'); self.commit()
        self.write('tests/reader_loading_ab_ci.py', '# later benchmark update\n')
        head = self.commit()
        self.assertEqual(select(self.root, 'push', {'before': base, 'after': head}, head)['scope'], 'full')

    def test_performance_git_add_delete_and_rename_fail_closed_when_structural(self):
        path = 'tests/reader_loading_ab_ci.py'
        self.write(path, '# added benchmark\n')
        added = self.commit()
        self.assertEqual(self.pr(added)['scope'], 'performance')
        self.git('mv', path, 'tests/test_reader_loading_ab_ci.py')
        renamed = self.commit()
        self.assertEqual(self.pr(renamed, added)['scope'], 'full')
        self.git('rm', 'tests/test_reader_loading_ab_ci.py')
        self.assertEqual(self.pr(self.commit(), renamed)['scope'], 'full')

    def test_bad_or_additional_fixture_logic_is_not_ignored(self):
        self.write('src/kaggle_batch/fulltext_source.py', '# changed\n')
        self.write(PIN_FILE, 'SRC = "'+'f'*40+'"\nGUARD = False\n')
        self.assertEqual(self.pr(self.commit())['scope'], 'full')

    def test_only_literal_change_is_accepted_by_pin_exception(self):
        old, new = b'a'*40, b'b'*40
        before = b'SRC = "'+old+b'"\nGUARD = True\n'
        after = b'SRC = "'+new+b'"\nGUARD = True\n'
        self.assertTrue(pin_only(before, after, old.decode(), new.decode()))
        for bad in [after+b'# changed logic too\n', after.replace(b'True', b'False'),
                    after+after.splitlines(keepends=True)[0], after.replace(new, b'c'*40)]:
            self.assertFalse(pin_only(before, bad, old.decode(), new.decode()))
        self.assertFalse(pin_only(before, after, 'd'*40, new.decode()))

    def test_rename_records_both_paths_and_fails_closed(self):
        self.git('mv', 'src/kaggle_batch/fulltext_source.py', 'src/renamed-extractor.py')
        result = self.pr(self.commit())
        self.assertEqual(result['scope'], 'full')
        self.assertEqual({x['path'] for x in result['changes']},
                         {'src/kaggle_batch/fulltext_source.py','src/renamed-extractor.py'})
        self.assertIn('D', {x['status'] for x in result['changes']})

    def test_deleted_test_or_type_change_cannot_reduce_scope(self):
        for status in ('D','T','R100','C100','U'):
            self.assertEqual(classify([{'path':'src/kaggle_batch/test_fulltext_source.py','status':status}]), 'full')

    def test_pr_diff_uses_merge_base_and_excludes_base_only_changes(self):
        self.git('checkout', '-qb', 'topic')
        self.write('src/backup.py', '# backup change\n'); head = self.commit()
        self.git('checkout', '--detach', self.base)
        self.write('src/core.py', '# base-only core change\n'); advanced_base = self.commit()
        self.git('checkout', 'topic')
        result = self.pr(head, advanced_base)
        self.assertEqual(result['scope'], 'backup')
        self.assertEqual(result['comparison_base'], self.base)

    def test_push_compares_whole_before_after_not_last_commit(self):
        self.write('src/core.py', '# earlier pushed core change\n'); self.commit()
        self.write('src/backup.py', '# last pushed backup change\n'); head = self.commit()
        result = select(self.root, 'push', {'before': self.base, 'after': head}, head)
        self.assertEqual(result['scope'], 'full')
        self.assertEqual({x['path'] for x in result['changes']}, {'src/core.py','src/backup.py'})

    def test_force_push_net_changes_include_reverted_core(self):
        self.write('src/core.py', '# replaced branch core\n'); before = self.commit()
        self.git('checkout', '--detach', self.base)
        self.write('src/backup.py', '# force-pushed branch\n'); head = self.commit()
        result = select(self.root, 'push', {'before': before, 'after': head}, head)
        self.assertEqual(result['scope'], 'full')
        self.assertIn('src/core.py', {x['path'] for x in result['changes']})

    def test_missing_zero_wrong_and_unavailable_revisions_select_full(self):
        self.write('src/backup.py', '# changed\n'); head = self.commit()
        for event in [{}, {'before': '0'*40, 'after': head},
                      {'before': 'f'*40, 'after': head},
                      {'before': self.base, 'after': self.base}]:
            self.assertEqual(select(self.root, 'push', event, head)['scope'], 'full')
        self.assertEqual(self.pr(self.base)['reason'], 'checkout_identity_mismatch')

    def test_manual_full_and_missing_baseline_are_full(self):
        self.write('src/backup.py', '# changed\n'); head = self.commit()
        for inputs in ({}, {'scope':'full'}, {'scope':'auto'}, {'scope':'backup','base_sha':self.base}):
            self.assertEqual(select(self.root,'workflow_dispatch',{'inputs':inputs},head)['scope'],'full')
        self.assertEqual(select(self.root,'workflow_dispatch',
                         {'inputs':{'scope':'auto','base_sha':self.base}},head)['scope'],'backup')

    def test_dirty_checkout_or_diff_failure_fails_closed(self):
        self.write('src/backup.py', '# dirty\n')
        self.assertEqual(self.pr(self.base)['scope'], 'full')
        with patch('ci_reader_scope.git', side_effect=OSError('synthetic unavailable git')):
            self.assertEqual(self.pr(self.base)['scope'], 'full')


    def test_image_scope_requires_exact_history_and_src_binding(self):
        old_hash = hashlib.sha256(subprocess.check_output(
            ['git', 'show', self.base+':'+IMAGE_HELPER], cwd=self.root, timeout=10)).hexdigest()
        for path in IMAGES - {IMAGE_HISTORY}: self.write(path, '// updated image source\n')
        self.write(IMAGE_HISTORY, json.dumps({IMAGE_KEY: [old_hash]}))
        result = self.pr(self.commit(rebind=True))
        self.assertEqual(result['scope'], 'images')
        self.assertTrue(result['verified_src_pin_only'])
        self.assertFalse(result['native'])
        for history in ({IMAGE_KEY: ['b'*64]}, {IMAGE_KEY: [old_hash], 'other/component': ['a'*64]}):
            self.write(IMAGE_HISTORY, json.dumps(history))
            self.assertEqual(self.pr(self.commit())['scope'], 'full')


class WiringTests(unittest.TestCase):
    def test_full_job_is_exact_original_after_removing_scope_wiring_and_anchors(self):
        text = (ROOT/'.github/workflows/reader-regression.yml').read_text()
        block = text[text.index('  full-regression:'):text.index('\n  scope-job:')].rstrip()+'\n'
        block = block.replace('  full-regression:\n', '  offline-and-browser:\n', 1)
        block = re.sub(r'^    needs: scope-job\n', '', block, count=1, flags=re.M)
        block = re.sub(r'^    if: .*\n', '', block, count=1, flags=re.M)
        block = block.replace('    env: &reader_env\n','    env:\n')
        block = re.sub(r'^      - &[a-z_]+\n        ', '      - ', block, flags=re.M)
        self.assertEqual(hashlib.sha256(block.encode()).hexdigest(), '7c7a1f0e3211d8bd682f17f2c6d448803f1a4a684783ceded97b8dc1e0646e3e')

    def test_interface_browser_contracts_and_focused_python_inventory_are_present(self):
        text = (ROOT/'.github/workflows/reader-regression.yml').read_text()
        interface = text[text.index('  interfaces:'):text.index('  native-interface:')]
        for anchor in ('related_python','query_browser','notes_browser','history_browser',
                       'cjk_verify','calendar_browser','calendar_identity','verify_identity'):
            self.assertIn('- *'+anchor, interface)
        for paths in PYTHON_TESTS.values():
            for path in paths: self.assertTrue((ROOT/path).is_file(), path)
        self.assertIn("github.event_name != 'pull_request'", text)
        self.assertNotIn('continue-on-error:', text)
        self.assertNotIn('HEAD^', text)

    def test_performance_reuses_python_only_job_and_preserves_full_fallback(self):
        text = (ROOT/'.github/workflows/reader-regression.yml').read_text()
        focused = text[text.index('  python-focused:'):text.index('  interfaces:')]
        self.assertIn("needs.scope-job.outputs.scope == 'performance'", focused)
        self.assertIn('runs-on: ubuntu-22.04', focused)
        self.assertIn("targets = PYTHON_TESTS[os.environ['READER_CI_SCOPE']]", focused)
        self.assertIn("['-q', *targets, '--junitxml=artifacts/ci-python.xml']", focused)
        for name in ('prepare_evidence', 'setup_python', 'upload_evidence'):
            self.assertIn('- *' + name, focused)
        for name in ('setup_node', 'install_dependencies', 'prepare_reader', 'build_reader',
                     'chromium_os', 'chromium', 'fulltext_browser'):
            self.assertNotIn('- *' + name + '\n', focused)
        self.assertNotIn('playwright install', focused)
        full = text[text.index('  full-regression:'):text.index('    runs-on:')]
        self.assertIn("needs.scope-job.result != 'success'", full)
        accepted = json.loads(re.search(r"fromJSON\('([^']+)'\)", full)[1])
        self.assertEqual(set(accepted), {'docs', 'fulltext', 'backup', 'interfaces', 'images', 'performance'})
        self.assertIn('!contains(', full)

    def test_original_required_gate_rejects_failed_skipped_or_cancelled_selected_job(self):
        text = (ROOT/'.github/workflows/reader-regression.yml').read_text()
        block = text[text.index('      - name: Require the selected regression to succeed'):]
        code = block.split("python3 - <<'PY'\n",1)[1].rsplit('          PY',1)[0]
        code = '\n'.join(line[10:] for line in code.splitlines())
        baseline = {'SELECT_RESULT':'success','SELECT_SCOPE':'backup', 'PYTHON_RESULT':'success',
                    'IMAGE_RESULT':'skipped',
                    'FULL_RESULT':'skipped','INTERFACE_RESULT':'skipped','NATIVE_REQUIRED':'false',
                    'NATIVE_RESULT':'skipped','GITHUB_EVENT_NAME':'pull_request'}
        with patch.dict(os.environ, baseline, clear=True): exec(code, {})
        for outcome in ('failure','skipped','cancelled',''):
            with patch.dict(os.environ, {**baseline,'PYTHON_RESULT':outcome}, clear=True):
                with self.assertRaises(AssertionError): exec(code, {})
        with patch.dict(os.environ,{**baseline,'SELECT_RESULT':'failure','FULL_RESULT':'success'},clear=True):
            exec(code,{})
        with patch.dict(os.environ,{**baseline,'GITHUB_EVENT_NAME':'push','NATIVE_REQUIRED':'true'},clear=True):
            with self.assertRaises(AssertionError): exec(code,{})
        performance = {**baseline, 'SELECT_SCOPE': 'performance'}
        with patch.dict(os.environ, performance, clear=True): exec(code, {})
        for outcome in ('failure', 'skipped', 'cancelled', ''):
            with patch.dict(os.environ, {**performance, 'PYTHON_RESULT': outcome,
                                        'FULL_RESULT': 'success'}, clear=True):
                with self.assertRaises(AssertionError): exec(code, {})
        with patch.dict(os.environ, {**performance, 'SELECT_RESULT': 'failure'}, clear=True):
            with self.assertRaises(AssertionError): exec(code, {})
        docs = {**baseline, 'SELECT_SCOPE': 'docs', 'PYTHON_RESULT': 'skipped'}
        with patch.dict(os.environ, docs, clear=True): exec(code, {})
        for outcome in ('failure', 'skipped', 'cancelled', ''):
            with patch.dict(os.environ, {**docs, 'SELECT_RESULT': outcome}, clear=True):
                with self.assertRaises(AssertionError): exec(code, {})
        for key in ('FULL_RESULT', 'PYTHON_RESULT', 'INTERFACE_RESULT', 'IMAGE_RESULT', 'NATIVE_RESULT'):
            for outcome in ('success', 'failure'):
                with patch.dict(os.environ, {**docs, key: outcome}, clear=True):
                    with self.assertRaises(AssertionError): exec(code, {})
        with patch.dict(os.environ, {**docs, 'NATIVE_REQUIRED': 'true'}, clear=True):
            with self.assertRaises(AssertionError): exec(code, {})
        image = {**docs, 'SELECT_SCOPE': 'images', 'IMAGE_RESULT': 'success'}
        with patch.dict(os.environ, image, clear=True): exec(code, {})
        for outcome in ('failure', 'skipped', 'cancelled', ''):
            with patch.dict(os.environ, {**image, 'IMAGE_RESULT': outcome}, clear=True):
                with self.assertRaises(AssertionError): exec(code, {})


if __name__ == '__main__':
    unittest.main()
