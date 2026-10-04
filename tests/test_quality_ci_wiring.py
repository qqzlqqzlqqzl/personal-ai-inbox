import ast
from pathlib import Path
import tempfile
import unittest

from ci_prepare_evidence import UPLOAD_PATHS, matches
from ci_reader_contract import BROWSER_TESTS

ROOT = Path(__file__).resolve().parents[1]


class QualityWiring(unittest.TestCase):
    def test_quality_is_unconditional_mandatory_step_and_identity_inventory(self):
        self.assertIn('quality_consumer_browser.py', BROWSER_TESTS)
        workflow = (ROOT / '.github/workflows/reader-regression.yml').read_text()
        block = next(block for block in workflow.split('      - name: ') if 'python tests/quality_consumer_browser.py' in block)
        self.assertIn('AI_NEWS_TEST_BUILD: runtime/browser-build', block)
        self.assertIn('timeout --signal=TERM --kill-after=10s 600s python tests/quality_consumer_browser.py', block)
        self.assertNotIn('if:', block)
        self.assertNotIn('continue-on-error', block)
        self.assertNotIn('||', block)

    def test_all_synthetic_quality_output_is_prepared_and_uploaded(self):
        workflow = (ROOT / '.github/workflows/reader-regression.yml').read_text()
        self.assertIn('runtime/quality-consumer/', UPLOAD_PATHS)
        self.assertIn('            runtime/quality-consumer/\n', workflow)
        for name in ('build-input.json', 'execution.json', '1440x960/consumer-evidence.json',
                     '390x844/result.json', '390x844/failure.png', '390x844/recommended-filtered.png'):
            self.assertTrue(any(matches('runtime/quality-consumer/run-1/' + name, p) for p in UPLOAD_PATHS))

    def test_test_module_cannot_be_optional_or_execute_no_browser(self):
        source = (ROOT / 'tests/quality_consumer_browser.py').read_text()
        tree = ast.parse(source)
        self.assertTrue(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'Harness' for n in ast.walk(tree)))
        self.assertNotIn('pytest.skip', source)
        self.assertIn('((1440, 960), (390, 844))', source)


if __name__ == '__main__': unittest.main()
