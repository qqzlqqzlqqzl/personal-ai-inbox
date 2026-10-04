"""Narrow Reader #72 trial: verify the official image and measure retained pulls.

The workflow pins the Linux/amd64 manifest, not a mutable tag. The Docker job
initialization log is the image-pull evidence; this helper separately checks the
actual browser paths, versions and OS dependencies. No installed-system cache,
mirror rewrite, privilege flag, browser fallback, or cleanup is provided.
"""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PLAYWRIGHT = '1.63.0'
TAG = 'mcr.microsoft.com/playwright/python:v1.63.0-noble'
DIGEST = 'sha256:96b39581c89131729a7ecb8d532314af54c7f9bcc7a61fe15c7a9e77602acf59'
IMAGE = 'mcr.microsoft.com/playwright/python@' + DIGEST
CONFIG = 'sha256:59fe35832a79d1324e778b737760b2afc11cffcbaeba325012f96cabae832a75'
COMPRESSED_LAYER_BYTES = 1013933919
CHROMIUM = '153.0.8010.12'
COMMANDS = []


def require(value, message):
    if not value:
        raise ValueError(message)


def run(*argv, seconds=60, check=True, environ=None):
    start = time.monotonic()
    result = subprocess.run(argv, text=True, capture_output=True, timeout=seconds, env=environ)
    COMMANDS.append({'argv': list(argv), 'seconds': round(time.monotonic() - start, 6),
                     'timeout_seconds': seconds, 'exit_code': result.returncode,
                     'stdout': result.stdout, 'stderr': result.stderr})
    if check:
        result.check_returncode()
    return result


def validate_environment(release, machine, installed, marker, environ):
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '24.04',
            'Expected official Noble Ubuntu 24.04')
    require(machine == 'x86_64', 'Expected Linux amd64 image')
    require(installed == PLAYWRIGHT, 'Playwright package/image version mismatch')
    require(marker == {'driverVersion': PLAYWRIGHT, 'dockerImageName': TAG},
            'Official Playwright image marker mismatch')
    require(environ.get('PLAYWRIGHT_BROWSERS_PATH') == '/ms-playwright',
            'Image browser directory must be used')
    require(environ.get('READER_PLAYWRIGHT_IMAGE') == IMAGE,
            'Workflow must declare the reviewed immutable image')
    require(not environ.get('CHROMIUM_EXECUTABLE'), 'Browser override is refused')


def validate_image_inspection(info):
    require(info.get('Id') == CONFIG, 'Image config digest mismatch')
    require(info.get('Os') == 'linux' and info.get('Architecture') == 'amd64',
            'Docker image platform mismatch')
    require(IMAGE in info.get('RepoDigests', []), 'Docker did not retain the pinned manifest identity')


def bind_checkout(root, expected_head, environ):
    """Bind Git's container-owner exception to this verified job workspace only.

    Checkout v7 uses a temporary HOME for its own Git configuration. Subsequent
    container shell steps have another HOME and need the exact workspace binding.
    No global config, ownership, permissions or wildcard exception is changed.
    """
    root = Path(root)
    require(not any(c in str(root) for c in '\r\n*?[]'), 'Unsafe workspace configuration value')
    require(environ.get('GITHUB_ACTIONS') == 'true' and environ.get('READER_PLAYWRIGHT_IMAGE') == IMAGE,
            'Checkout binding requires the declared hosted image')
    require(re.fullmatch(r'[a-f0-9]{40}', expected_head), 'Expected immutable commit SHA')
    require(root.is_absolute() and str(root) == environ.get('GITHUB_WORKSPACE') and root.resolve() == root,
            'Checkout must equal the exact canonical job workspace')
    require(not any(p.is_symlink() for p in (root, *root.parents)), 'Symlink workspace is refused')
    require((root / '.git').is_dir() and not (root / '.git').is_symlink(), 'Expected real checkout Git directory')
    require(not any(k.startswith('GIT_CONFIG_') for k in environ), 'Pre-existing Git configuration override is refused')
    target = Path(environ.get('GITHUB_ENV', ''))
    temporary = Path(environ.get('RUNNER_TEMP', ''))
    require(temporary.is_absolute() and target.is_absolute() and target.is_relative_to(temporary)
            and target.is_file() and not target.is_symlink()
            and not any(p.is_symlink() for p in target.parents), 'Expected runner-owned job environment file')
    actual = run('git', '-c', 'safe.directory=' + str(root), '-C', str(root),
                 'rev-parse', 'HEAD', seconds=10).stdout.strip()
    require(actual == expected_head, 'Checkout differs from the requested immutable commit')
    with target.open('a') as stream:
        stream.write('GIT_CONFIG_COUNT=1\nGIT_CONFIG_KEY_0=safe.directory\nGIT_CONFIG_VALUE_0=' + str(root) + '\n')
    return {'passed': True, 'head': actual, 'exact_safe_directory': str(root),
            'scope': 'subsequent steps of this one disposable job; no persistent/global Git configuration'}


