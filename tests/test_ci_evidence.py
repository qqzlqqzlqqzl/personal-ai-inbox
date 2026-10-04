"""Synthetic CI evidence gates. Keep every fixture and prior output on disk."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ci_prepare_evidence import UPLOAD_PATHS, matches, prepare


class ReaderEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(tempfile.mkdtemp(prefix='reader-ci-evidence-retained-'))
        self.root = self.fixture / 'repo'
        self.root.mkdir()
        self.retained = self.fixture / 'retained'
        self.retained.mkdir()
        (self.root / 'src').mkdir()
        (self.root / 'src/core.py').write_text('synthetic = True\n')
        (self.root / 'artifacts').mkdir()
        (self.root / 'artifacts/historical.json').write_text('{"old":true}\n')
        self.git('init', '--quiet')
        self.git('add', 'src/core.py', 'artifacts/historical.json')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '--quiet', '-m', 'synthetic CI evidence fixture')
        self.head = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, text=True, timeout=10).strip()

    def test_old_outputs_move_and_historical_tracked_files_stay(self):
        old = self.root / 'runtime/browser-build'
        old.mkdir(parents=True)
        (old / 'index.html').write_bytes(b'old synthetic output')
        receipt = prepare(self.root, self.retained, self.head)
        self.assertFalse(old.exists())
        self.assertEqual((Path(receipt['retained_at']) / 'runtime/browser-build/index.html').read_bytes(), b'old synthetic output')
        self.assertEqual((self.root / 'artifacts/historical.json').read_text(), '{"old":true}\n')
        self.assertEqual(receipt['head'], self.head)

    def test_tracked_upload_overlap_fails_before_moves(self):
        with self.assertRaisesRegex(RuntimeError, 'overlap tracked'):
            prepare(self.root, self.retained, self.head, patterns=('artifacts/',))
        self.assertFalse((self.root / 'artifacts/ci-tested-source.json').exists())
        self.assertEqual(list(self.retained.iterdir()), [])

    def test_wrong_head_fails_before_mutation(self):
        with self.assertRaisesRegex(RuntimeError, 'immutable checkout'):
            prepare(self.root, self.retained, '0' * 40)
        self.assertEqual(list(self.retained.iterdir()), [])

    def test_symlink_upload_refused_without_touching_target(self):
        actual = self.fixture / 'existing-output'
        actual.mkdir()
        (actual / 'sentinel').write_text('retained')
        (self.root / 'runtime').mkdir()
        (self.root / 'runtime/browser-build').symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'Symlink'):
            prepare(self.root, self.retained, self.head)
        self.assertEqual((actual / 'sentinel').read_text(), 'retained')

    def test_repeat_keeps_prior_receipt(self):
        first = prepare(self.root, self.retained, self.head)
        second = prepare(self.root, self.retained, self.head)
        prior = json.loads((Path(second['retained_at']) / 'artifacts/ci-tested-source.json').read_text())
        self.assertEqual(prior, first)

    def test_workflow_prepares_before_dependencies_and_gates_upload(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/reader-regression.yml').read_text()
        self.assertLess(workflow.index('id: prepare_evidence'), workflow.index('uses: actions/setup-python@'))
        self.assertIn("if: always() && steps.prepare_evidence.outcome == 'success'", workflow)
        for path in UPLOAD_PATHS:
            self.assertIn('            ' + path, workflow)
        self.assertTrue(matches('runtime/note-legacy-migration/result.json', 'runtime/note-legacy-*/'))
        self.assertFalse(matches('artifacts/historical.json', 'artifacts/ci-python.xml'))

    def test_query_and_reading_browser_entries_are_mandatory(self):
        from ci_reader_contract import BROWSER_TESTS
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/reader-regression.yml').read_text()
        for name in ('query_result_ownership_browser.py', 'reading_focus_browser.py'):
            self.assertIn(name, BROWSER_TESTS)
            self.assertEqual(workflow.count('600s python tests/' + name), 1)
        self.assertNotIn('continue-on-error:', workflow)
        self.assertNotIn('if: hashFiles', workflow)

    def test_query_and_reading_prior_outputs_are_retained_before_upload(self):
        folders = ['query-result-ownership'] + [f'reading-focus-{width}-{theme}'
                    for width in (1440, 390) for theme in ('light', 'dark')]
        for folder in folders:
            path = self.root / 'runtime' / folder
            path.mkdir(parents=True)
            (path / 'result.json').write_bytes(b'{"old_synthetic":true}')
        receipt = prepare(self.root, self.retained, self.head)
        for folder in folders:
            relative = 'runtime/' + folder
            self.assertIn(relative, receipt['previous_outputs_retained'])
            self.assertFalse((self.root / relative).exists())
            self.assertEqual((Path(receipt['retained_at']) / relative / 'result.json').read_bytes(),
                             b'{"old_synthetic":true}')


if __name__ == '__main__':
    unittest.main()
