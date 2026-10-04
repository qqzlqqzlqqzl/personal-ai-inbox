"""Hosted-only input and evidence adapter for the reviewed Linux measurement entry.

The baseline archive, extracted build and browser profiles are never upload inputs.
Each invocation retains all files. This adapter does not publish or invoke CI.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

from reader_loading_fixture import checked_directory, bound_read, require, admit_build
from reader_loading_performance import save_new, browser_env

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'qqzlqqzlqqzl/personal-ai-inbox'
ARTIFACT = 11299074112
ARTIFACT_BYTES = 8750880
ARTIFACT_SHA = '108536813a6d2e3da2f3801917f082acad2e8271b9a27ef2b2dcf66c7a3b8a85'
MANIFEST_SHA = '05dd86f9c5face0b31a242f27b5cc25174249cc10d41f2d5bd535575c3c06e1f'
BUILD_HEAD = '879ff545aa19330ac884dd320fdd3ccfbd265d9b'
BUILD_TREE = '65355bc7b2849809c83121e90e953429ba81ffde'
BUILD_SRC = '0049640f793f5dd505bf2fea6fd8274f0f074f08'
LOCK_SHA = '106373e10b547e80b632c0d584cf5deb85bb5486d60303936f22e3f2aefdbe4f'
MODES = ('keyboard', 'touch', 'weak-network')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, timeout=15).strip()


def private_directory(path):
    path = checked_directory(path)
    info = path.stat(follow_symlinks=False)
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            'expected a current-user private directory')
    return path


def prepare(expected_head, mode):
    require(sys.platform == 'linux' and mode in MODES, 'Linux and a fixed mode are required')
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY,
            'only the requested hosted repository is admitted')
    require(re.fullmatch('[0-9a-f]{40}', expected_head) and git('rev-parse', 'HEAD') == expected_head,
            'unexpected immutable checkout')
    require(not git('diff', '--name-only', 'HEAD'), 'tracked checkout changed')
    require(not (ROOT / '.private').exists(), 'private deployment configuration is refused')
    parent = checked_directory(os.environ['RUNNER_TEMP'])
    require(not parent.is_relative_to(ROOT), 'temporary input must be outside checkout')
    root = Path(tempfile.mkdtemp(prefix='reader-loading-ci-', dir=parent))
    private_directory(root)
    for name in ('inputs', 'work', 'evidence'):
        (root / name).mkdir(mode=0o700)
    save_new(root / 'evidence/source.json', {
        'head': expected_head, 'tree': git('rev-parse', 'HEAD^{tree}'),
        'src_tree': git('rev-parse', 'HEAD:src'), 'mode': mode,
        'run_id': os.environ.get('GITHUB_RUN_ID'), 'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
        'baseline_artifact_id': ARTIFACT, 'synthetic_only': True,
        'measurement_status': 'NOT_RUN', 'prepared': True})
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
        stream.write(f'root={root}\nevidence={root / "evidence"}\n')
    return root


def download(root):
    """Exact gh API read; bound time/bytes and keep raw failure text outside upload."""
    root = private_directory(root)
    path = root / 'inputs/baseline.zip'
    args = ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            f'repos/{REPOSITORY}/actions/artifacts/{ARTIFACT}/zip']
    start = time.monotonic()
    count = 0
    process = None
    # gh owns the official API redirect handling. No signed redirect or token is logged.
    with path.open('xb') as target, (root / 'work/gh-download.stderr').open('xb') as errors:
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = 90 - (time.monotonic() - start)
                    if remaining <= 0:
                        raise TimeoutError('artifact download exceeded 90 seconds')
                    ready = selector.select(min(remaining, 1))
                    if not ready:
                        continue
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    count += len(chunk)
                    require(count <= ARTIFACT_BYTES, 'artifact exceeds the exact byte budget')
                    target.write(chunk)
            code = process.wait(timeout=max(1, 90 - (time.monotonic() - start)))
            require(code == 0, 'gh artifact download failed; raw error retained outside upload')
            require(count == ARTIFACT_BYTES, 'artifact byte count differs')
        finally:
            if process is not None:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                process.stdout.close()
    require(hashlib.sha256(bound_read(path.parent, path.name, ARTIFACT_BYTES)).hexdigest() == ARTIFACT_SHA,
            'downloaded artifact SHA256 differs')


def unpack(root):
    root = private_directory(root)
    raw = bound_read(root / 'inputs', 'baseline.zip', ARTIFACT_BYTES)
    require(len(raw) == ARTIFACT_BYTES and hashlib.sha256(raw).hexdigest() == ARTIFACT_SHA,
            'baseline bytes or SHA256 differ')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(len(infos) <= 1024 and sum(i.file_size for i in infos) <= 64 * 1024 * 1024,
                'archive member or expanded-size budget exceeded')
        require(len(set(i.filename for i in infos)) == len(infos), 'duplicate archive member')
        require(archive.testzip() is None, 'baseline CRC failure')
        identity = json.loads(archive.read('artifacts/ci-reader-identity.json'))
        require(identity.get('passed') is True and identity.get('head') == BUILD_HEAD and
                identity.get('tree') == BUILD_TREE and identity.get('src_tree') == BUILD_SRC,
                'baseline source identity differs')
        prefix = 'runtime/browser-build/'
        rows = []
        files = {}
        for info in infos:
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            name = info.filename[len(prefix):]
            require(name and str(PurePosixPath(name)) == name and not name.startswith('/') and
                    '\\' not in name and '..' not in PurePosixPath(name).parts,
                    'noncanonical build archive member')
            require(stat.S_ISREG(info.external_attr >> 16), 'non-regular build archive member')
            data = archive.read(info)
            files[name] = data
            rows.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        manifest = (json.dumps(rows, ensure_ascii=False, indent=2) + '\n').encode()
        require(len(rows) == 125 and hashlib.sha256(manifest).hexdigest() == MANIFEST_SHA,
                'exact 125-file baseline manifest differs')
        build = root / 'inputs/build'
        build.mkdir(mode=0o700)
        for name, data in files.items():
            target = build / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open('xb') as stream:
                stream.write(data)
        with (root / 'inputs/manifest.json').open('xb') as stream:
            stream.write(manifest)
    admit_build(build, root / 'inputs/manifest.json', MANIFEST_SHA)
    save_new(root / 'evidence/baseline-input.json', {
        'artifact_id': ARTIFACT, 'artifact_bytes': len(raw), 'artifact_sha256': ARTIFACT_SHA,
        'manifest_sha256': MANIFEST_SHA, 'files': 125, 'crc': 'PASSED',
        'head': BUILD_HEAD, 'tree': BUILD_TREE, 'src_tree': BUILD_SRC,
        'note': '879 tested build; 9a66803 is a same-tree merge, not a new baseline CI run'})


def identity(root):
    root = private_directory(root)
    require(sys.platform == 'linux' and sys.version_info[:3] == (3, 12, 14), 'expected Linux Python 3.12.14')
    release = platform.freedesktop_os_release()
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '22.04', 'expected Ubuntu 22.04')
    require(not os.environ.get('CHROMIUM_EXECUTABLE'), 'browser executable override refused')
    raw = (ROOT / 'requirements.dev.lock.txt').read_bytes()
    require(hashlib.sha256(raw).hexdigest() == LOCK_SHA, 'locked test dependency file changed')
    versions = {}
    for line in raw.decode().splitlines():
        if not line or line.startswith('#'):
            continue
        name, expected = line.split('==')
        require(importlib.metadata.version(name) == expected, 'locked package mismatch: ' + name)
        versions[name] = expected
    import playwright
    descriptors = json.loads((Path(playwright.__file__).parent / 'driver/package/browsers.json').read_text())
    chromium = [row for row in descriptors['browsers'] if row['name'] in ('chromium', 'chromium-headless-shell')]
    require(len(chromium) == 2 and all(row['revision'] == '1243' and row['browserVersion'] == '153.0.8010.12'
                                     for row in chromium), 'wrong fixed Chromium descriptors')
    save_new(root / 'evidence/toolchain.json', {'python': platform.python_version(), 'os': release,
             'dependencies': versions, 'lock_sha256': LOCK_SHA, 'chromium': chromium,
             'browser_launch': 'NOT_RUN; the measurement entry separately checks the actual binary'})


def font(root, action):
    """Reuse signed-index/size/SHA checks, retaining the helper's temporary dirs."""
    from unittest.mock import patch
    import ci_cjk_font as cjk
    root = private_directory(root)
    original_command = cjk.command

    @contextmanager
    def retained_temporary_directory(*, prefix):
        folder = tempfile.mkdtemp(prefix=prefix, dir=root / 'work')
        yield folder  # Retention is intentional, including failure paths.

    def bounded_command(*args, **kwargs):
        return original_command(*args, timeout=120, **kwargs)

    argv = ['ci_cjk_font.py', action, '--cache', str(root / 'work/font-cache'),
            '--manifest', str(root / 'evidence/font-package.json')]
    with patch.object(sys, 'argv', argv), patch.object(cjk.tempfile, 'TemporaryDirectory', retained_temporary_directory), \
            patch.object(cjk, 'command', bounded_command):
        cjk.main()