def bind_python_cache_paths(cache, original_prefix):
    """Preserve a cached pip script and map its proven original interpreter path.

    Called only with /__t and /opt/hostedtoolcache by the hosted CLI. A missing
    cached distribution is left to the unchanged setup-python installer. Unknown
    shebangs, targets outside that exact Python version, and existing conflicting
    paths are fatal. No script, package, cache key or permission is rewritten.
    """
    cache, original_prefix = Path(cache), Path(original_prefix)
    require(cache.is_absolute() and cache.is_dir() and cache.resolve() == cache,
            'Expected canonical mounted tool cache')
    require(not any(p.is_symlink() for p in (cache, *cache.parents)), 'Symlink tool cache is refused')
    require(original_prefix.is_absolute() and original_prefix.parent.is_dir()
            and not any(p.is_symlink() for p in original_prefix.parents), 'Unsafe original tool-cache prefix')
    version = cache / 'Python/3.12.14/x64'
    pip = version / 'bin/pip'
    if not pip.exists():
        require(not pip.is_symlink(), 'Broken cached pip symlink')
        require(not version.exists(), 'Existing exact Python cache is incomplete; no fallback repair')
        return {'passed': True, 'mapping_created': False,
                'diagnosis': 'No cached distribution; absolute-prefix mismatch not established',
                'reason': 'Exact cached distribution absent; unchanged official installer must provide it'}
    require(pip.is_file() and not pip.is_symlink() and version.resolve() == version,
            'Cached pip must be a regular file in the exact version directory')
    before = pip.read_bytes()
    first = before.split(b'\n', 1)[0]
    require(first.startswith(b'#!') and len(first) < 1024, 'Unrecognized cached pip interpreter')
    interpreter = Path(first[2:].decode('ascii'))
    require(interpreter.name in {'python', 'python3', 'python3.12'}, 'Unexpected cached pip interpreter name')
    relative = Path('Python/3.12.14/x64/bin') / interpreter.name
    translated = cache / relative
    require(translated.is_file() and translated.resolve().is_relative_to(version),
            'Exact cached interpreter is missing or redirected')
    require(interpreter in (original_prefix / relative, translated),
            'Unrecognized cached interpreter prefix; no mapping attempted')
    probe = 'import json,sys; print(json.dumps(dict(version=list(sys.version_info[:3]),implementation=sys.implementation.name,executable=sys.executable,prefix=sys.prefix)))'
    identity = json.loads(run(str(translated), '-I', '-c', probe, seconds=15,
                              environ={**os.environ, 'LD_LIBRARY_PATH': str(version / 'lib')}).stdout)
    require(identity.get('version') == [3, 12, 14] and identity.get('implementation') == 'cpython',
            'Actual cached CPython identity mismatch')
    require(Path(identity.get('executable', '')).resolve() == translated.resolve()
            and Path(identity.get('prefix', '')).resolve() == version,
            'Actual cached CPython executable or prefix mismatch')
    created = False
    if interpreter == original_prefix / relative:
        if original_prefix.exists() or original_prefix.is_symlink():
            require(original_prefix.is_symlink() and original_prefix.resolve() == cache,
                    'Existing original tool-cache prefix conflicts; refusing overwrite')
        else:
            original_prefix.symlink_to(cache, target_is_directory=True)
            created = True
        require(interpreter.resolve() == translated.resolve(), 'Interpreter path mapping mismatch')
        reason = 'Original absolute shebang now resolves to the existing mounted interpreter'
    else:
        reason = 'Cached interpreter already uses the mounted path'
    require(pip.read_bytes() == before, 'Cached pip bytes changed during path mapping')
    return {'passed': True, 'mapping_created': created, 'reason': reason,
            'diagnosis': 'Missing absolute shebang prefix confirmed and mapped' if created
                         else 'Existing interpreter path resolves; missing-prefix hypothesis not established in this run',
            'cache': str(cache), 'original_prefix': str(original_prefix),
            'observed_shebang_interpreter': str(interpreter), 'resolved_interpreter': str(translated.resolve()),
            'pip_sha256': hashlib.sha256(before).hexdigest(),
            'interpreter_sha256': hashlib.sha256(translated.read_bytes()).hexdigest(),
            'actual_cpython_identity': identity,
            'identity_probe_library_path': str(version / 'lib'),
            'version_validation': 'The unchanged setup-python action and subsequent exact CI identity guard remain mandatory'}


