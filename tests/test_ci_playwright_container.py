"""Offline rejection and workflow coverage for the official image trial."""
import copy
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


if __name__ == '__main__':
    unittest.main()
