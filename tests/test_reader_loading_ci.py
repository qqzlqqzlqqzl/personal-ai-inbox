"""Retained synthetic contracts for Hosted admission, extraction and upload scope."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import reader_loading_ci as ci

RETAINED = Path(tempfile.mkdtemp(prefix='reader-loading-ci-contract-', dir=Path(__file__).parent))


class HostedContracts(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='case-', dir=RETAINED))
        for name in ('inputs', 'work', 'evidence'):
            (self.root / name).mkdir(mode=0o700)

    def synthetic_archive(self, *, path='index.html', mode=stat.S_IFREG | 0o600, repeat=False,
                          identity=None):
        data = b'<html>synthetic baseline</html>'
        info = zipfile.ZipInfo('runtime/browser-build/' + path)
        info.external_attr = mode << 16
        identity = identity or {'passed': True, 'head': ci.BUILD_HEAD, 'tree': ci.BUILD_TREE, 'src_tree': ci.BUILD_SRC}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('artifacts/ci-reader-identity.json', json.dumps(identity))
            archive.writestr(info, data)
            if repeat:
                archive.writestr(info, data)
        raw = stream.getvalue()
        (self.root / 'inputs/baseline.zip').write_bytes(raw)
        return raw

    def verify_synthetic(self, raw):
        with patch.object(ci, 'ARTIFACT_BYTES', len(raw)), \
                patch.object(ci, 'ARTIFACT_SHA', hashlib.sha256(raw).hexdigest()):
            ci.unpack(self.root)

    def test_wrong_artifact_bytes_rejected_before_extraction(self):
        self.synthetic_archive()
        with self.assertRaisesRegex(ValueError, 'bytes or SHA'):
            ci.unpack(self.root)
        self.assertFalse((self.root / 'inputs/build').exists())

    def test_same_size_wrong_digest_rejected_before_extraction(self):
        raw = self.synthetic_archive()
        with patch.object(ci, 'ARTIFACT_BYTES', len(raw)):
            with self.assertRaisesRegex(ValueError, 'bytes or SHA'):
                ci.unpack(self.root)
        self.assertFalse((self.root / 'inputs/build').exists())

    def test_other_baseline_identity_rejected(self):
        raw = self.synthetic_archive(identity={'passed': True, 'head': '0' * 40, 'tree': ci.BUILD_TREE, 'src_tree': ci.BUILD_SRC})
        with self.assertRaisesRegex(ValueError, 'source identity'):
            self.verify_synthetic(raw)

    def test_bad_manifest_set_is_rejected_before_any_build_write(self):
        raw = self.synthetic_archive()
        with self.assertRaisesRegex(ValueError, '125-file'):
            self.verify_synthetic(raw)
        self.assertFalse((self.root / 'inputs/build').exists())

    def test_archive_symlink_rejected(self):
        raw = self.synthetic_archive(mode=stat.S_IFLNK | 0o777)
        with self.assertRaisesRegex(ValueError, 'non-regular'):
            self.verify_synthetic(raw)

    def test_archive_escape_rejected(self):
        raw = self.synthetic_archive(path='../outside.html')
        with self.assertRaisesRegex(ValueError, 'noncanonical'):
            self.verify_synthetic(raw)
        self.assertFalse((self.root / 'outside.html').exists())

    def test_duplicate_archive_member_rejected(self):
        raw = self.synthetic_archive(repeat=True)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.verify_synthetic(raw)

    def test_linked_artifact_is_refused(self):
        target = self.root / 'original.zip'; target.write_bytes(b'preserve')
        (self.root / 'inputs/baseline.zip').symlink_to(target)
        with self.assertRaises(OSError):
            ci.unpack(self.root)
        self.assertEqual(target.read_bytes(), b'preserve')

    def run_fake_download(self, data, *, limit=None, code=0):
        original = subprocess.Popen
        observed = []
        def fake(args, **kwargs):
            observed.append(args)
            script = 'import sys;sys.stdout.buffer.write(' + repr(data) + ');sys.exit(' + str(code) + ')'
            return original([sys.executable, '-c', script], **kwargs)
        with patch.object(ci.subprocess, 'Popen', side_effect=fake), \
                patch.object(ci, 'ARTIFACT_BYTES', len(data) if limit is None else limit), \
                patch.object(ci, 'ARTIFACT_SHA', hashlib.sha256(data).hexdigest()):
            ci.download(self.root)
        return observed

    def test_download_uses_only_exact_same_repository_get_and_bounds_bytes(self):
        calls = self.run_fake_download(b'synthetic bounded artifact')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                   '-H', 'X-GitHub-Api-Version: 2022-11-28',
                                   'repos/qqzlqqzlqqzl/personal-ai-inbox/actions/artifacts/11299074112/zip'])
        self.assertEqual((self.root / 'inputs/baseline.zip').read_bytes(), b'synthetic bounded artifact')
        self.assertFalse(list((self.root / 'evidence').iterdir()))

    def test_excessive_download_is_stopped_and_partial_file_retained(self):
        with self.assertRaisesRegex(ValueError, 'byte budget'):
            self.run_fake_download(b'exceeds budget', limit=3)
        self.assertTrue((self.root / 'inputs/baseline.zip').exists())
        self.assertLessEqual((self.root / 'inputs/baseline.zip').stat().st_size, 3)

    def test_denied_download_is_failure_without_input_fallback(self):
        with self.assertRaisesRegex(ValueError, 'gh artifact download failed'):
            self.run_fake_download(b'', code=1)
        self.assertTrue((self.root / 'work/gh-download.stderr').exists())
        self.assertFalse((self.root / 'inputs/build').exists())

    def test_existing_nonprivate_root_is_refused_without_chmod(self):
        self.root.chmod(0o755)  # Only this new synthetic fixture; robust under umask 077.
        with self.assertRaisesRegex(ValueError, 'private directory'):
            ci.private_directory(self.root)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)

    def test_root_symlink_is_refused(self):
        link = RETAINED / (self.root.name + '-link'); link.symlink_to(self.root)
        with self.assertRaises(ValueError):
            ci.private_directory(link)

    def test_collect_selects_only_direct_synthetic_files(self):
        folder = self.root / 'work/measurements/reader-perf-synthetic'
        folder.mkdir(parents=True, mode=0o700)
        (folder / 'result.json').write_text('{"status":"FAILED","synthetic":true}')
        (folder / 'pair-1-failure.png').write_bytes(b'synthetic image bytes')
        (folder / 'home').mkdir(); (folder / 'home/cookie').write_bytes(b'do not upload')
        (folder / 'baseline.zip').write_bytes(b'do not upload')
        (folder / 'arbitrary.log').write_bytes(b'do not upload')
        ci.collect(self.root)
        output = self.root / 'evidence/measurements'
        self.assertEqual({p.name for p in output.iterdir()},
                         {'reader-perf-synthetic-result.json', 'reader-perf-synthetic-pair-1-failure.png'})
        self.assertEqual((folder / 'home/cookie').read_bytes(), b'do not upload')

    def test_collect_refuses_synthetic_named_symlink(self):
        folder = self.root / 'work/measurements/reader-perf-synthetic'
        folder.mkdir(parents=True, mode=0o700)
        target = self.root / 'original'; target.write_text('retained')
        (folder / 'result.json').symlink_to(target)
        with self.assertRaises(OSError):
            ci.collect(self.root)
        self.assertEqual(target.read_text(), 'retained')

    def test_collect_never_overwrites_an_existing_delivery(self):
        (self.root / 'work/measurements').mkdir()
        ci.collect(self.root)
        before = (self.root / 'evidence/measurement-files.json').read_bytes()
        with self.assertRaises(FileExistsError):
            ci.collect(self.root)
        self.assertEqual((self.root / 'evidence/measurement-files.json').read_bytes(), before)

    def test_only_fixed_modes_are_accepted(self):
        with self.assertRaisesRegex(ValueError, 'unknown fixed'):
            ci.measure(self.root, '--no-sandbox')

    def test_setup_failure_reports_measurement_not_run(self):
        with patch.dict(os.environ, {'READER_LOADING_JOB_STATUS': 'failure'}):
            ci.finish(self.root)
        report = json.loads((self.root / 'evidence/job-outcome.json').read_text())
        self.assertEqual(report['measurement_status'], 'NOT_RUN')
        self.assertEqual(report['job_status_before_upload'], 'failure')

    def test_final_receipt_does_not_inherit_job_success_as_measurement_pass(self):
        with patch.dict(os.environ, {'READER_LOADING_JOB_STATUS': 'success'}):
            ci.finish(self.root)
        self.assertEqual(json.loads((self.root / 'evidence/job-outcome.json').read_text())['measurement_status'], 'NOT_RUN')

    def test_measurement_mode_cannot_change_after_admission(self):
        (self.root / 'evidence/source.json').write_text(json.dumps({'head': 'a' * 40, 'mode': 'touch'}))
        with patch.object(ci, 'git', side_effect=['a' * 40, '']):
            with self.assertRaisesRegex(ValueError, 'mode changed'):
                ci.measure(self.root, 'keyboard')

    def test_measurement_dirty_checkout_is_refused(self):
        (self.root / 'evidence/source.json').write_text(json.dumps({'head': 'a' * 40, 'mode': 'keyboard'}))
        with patch.object(ci, 'git', side_effect=['a' * 40, 'changed.py']):
            with self.assertRaisesRegex(ValueError, 'checkout changed'):
                ci.measure(self.root, 'keyboard')

    def test_prepare_refuses_nonhosted_and_other_repository_before_output(self):
        for env in ({'GITHUB_ACTIONS': 'false', 'GITHUB_REPOSITORY': ci.REPOSITORY},
                    {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'other/repo'}):
            with self.subTest(env=env), patch.dict(os.environ, env):
                with self.assertRaisesRegex(ValueError, 'hosted repository'):
                    ci.prepare('a' * 40, 'keyboard')

    def test_prepare_binds_exact_head_and_new_private_output(self):
        checkout = self.root / 'checkout'; checkout.mkdir()
        runner = self.root / 'runner'; runner.mkdir()
        output = self.root / 'github-output'
        replies = {('rev-parse', 'HEAD'): 'a' * 40, ('diff', '--name-only', 'HEAD'): '',
                   ('rev-parse', 'HEAD^{tree}'): 'b' * 40, ('rev-parse', 'HEAD:src'): 'c' * 40}
        env = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': ci.REPOSITORY,
               'RUNNER_TEMP': str(runner), 'GITHUB_OUTPUT': str(output)}
        with patch.dict(os.environ, env), patch.object(ci, 'ROOT', checkout), \
                patch.object(ci, 'git', side_effect=lambda *args: replies[args]):
            root = ci.prepare('a' * 40, 'keyboard')
            another = ci.prepare('a' * 40, 'touch')
        self.assertNotEqual(root, another)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        receipt = json.loads((root / 'evidence/source.json').read_text())
        self.assertEqual(receipt['head'], 'a' * 40)
        self.assertEqual(receipt['measurement_status'], 'NOT_RUN')

    def test_measurement_child_excludes_tokens_proxies_and_python_injection(self):
        (self.root / 'evidence/source.json').write_text(json.dumps({'head': 'a' * 40, 'mode': 'touch'}))
        browsers = self.root / 'work/browser-cache'; browsers.mkdir()
        observed = []
        class FailedSyntheticChild:
            def wait(self, timeout):
                self.timeout = timeout
                return 1
            def poll(self):
                return 1
        def launch(args, **kwargs):
            observed.append((args, kwargs))
            return FailedSyntheticChild()
        with patch.dict(os.environ, {'GH_TOKEN': 'synthetic-never-inherit', 'HTTPS_PROXY': 'synthetic',
                                    'PYTHONPATH': 'synthetic', 'PLAYWRIGHT_BROWSERS_PATH': str(browsers)}), \
                patch.object(ci, 'git', side_effect=['a' * 40, '']), patch.object(ci.subprocess, 'Popen', side_effect=launch):
            with self.assertRaisesRegex(ValueError, 'measurement failed'):
                ci.measure(self.root, 'touch')
        args, options = observed[0]
        self.assertEqual(args[args.index('--input') + 1], 'touch')
        self.assertNotIn('--weak-network', args)
        for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'HTTPS_PROXY', 'PYTHONPATH'):
            self.assertNotIn(key, options['env'])
        self.assertTrue(options['start_new_session'])
        self.assertEqual(json.loads((self.root / 'evidence/measurement-process.json').read_text())['status'], 'FAILED')

    def test_font_checks_are_reused_and_temporary_inputs_are_retained(self):
        import ci_cjk_font as cjk
        data = b'synthetic verified font bytes'
        record = {'package': 'fonts-noto-cjk', 'version': '1:20220127+repack1-1', 'architecture': 'all',
                  'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                  'filename': 'fonts-noto-cjk_20220127+repack1-1_all.deb'}
        cache = self.root / 'work/font-cache'; cache.mkdir()
        (cache / cjk.CACHED_FILE).write_bytes(data)
        (self.root / 'evidence/font-package.json').write_text(json.dumps(record))
        calls = []
        def command(*args, **kwargs):
            self.assertEqual(kwargs['timeout'], 120)
            calls.append(args)
            if args[:2] == ('sudo', 'install'):
                self.assertEqual(Path(args[5]).read_bytes(), data)
        with patch.object(cjk, 'metadata', return_value=record), patch.object(cjk, 'command', side_effect=command):
            ci.font(self.root, 'install')
        self.assertEqual(len(calls), 2)
        self.assertIn('--no-download', calls[-1])
        staging = list((self.root / 'work').glob('reader-cjk-install-*'))
        self.assertEqual(len(staging), 1)
        self.assertEqual((staging[0] / cjk.CACHED_FILE).read_bytes(), data)

    def test_font_digest_failure_remains_failure_before_any_install(self):
        import ci_cjk_font as cjk
        data = b'synthetic tampered font bytes'
        record = {'package': 'fonts-noto-cjk', 'version': '1:20220127+repack1-1', 'architecture': 'all',
                  'size': len(data), 'sha256': '0' * 64, 'filename': 'unused.deb'}
        cache = self.root / 'work/font-cache'; cache.mkdir()
        (cache / cjk.CACHED_FILE).write_bytes(data)
        (self.root / 'evidence/font-package.json').write_text(json.dumps(record))
        with patch.object(cjk, 'metadata', return_value=record), patch.object(cjk, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                ci.font(self.root, 'install')
            command.assert_not_called()
        self.assertEqual((cache / cjk.CACHED_FILE).read_bytes(), data)


class WorkflowContracts(unittest.TestCase):
    def setUp(self):
        self.workflow = (ci.ROOT / '.github/workflows/reader-loading-performance.yml').read_text()

    def test_modes_have_independent_jobs_and_no_suppressed_failures(self):
        self.assertIn('mode: [keyboard, touch, weak-network]', self.workflow)
        self.assertIn('fail-fast: false', self.workflow)
        self.assertNotIn('continue-on-error:', self.workflow)
        self.assertNotIn('pull_request_target:', self.workflow)
        self.assertIn('cancel-in-progress: false', self.workflow)

    def test_upload_is_fresh_evidence_only_and_failure_retained(self):
        self.assertIn("if: always() && steps.prepare.outcome == 'success'", self.workflow)
        self.assertIn('path: ${{ steps.prepare.outputs.evidence }}/', self.workflow)
        self.assertIn('if-no-files-found: error', self.workflow)
        self.assertLess(self.workflow.index('id: prepare'), self.workflow.index('uses: actions/setup-python@'))

    def test_same_repository_read_permission_is_job_scoped(self):
        prefix, jobs = self.workflow.split('jobs:\n', 1)
        self.assertIn('permissions:\n  contents: read\n', prefix)
        self.assertNotIn('actions: read', prefix)
        self.assertIn('permissions:\n      contents: read\n      actions: read', jobs)
        self.assertNotIn('secrets.', self.workflow)
        self.assertNotIn(': write', self.workflow)
        self.assertEqual(self.workflow.count('GH_TOKEN:'), 1)
        self.assertEqual(self.workflow.count('${{ github.token }}'), 1)

    def test_existing_pins_timeouts_and_no_alternate_browser(self):
        pins = ['3d3c42e5aac5ba805825da76410c181273ba90b1', '5fda3b95a4ea91299a34e894583c3862153e4b97',
                '55cc8345863c7cc4c66a329aec7e433d2d1c52a9', '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a']
        for pin in pins:
            self.assertIn('@' + pin, self.workflow)
        for line in self.workflow.splitlines():
            if 'run: ' in line:
                self.assertIn('run: timeout --signal=TERM --kill-after=10s ', line)
        self.assertIn("python-version: '3.12.14'", self.workflow)
        self.assertIn('runs-on: ubuntu-24.04', self.workflow)
        self.assertNotIn('--no-sandbox', self.workflow)
        self.assertNotIn('CHROMIUM_EXECUTABLE', self.workflow)

    def test_adapter_does_not_add_an_alternate_browser_launch(self):
        tree = ast.parse(Path(ci.__file__).read_text())
        launches = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and
                    isinstance(node.func, ast.Attribute) and node.func.attr == 'launch']
        self.assertEqual(launches, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
