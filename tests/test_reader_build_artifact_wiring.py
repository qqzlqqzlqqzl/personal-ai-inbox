"""Build-only upload wiring and compatibility with the unchanged input verifier."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from ci_prepare_evidence import UPLOAD_PATHS
from reader_loading_ab_ci import unpack_one

ROOT = Path(__file__).resolve().parents[1]
BASE = '44d2c203b3a8734cdad890d1ca02306f3e29ab2e'
STEP = '      - name: Retain immutable build input separately from full regression evidence\n'
CATALOG_STEP = (
    '      - name: Source catalog ownership across list and detail consumers\n'
    '        env:\n'
    '          AI_NEWS_TEST_BUILD: runtime/browser-build\n'
    '        run: timeout --signal=TERM --kill-after=10s 600s python tests/source_catalog_browser.py\n'
)
CATALOG_UPLOAD = '            runtime/source-catalog/\n'


def workflow_step():
    workflow = (ROOT / '.github/workflows/reader-regression.yml').read_text()
    assert workflow.count(STEP) == 1
    start = workflow.index(STEP)
    end = workflow.index('      - name: ', start + len(STEP))
    return workflow, workflow[start:end], start, end


class BuildArtifactWiring(unittest.TestCase):
    def test_all_original_workflow_bytes_and_steps_preserved(self):
        workflow, _, start, end = workflow_step()
        original = subprocess.check_output(['git', 'show', BASE + ':.github/workflows/reader-regression.yml'],
                                           cwd=ROOT, text=True, timeout=15)
        without_build_upload = workflow[:start] + workflow[end:]
        # The reviewed catalog integration adds one browser gate and its evidence.
        # Every earlier workflow byte remains protected by the original baseline.
        self.assertEqual(without_build_upload.count(CATALOG_STEP), 1)
        self.assertEqual(without_build_upload.count(CATALOG_UPLOAD), 1)
        without_extensions = without_build_upload.replace(CATALOG_STEP, '', 1).replace(CATALOG_UPLOAD, '', 1)
        self.assertEqual(without_extensions, original)
        self.assertTrue(workflow[:start].endswith(
            '      - name: Build isolated reader\n'
            '        run: timeout --signal=TERM --kill-after=10s 600s python tests/build_ci_reader.py\n'))

    def test_only_fresh_build_and_identity_inputs_are_uploaded(self):
        _, block, _, _ = workflow_step()
        paths = block.split('          path: |\n', 1)[1].split('          if-no-files-found:', 1)[0]
        self.assertEqual(paths.splitlines(), [
            '            artifacts/ci-tested-source.json',
            '            artifacts/ci-reader-identity.json',
            '            runtime/browser-build/'])
        for path in paths.splitlines():
            self.assertIn(path.strip(), UPLOAD_PATHS)
        self.assertIn("if: success() && steps.prepare_evidence.outcome == 'success'", block)
        self.assertIn('if-no-files-found: error', block)
        self.assertNotIn('always()', block)
        self.assertNotIn('continue-on-error', block)
        self.assertNotIn('overwrite:', block)

    def test_action_and_attempt_identity_are_pinned(self):
        _, block, _, _ = workflow_step()
        self.assertIn('uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a', block)
        self.assertIn('name: reader-build-${{ github.run_id }}-${{ github.run_attempt }}', block)
        self.assertNotIn('secrets.', block)
        self.assertNotIn('permissions:', block)

    def archive(self, *, wrong_identity=False, extra_build=False):
        # Deliberately retained synthetic fixtures; no profile or user data.
        root = Path(tempfile.mkdtemp(prefix='reader-build-artifact-retained-'))
        files = [('index.html', b'<html><body>synthetic build</body></html>'),
                 ('assets/synthetic.js', b'console.log("synthetic");')]
        rows = [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                for name, data in files]
        manifest = (json.dumps(rows, ensure_ascii=False, indent=2) + '\n').encode()
        identity = {'passed': True, 'head': 'a'*40, 'tree': 'b'*40, 'src_tree': 'c'*40}
        if wrong_identity: identity['head'] = 'd'*40
        path = root / 'artifact.zip'
        with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('artifacts/ci-reader-identity.json', json.dumps(identity))
            archive.writestr('artifacts/ci-tested-source.json', json.dumps({
                'head': 'a'*40, 'run_id': '1', 'run_attempt': '1', 'prepared': True}))
            for name, data in files:
                info = zipfile.ZipInfo('runtime/browser-build/' + name)
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
            if extra_build:
                info = zipfile.ZipInfo('runtime/browser-build/unlisted.js')
                info.external_attr = 0o100644 << 16
                archive.writestr(info, b'not in expected manifest')
        raw = path.read_bytes()
        return root, {'artifact_bytes': len(raw), 'artifact_sha256': hashlib.sha256(raw).hexdigest(),
                      'manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'files': len(files),
                      'head': 'a'*40, 'tree': 'b'*40, 'src': 'c'*40, 'producer_conclusion': 'success'}

    def test_unchanged_consumer_accepts_complete_build_only_archive(self):
        root, spec = self.archive()
        result = unpack_one(root, spec)
        self.assertEqual(result['files'], 2)  # Actual candidate count, never forced to 125.
        self.assertTrue(result['build_identity_passed'])
        self.assertEqual(json.loads((root / 'manifest.json').read_text())[1]['path'], 'assets/synthetic.js')

    def test_unchanged_consumer_rejects_wrong_head_or_unlisted_build_member(self):
        for kwargs in ({'wrong_identity': True}, {'extra_build': True}):
            with self.subTest(case=kwargs):
                root, spec = self.archive(**kwargs)
                with self.assertRaises(ValueError):
                    unpack_one(root, spec)
                self.assertFalse((root / 'build').exists())


if __name__ == '__main__':
    unittest.main()
