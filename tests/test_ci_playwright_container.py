"""Offline rejection and workflow coverage for the official image trial."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ci_cjk_font as font
import ci_playwright_container as trial


class ContainerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.args = [{'ID': 'ubuntu', 'VERSION_ID': '24.04'}, 'x86_64', '1.63.0',
                     {'driverVersion': '1.63.0', 'dockerImageName': trial.TAG},
                     {'PLAYWRIGHT_BROWSERS_PATH': '/ms-playwright',
                      'READER_PLAYWRIGHT_IMAGE': trial.IMAGE}]

    def test_exact_environment_and_independent_identity_rejections(self):
        trial.validate_environment(*self.args)
        changes = [(0, {'ID': 'ubuntu', 'VERSION_ID': '22.04'}), (1, 'aarch64'),
                   (2, '1.62.0'), (3, {'driverVersion': '1.62.0', 'dockerImageName': trial.TAG}),
                   (3, {'driverVersion': '1.63.0', 'dockerImageName': 'untrusted.example/image'}),
                   (4, {'PLAYWRIGHT_BROWSERS_PATH': '/tmp/browser', 'READER_PLAYWRIGHT_IMAGE': trial.IMAGE}),
                   (4, {'PLAYWRIGHT_BROWSERS_PATH': '/ms-playwright', 'READER_PLAYWRIGHT_IMAGE': trial.TAG}),
                   (4, {**self.args[4], 'CHROMIUM_EXECUTABLE': '/tmp/chrome'})]
        for index, value in changes:
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                args = copy.deepcopy(self.args)
                args[index] = value
                trial.validate_environment(*args)

    def test_docker_inspect_rejects_wrong_digest_or_platform(self):
        info = {'Id': trial.CONFIG, 'Os': 'linux', 'Architecture': 'amd64',
                'RepoDigests': [trial.IMAGE]}
        trial.validate_image_inspection(info)
        for key, value in [('Id', 'sha256:' + '0' * 64), ('Os', 'windows'),
                           ('Architecture', 'arm64'), ('RepoDigests', [trial.TAG])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                trial.validate_image_inspection({**info, key: value})

    def test_browser_outside_image_is_rejected_before_execution(self):
        with patch.object(trial, 'run') as run, self.assertRaises(ValueError):
            trial.verify_browser('/tmp/unreviewed-browser')
        run.assert_not_called()

    def test_pull_command_failure_is_not_an_empty_cache(self):
        result = subprocess.CompletedProcess([], 1, '', 'permission denied')
        with patch.dict(trial.os.environ, GITHUB_ACTIONS='true'), \
                patch.object(trial, 'run', return_value=result) as run, self.assertRaises(ValueError):
            trial.measure_pulls()
        self.assertEqual(run.call_count, 1)

    def test_font_command_keeps_nonroot_and_equivalent_root_arguments(self):
        for uid, expected in [(1001, ('sudo', 'install', '-m', '0644', '--', 'verified', 'target')),
                              (0, ('install', '-m', '0644', '--', 'verified', 'target'))]:
            with self.subTest(uid=uid), patch.object(font.os, 'geteuid', return_value=uid), \
                    patch.object(font.subprocess, 'run') as run:
                font.command('sudo', 'install', '-m', '0644', '--', 'verified', 'target')
                self.assertEqual(run.call_args.args[0], expected)
                self.assertTrue(run.call_args.kwargs['check'])
        with patch.object(font.os, 'geteuid', return_value=0), patch.object(font.subprocess, 'run') as run:
            font.command('apt-cache', 'show', 'fonts-noto-cjk=1')
            self.assertEqual(run.call_args.args[0], ('apt-cache', 'show', 'fonts-noto-cjk=1'))

    def test_trial_keeps_complete_matrix_identity_and_font_verification(self):
        root = Path(__file__).resolve().parents[1]
        baseline = (root / '.github/workflows/reader-regression.yml').read_text()
        candidate = (root / '.github/workflows/reader-container-trial.yml').read_text()
        for line in baseline.splitlines():
            if line.strip().startswith('run: timeout') and 'python -m playwright install' not in line:
                self.assertIn(line, candidate)
        for required in ['pytest.main(', 'for test in tests/test_*.mjs', 'native_zoom_browser.py',
                         'python-version: \'3.12.14\'', 'node-version: \'24.21.0\'',
                         'ci_reader_contract.py --expected-head', 'ci_cjk_font.py prepare',
                         'ci_cjk_font.py install', 'APT::Update::Error-Mode=any',
                         'Acquire::Check-Valid-Until=true', 'timeout-minutes: 30',
                         'contents: read', 'persist-credentials: false', trial.IMAGE,
                         'image-pull-cost:', 'runtime/playwright-container/']:
            self.assertIn(required, candidate)
        for forbidden in ['--privileged', '--cap-add', '--ipc=host', 'continue-on-error',
                          'restore-keys:', 'apt-get clean', 'docker system prune']:
            self.assertNotIn(forbidden, candidate)
        self.assertIn('python -m playwright install-deps chromium', baseline)
        self.assertNotIn('python -m playwright install-deps', candidate)


class CheckoutBindingTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='reader-container-binding-retained-'))
        self.repo = self.base / 'repository'
        (self.repo / '.git').mkdir(parents=True)
        self.temp = self.base / 'runner-temp'
        self.temp.mkdir()
        self.output = self.temp / 'job-env'
        self.output.write_text('')
        self.head = 'a' * 40
        self.environ = {'GITHUB_ACTIONS': 'true', 'READER_PLAYWRIGHT_IMAGE': trial.IMAGE,
                        'GITHUB_WORKSPACE': str(self.repo), 'GITHUB_ENV': str(self.output),
                        'RUNNER_TEMP': str(self.temp)}

    def test_exact_binding_is_job_scoped_and_checks_head_first(self):
        result = subprocess.CompletedProcess([], 0, self.head + '\n', '')
        with patch.object(trial, 'run', return_value=result) as run:
            receipt = trial.bind_checkout(self.repo, self.head, self.environ)
        self.assertTrue(receipt['passed'])
        self.assertEqual(run.call_args.args,
                         ('git', '-c', 'safe.directory=' + str(self.repo), '-C', str(self.repo), 'rev-parse', 'HEAD'))
        self.assertEqual(self.output.read_text(),
                         'GIT_CONFIG_COUNT=1\nGIT_CONFIG_KEY_0=safe.directory\nGIT_CONFIG_VALUE_0=' + str(self.repo) + '\n')

    def test_wrong_head_does_not_export_trust(self):
        with patch.object(trial, 'run', return_value=subprocess.CompletedProcess([], 0, 'b' * 40, '')), \
                self.assertRaisesRegex(ValueError, 'immutable commit'):
            trial.bind_checkout(self.repo, self.head, self.environ)
        self.assertEqual(self.output.read_text(), '')

    def test_path_image_existing_config_and_env_file_fail_before_git(self):
        changes = [{'GITHUB_WORKSPACE': str(self.base)}, {'READER_PLAYWRIGHT_IMAGE': trial.TAG},
                   {'GIT_CONFIG_COUNT': '1'}, {'GIT_CONFIG_GLOBAL': '/tmp/not-approved'},
                   {'GITHUB_ENV': str(self.base / 'outside')}, {'GITHUB_ACTIONS': 'false'}]
        for change in changes:
            with self.subTest(change=change), patch.object(trial, 'run') as run, self.assertRaises(ValueError):
                trial.bind_checkout(self.repo, self.head, {**self.environ, **change})
            run.assert_not_called()
            self.assertEqual(self.output.read_text(), '')

    def test_workflow_binds_before_prepare_and_does_not_use_global_or_wildcard(self):
        text = (Path(__file__).resolve().parents[1] / '.github/workflows/reader-container-trial.yml').read_text()
        self.assertLess(text.index(' bind-checkout '), text.index('id: prepare_evidence'))
        self.assertNotIn('safe.directory=*', text)
        self.assertNotIn('git config --global', text)

    def test_real_git_exact_exception_keeps_another_repository_rejected(self):
        empty_config = self.base / 'empty-global-config'
        empty_config.write_text('')
        clean = {k: v for k, v in os.environ.items() if not k.startswith('GIT_CONFIG_')}
        clean.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=str(empty_config))
        repos = [self.base / 'trusted-fixture', self.base / 'other-fixture']
        for repo in repos:
            subprocess.run(['git', 'init', '--quiet', str(repo)], env=clean, check=True, timeout=10)
        probe = {**clean, 'GIT_TEST_ASSUME_DIFFERENT_OWNER': '1', 'GIT_CONFIG_COUNT': '1',
                 'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': str(repos[0])}
        trusted = subprocess.run(['git', '-C', str(repos[0]), 'rev-parse', '--show-toplevel'],
                                 env=probe, capture_output=True, text=True, timeout=10)
        other = subprocess.run(['git', '-C', str(repos[1]), 'rev-parse', '--show-toplevel'],
                               env=probe, capture_output=True, text=True, timeout=10)
        self.assertEqual(trusted.returncode, 0, trusted.stderr)
        self.assertEqual(other.returncode, 128)
        self.assertIn('dubious ownership', other.stderr)


class PythonCacheBindingTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='reader-python-prefix-retained-'))
        self.cache = self.base / 'mounted-cache'
        self.bin = self.cache / 'Python/3.12.14/x64/bin'
        self.bin.mkdir(parents=True)
        self.prefix = self.base / 'original-cache-prefix'
        self.python = self.bin / 'python3.12'
        # Synthetic executable tests the OS shebang failure, not Python version acceptance.
        self.python.write_text('#!/bin/sh\nprintf "synthetic-interpreter-reached\\n"\n')
        self.python.chmod(0o755)
        self.pip = self.bin / 'pip'
        self.pip.write_text('#!' + str(self.prefix / 'Python/3.12.14/x64/bin/python3.12') + '\n# synthetic pip fixture\n')
        self.pip.chmod(0o755)

    def identity(self, **overrides):
        return subprocess.CompletedProcess([], 0, json.dumps({
            'version': [3, 12, 14], 'implementation': 'cpython',
            'executable': str(self.python), 'prefix': str(self.bin.parent), **overrides}), '')

    def test_actual_missing_shebang_target_then_exact_mapping_without_rewrite(self):
        before = self.pip.read_bytes()
        with self.assertRaises(FileNotFoundError):
            subprocess.run([str(self.pip), 'cache', 'dir'], check=True, capture_output=True, timeout=10)
        # The identity boundary is mocked; the kernel shebang failure/recovery is real.
        with patch.object(trial, 'run', return_value=self.identity()):
            result = trial.bind_python_cache_paths(self.cache, self.prefix)
        self.assertTrue(result['mapping_created'])
        actual = subprocess.run([str(self.pip), 'cache', 'dir'], check=True,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(actual.stdout, 'synthetic-interpreter-reached\n')
        self.assertEqual(self.pip.read_bytes(), before)
        with patch.object(trial, 'run', return_value=self.identity()):
            self.assertFalse(trial.bind_python_cache_paths(self.cache, self.prefix)['mapping_created'])

    def test_conflicting_prefix_is_retained_and_rejected(self):
        self.prefix.mkdir()
        sentinel = self.prefix / 'keep'
        sentinel.write_text('retained')
        with patch.object(trial, 'run', return_value=self.identity()), self.assertRaisesRegex(ValueError, 'conflicts'):
            trial.bind_python_cache_paths(self.cache, self.prefix)
        self.assertEqual(sentinel.read_text(), 'retained')

    def test_unknown_shebang_and_wrong_version_do_not_create_alias(self):
        for interpreter in ['/unrecognized/python3.12',
                            str(self.prefix / 'Python/3.13.0/x64/bin/python3.12')]:
            with self.subTest(interpreter=interpreter):
                self.pip.write_text('#!' + interpreter + '\n')
                with self.assertRaises(ValueError):
                    trial.bind_python_cache_paths(self.cache, self.prefix)
                self.assertFalse(self.prefix.exists())

    def test_actual_interpreter_identity_mismatch_prevents_mapping(self):
        for change in [{'version': [3, 12, 3]}, {'implementation': 'pypy'},
                       {'executable': '/unrelated/python'}, {'prefix': '/unrelated/prefix'}]:
            with self.subTest(change=change), patch.object(trial, 'run', return_value=self.identity(**change)), \
                    self.assertRaises(ValueError):
                trial.bind_python_cache_paths(self.cache, self.prefix)
            self.assertFalse(self.prefix.exists())

    def test_uncached_distribution_leaves_original_installer_responsible(self):
        untouched = self.base / 'empty-cache'
        untouched.mkdir()
        result = trial.bind_python_cache_paths(untouched, self.prefix)
        self.assertFalse(result['mapping_created'])
        self.assertFalse(self.prefix.exists())

    def test_workflow_keeps_original_python_action_cache_and_exact_version(self):
        workflows = Path(__file__).resolve().parents[1] / '.github/workflows'
        text = (workflows / 'reader-container-trial.yml').read_text()
        self.assertLess(text.index(' bind-python-cache '), text.index('uses: actions/setup-python@'))
        self.assertIn("python-version: '3.12.14'\n          cache: pip\n          cache-dependency-path: requirements.dev.lock.txt", text)
        self.assertIn('PIP_CACHE_DIR: /root/.cache/pip', text)
        self.assertNotIn('PIP_CACHE_DIR:', (workflows / 'reader-regression.yml').read_text())


class PipOwnerCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='reader-pip-owner-retained-'))

    def test_owned_real_directory_is_created_and_existing_bytes_preserved(self):
        result = trial.prepare_owned_pip_cache(self.root, os.geteuid())
        cache = Path(result['path'])
        self.assertEqual(cache.stat().st_uid, os.geteuid())
        self.assertFalse(cache.is_symlink())
        marker = cache / 'existing-cache-data'
        marker.write_bytes(b'retain this existing cache')
        mode = cache.stat().st_mode
        again = trial.prepare_owned_pip_cache(self.root, os.geteuid())
        self.assertEqual(again['created_directories'], [])
        self.assertEqual(cache.stat().st_mode, mode)
        self.assertEqual(marker.read_bytes(), b'retain this existing cache')

    def test_symlink_and_nondirectory_are_refused_without_overwrite(self):
        outside = self.root / 'outside'
        outside.mkdir()
        home = self.root / 'home-link-case'
        home.mkdir()
        (home / '.cache').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            trial.prepare_owned_pip_cache(home, os.geteuid())
        self.assertEqual(list(outside.iterdir()), [])
        other = self.root / 'home-file-case'
        other.mkdir()
        sentinel = other / '.cache'
        sentinel.write_bytes(b'keep file')
        with self.assertRaises(ValueError):
            trial.prepare_owned_pip_cache(other, os.geteuid())
        self.assertEqual(sentinel.read_bytes(), b'keep file')

    def test_wrong_owner_is_refused_without_chown_or_creation(self):
        with self.assertRaises(ValueError):
            trial.prepare_owned_pip_cache(self.root, os.geteuid() + 1)
        self.assertFalse((self.root / '.cache').exists())


if __name__ == '__main__':
    unittest.main()