def collect(root):
    """Allow only new synthetic measurement JSON/screenshots; never recurse profiles."""
    root = private_directory(root)
    parent = root / 'work/measurements'
    target = root / 'evidence/measurements'
    target.mkdir(mode=0o700)
    inventory = []
    for folder in sorted(parent.glob('reader-perf-*')):
        private_directory(folder)
        for source in sorted(folder.iterdir()):
            if not re.fullmatch(r'(?:result|boundary|pair-[1-5])\.json|pair-[1-5]-failure\.png', source.name):
                continue
            raw = bound_read(folder, source.name, 16 * 1024 * 1024)
            destination = target / (folder.name + '-' + source.name)
            with destination.open('xb') as stream:
                stream.write(raw)
            inventory.append({'path': destination.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    save_new(root / 'evidence/measurement-files.json', inventory)


def measure(root, mode):
    root = private_directory(root)
    require(mode in MODES, 'unknown fixed measurement mode')
    source = json.loads(bound_read(root / 'evidence', 'source.json'))
    require(git('rev-parse', 'HEAD') == source['head'] and not git('diff', '--name-only', 'HEAD'),
            'measurement checkout changed after admission')
    require(source['mode'] == mode, 'measurement mode changed after admission')
    parent = root / 'work/measurements'
    parent.mkdir(mode=0o700)
    args = [sys.executable, '-B', str(ROOT / 'tests/reader_loading_performance.py'),
            '--build', str(root / 'inputs/build'), '--manifest', str(root / 'inputs/manifest.json'),
            '--manifest-sha', MANIFEST_SHA, '--artifact-zip', str(root / 'inputs/baseline.zip'),
            '--artifact-sha', ARTIFACT_SHA, '--source-tree', BUILD_TREE, '--phase', 'baseline',
            '--input', 'touch' if mode == 'touch' else 'keyboard', '--output-parent', str(parent)]
    if mode == 'weak-network':
        args.append('--weak-network')
    driver = root / 'work/driver'
    driver.mkdir(mode=0o700)
    env = browser_env(driver)
    env.update(PATH=os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1',
               PLAYWRIGHT_BROWSERS_PATH=str(checked_directory(os.environ['PLAYWRIGHT_BROWSERS_PATH'])))
    start = time.monotonic()
    result = {'mode': mode, 'argv': args, 'timeout_seconds': 590, 'status': 'FAILED'}
    process = None
    try:
        with (root / 'evidence/measurement.stdout').open('xb') as output, \
                (root / 'evidence/measurement.stderr').open('xb') as errors:
            process = subprocess.Popen(args, env=env, stdout=output, stderr=errors, start_new_session=True)
            code = process.wait(timeout=590)
        result.update(exit_code=code, status='PASSED' if code == 0 else 'FAILED')
        require(code == 0, 'measurement failed; synthetic evidence retained')
    except subprocess.TimeoutExpired:
        result.update(exit_code=None, status='TIMEOUT')
        raise
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
        result['elapsed_seconds'] = time.monotonic() - start
        save_new(root / 'evidence/measurement-process.json', result)
        collect(root)


def finish(root):
    root = private_directory(root)
    job_status = os.environ['READER_LOADING_JOB_STATUS']
    require(job_status in ('success', 'failure', 'cancelled'), 'unexpected hosted job outcome')
    path = root / 'evidence/measurement-process.json'
    measurement = json.loads(bound_read(path.parent, path.name)) if path.exists() else None
    save_new(root / 'evidence/job-outcome.json', {
        'job_status_before_upload': job_status,
        'measurement_status': measurement['status'] if measurement else 'NOT_RUN',
        'synthetic_only': True, 'production_improvement_claimed': False,
        'setup_failure_details': 'See exact GitHub job step logs; no raw download stderr is uploaded.'})


def main():
    os.umask(0o077)  # Only newly created files in this short-lived helper process.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'download', 'unpack', 'identity', 'font-prepare', 'font-install', 'measure', 'finish'])
    parser.add_argument('--root'); parser.add_argument('--expected-head'); parser.add_argument('--mode', choices=MODES)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.expected_head, args.mode)
        return 0
    root = private_directory(args.root)
    receipt = {'action': args.action, 'status': 'FAILED', 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    try:
        if args.action == 'download': download(root)
        elif args.action == 'unpack': unpack(root)
        elif args.action == 'identity': identity(root)
        elif args.action.startswith('font-'): font(root, args.action.split('-')[1])
        elif args.action == 'measure': measure(root, args.mode)
        elif args.action == 'finish': finish(root)
        receipt['status'] = 'PASSED'
        return 0
    except Exception as exc:
        # Raw gh stderr can include private redirect details and is never uploaded.
        receipt['error_type'] = type(exc).__name__
        print('Reader loading CI action failed:', args.action, type(exc).__name__, file=sys.stderr)
        return 1
    finally:
        receipt['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        save_new(root / 'evidence' / (args.action + '-status.json'), receipt)


if __name__ == '__main__':
    raise SystemExit(main())