def prepare_owned_pip_cache(home, uid):
    """Create only a missing owner cache; reject redirects or foreign ownership."""
    home = Path(home)
    require(home.is_absolute() and home.is_dir() and home.resolve() == home,
            'Expected canonical container user home')
    require(not any(p.is_symlink() for p in (home, *home.parents)), 'Symlink home is refused')
    paths = (home, home / '.cache', home / '.cache/pip')
    for path in paths:
        require(not path.is_symlink(), 'Symlink pip cache component is refused')
        if path.exists():
            require(path.is_dir() and path.stat().st_uid == uid,
                    'Pip cache component is not a current-user-owned directory')
    created = []
    for path in paths[1:]:
        if not path.exists():
            path.mkdir(mode=0o700)
            created.append(str(path))
        require(not path.is_symlink() and path.is_dir() and path.stat().st_uid == uid,
                'Pip cache ownership changed during preparation')
    return {'path': str(paths[-1]), 'owner_uid': uid, 'created_directories': created,
            'mode': oct(paths[-1].stat().st_mode & 0o777), 'symlinks': False,
            'existing_owner_mode_or_contents_changed': False}


def verify_browser(path):
    path = Path(path)
    base = Path('/ms-playwright')
    require(path.is_relative_to(base) and path.is_file(), 'Pinned browser is missing')
    require(not path.is_symlink() and not any(p.is_symlink() for p in path.parents),
            'Browser symlink is refused')
    require(os.access(path, os.X_OK), 'Browser is not executable')
    result = run(str(path), '--version', seconds=15)
    require(result.stdout.strip().split()[-1] == CHROMIUM, 'Actual Chromium version mismatch')
    libraries = run('ldd', str(path), seconds=15)
    require('not found' not in libraries.stdout + libraries.stderr, 'Missing Chromium OS dependency')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'version': result.stdout.strip(), 'dependencies_present': True}


def verify_container():
    release = platform.freedesktop_os_release()
    marker = json.loads(Path('/ms-playwright/.docker-info').read_text())
    validate_environment(release, platform.machine(), metadata.version('playwright'), marker, os.environ)
    import playwright
    descriptors = json.loads((Path(playwright.__file__).parent / 'driver/package/browsers.json').read_text())
    found = {b['name']: b for b in descriptors['browsers']
             if b['name'] in {'chromium', 'chromium-headless-shell'}}
    require(set(found) == {'chromium', 'chromium-headless-shell'}, 'Missing Chromium descriptors')
    require(all(b['revision'] == '1243' and b['browserVersion'] == CHROMIUM for b in found.values()),
            'Locked Chromium descriptor mismatch')
    browsers = [verify_browser('/ms-playwright/chromium-1243/chrome-linux64/chrome'),
                verify_browser('/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell')]
    for command in ('Xvfb', 'xvfb-run', 'xauth'):
        import shutil
        require(shutil.which(command) is not None, 'Native zoom requirement missing: ' + command)
    return {'passed': True, 'image_declared_by_workflow': IMAGE, 'image_marker': marker,
            'platform': release, 'browsers': browsers,
            'image_identity_evidence': 'Digest-pinned GitHub job container initialization log',
            'browser_assertions': 'The unchanged full browser matrix runs after this preflight'}


