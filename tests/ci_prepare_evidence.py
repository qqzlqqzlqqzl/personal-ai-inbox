"""Prepare only this Reader job's upload paths; preserve all existing bytes."""
import argparse
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_PATHS = (
    'artifacts/ci-tested-source.json', 'artifacts/ci-python.xml',
    'artifacts/ci-reader-identity.json', 'artifacts/review-overlay-input/',
    'artifacts/browser-admission.json', 'runtime/retained-browser-builds/',
    'runtime/browser-acceptance.json', 'runtime/history-*.png', 'runtime/quota-*.png',
    'runtime/browser-failure.png', 'runtime/cjk-font/', 'runtime/review-console/',
    'runtime/review-reading/', 'runtime/note-session-privacy/', 'runtime/note-request-recovery/',
    'runtime/note-legacy-*/', 'runtime/search-ime-*/', 'runtime/sort-*/',
    'runtime/sort-component.json', 'runtime/mobile-reading-*/',
    'runtime/mobile-reading-metrics.json', 'runtime/reader-panel-transition/',
    'runtime/calendar-*/', 'runtime/browser-build/',
    'runtime/dev-fixture-workspace-browser/',
    'runtime/navigation-focus-*/',
    'runtime/query-result-ownership/', 'runtime/reading-focus-*/',
    'runtime/console-header/',
    'runtime/quality-consumer/',
    'runtime/source-catalog/',
    'runtime/native-zoom/result.json', 'runtime/native-zoom/native-metrics.json',
    'runtime/native-zoom/native-font-coverage.json', 'runtime/native-zoom/native-200.png',
    'runtime/native-zoom/native-200-after300ms.png', 'runtime/native-zoom/native-stability.json',
    'runtime/native-zoom/native-200-corner-probe.png',
    'runtime/native-zoom/native-200-corner-probe.surface.json',
    'runtime/native-zoom/native-200-corner-probe.proof.json',
    'runtime/native-zoom/native-200.surface.json',
    'runtime/native-zoom/native-200-after300ms.surface.json',
)


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, timeout=10).strip()


def matches(path, pattern):
    if pattern.endswith('/'):
        return any(fnmatch.fnmatchcase('/'.join(path.split('/')[:i]) + '/', pattern)
                   for i in range(1, len(path.split('/'))))
    return fnmatch.fnmatchcase(path, pattern)


def prepare(root, retained_root, expected_head, *, patterns=UPLOAD_PATHS):
    root, retained_root = Path(root).resolve(), Path(retained_root).resolve()
    if root == Path('/home/ubuntu/ai-news') or (root / '.private').exists():
        raise RuntimeError('Refuses deployment/private configuration')
    if git(root, 'rev-parse', 'HEAD') != expected_head:
        raise RuntimeError('Unexpected immutable checkout')
    tracked = git(root, 'ls-files', '-z').split('\0')
    overlap = [path for path in tracked if path and any(matches(path, p) for p in patterns)]
    if overlap:
        raise RuntimeError('Upload paths overlap tracked historical files: ' + ', '.join(overlap))
    if not retained_root.is_dir() or retained_root == Path('/') or retained_root.is_relative_to(root):
        raise RuntimeError('Retention root must be an existing directory outside checkout')
    candidates = set()
    for pattern in patterns:
        candidates.update(root.glob(pattern.rstrip('/')))
    for path in candidates:
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root):
            raise RuntimeError('Symlink upload input is refused')
    retained = retained_root / ('reader-prior-evidence-' + uuid.uuid4().hex)
    retained.mkdir()
    moved = []
    for path in sorted(candidates, key=lambda p: (len(p.parts), str(p))):
        if any(path.is_relative_to(parent) for parent in moved):
            continue
        target = retained / path.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
        moved.append(path)
    report = {'head': expected_head, 'tree': git(root, 'rev-parse', 'HEAD^{tree}'),
              'src_tree': git(root, 'rev-parse', 'HEAD:src'),
              'upload_paths': list(patterns), 'tracked_overlap': [],
              'previous_outputs_retained': [p.relative_to(root).as_posix() for p in moved],
              'retained_at': str(retained), 'run_id': os.environ.get('GITHUB_RUN_ID'),
              'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'), 'prepared': True}
    receipt = root / 'artifacts/ci-tested-source.json'
    receipt.parent.mkdir(exist_ok=True)
    with receipt.open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(ROOT, os.environ['RUNNER_TEMP'], args.expected_head), indent=2))


if __name__ == '__main__':
    main()