def measure_pulls():
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'Pull measurement requires the hosted trial')
    before = run('docker', 'image', 'inspect', IMAGE, check=False, seconds=30)
    if before.returncode:
        require(before.returncode == 1 and 'No such image' in before.stderr,
                'Unexpected Docker inspection failure')
    else:
        validate_image_inspection(json.loads(before.stdout)[0])
    initial_present = before.returncode == 0
    run('docker', 'pull', '--platform=linux/amd64', IMAGE, seconds=600)
    first_seconds = COMMANDS[-1]['seconds']
    inspected = json.loads(run('docker', 'image', 'inspect', IMAGE, seconds=30).stdout)
    require(len(inspected) == 1, 'Unexpected image inspection count')
    validate_image_inspection(inspected[0])
    run('docker', 'pull', '--platform=linux/amd64', IMAGE, seconds=600)
    warm_seconds = COMMANDS[-1]['seconds']
    repeated = json.loads(run('docker', 'image', 'inspect', IMAGE, seconds=30).stdout)
    require(len(repeated) == 1, 'Unexpected warm image count')
    validate_image_inspection(repeated[0])
    return {'passed': True, 'image': IMAGE, 'config': CONFIG,
            'image_present_before_first_pull': initial_present,
            'first_pull_class': 'already present' if initial_present else 'image absent; shared base layers may exist',
            'first_pull_seconds': first_seconds, 'same_runner_warm_pull_seconds': warm_seconds,
            'manifest_compressed_layer_bytes': COMPRESSED_LAYER_BYTES,
            'bytes_note': 'Manifest payload total; not a claim of measured network bytes',
            'warm_note': 'Same-runner image pull only; not a full warm-suite job or a new hosted VM',
            'image_size_uncompressed': inspected[0]['Size'], 'layers_retained': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('verify', 'pull-cost', 'bind-checkout', 'bind-python-cache'))
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    if args.action == 'bind-checkout':
        print(json.dumps(bind_checkout(ROOT, args.expected_head, os.environ), indent=2))
        return
    head = run('git', 'rev-parse', 'HEAD', seconds=10).stdout.strip()
    require(head == args.expected_head, 'Wrong immutable checkout')
    require(not run('git', 'diff', '--name-only', 'HEAD', seconds=10).stdout.strip(),
            'Tracked trial inputs changed')
    output = ROOT / 'runtime/playwright-container'
    output.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'head': head, 'action': args.action, 'diagnosis': 'Not established'}
    try:
        if args.action == 'bind-python-cache':
            require(os.environ.get('GITHUB_ACTIONS') == 'true'
                    and os.environ.get('READER_PLAYWRIGHT_IMAGE') == IMAGE
                    and os.environ.get('RUNNER_TOOL_CACHE') == '/__t'
                    and os.environ.get('PIP_CACHE_DIR') == '/root/.cache/pip'
                    and os.geteuid() == 0,
                    'Tool-cache binding requires the declared hosted container mount')
            binding = bind_python_cache_paths('/__t', '/opt/hostedtoolcache')
            report.update({k: v for k, v in binding.items() if k != 'passed'})
            report['pip_cache'] = prepare_owned_pip_cache('/root', os.geteuid())
            report['passed'] = True
        else:
            report.update(verify_container() if args.action == 'verify' else measure_pulls())
    finally:
        report['commands'] = COMMANDS
        with (output / (args.action + '.json')).open('x') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'commands'}, indent=2))


if __name__ == '__main__':
    main()
