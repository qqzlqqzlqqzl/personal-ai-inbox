"""Fixed same-repository inputs; ten serial samples in five AB/BA groups.

This is regression measurement, not an approval to release the candidate.
The producer's full workflow outcome is recorded separately from build identity.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import zipfile

import reader_loading_ci as base
from reader_loading_fixture import admit_build, bound_read, checked_directory, require
from reader_loading_performance import browser_env, save_new
from reader_loading_transport import GZIP
from compare_reader_loading import compare

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / 'tests/fixtures/reader-loading-ab-inputs.json'
SIDES = ('baseline', 'candidate')
BROWSER_SHA = '8c599d43aec53f2460a31ae2f4af6bd863f8258b34ff519564bc5d4726bfaa1e'


def load_spec():
    value = json.loads(bound_read(SPEC.parent, SPEC.name, 16000))
    require(value['schema'] == 1 and value['repository'] == base.REPOSITORY, 'wrong A/B repository/schema')
    require(set(value['inputs']) == set(SIDES), 'both fixed inputs required')
    for spec in value['inputs'].values():
        for key in ('artifact_id', 'artifact_bytes', 'files', 'producer_run', 'producer_attempt'):
            require(type(spec[key]) is int and spec[key] > 0, 'invalid fixed integer: ' + key)
        require(spec['artifact_bytes'] <= 32 * 1024 * 1024 and spec['files'] <= 1024, 'input exceeds existing budget')
        for key in ('artifact_sha256', 'manifest_sha256'):
            require(re.fullmatch('[a-f0-9]{64}', spec[key]), 'invalid SHA256')
        for key in ('head', 'tree', 'src'):
            require(re.fullmatch('[a-f0-9]{40}', spec[key]), 'invalid Git identity')
        require(spec['producer_conclusion'] in ('success', 'failure'), 'producer must be terminal and reviewed')
    old = value['inputs']['baseline']
    require((old['artifact_id'], old['artifact_sha256'], old['manifest_sha256'], old['files']) ==
            (base.ARTIFACT, base.ARTIFACT_SHA, base.MANIFEST_SHA, 125), 'fixed baseline changed')
    return value


def schedule():
    return [{'ordinal': 2 * (group - 1) + index + 1, 'pair': group, 'side': side}
            for group in range(1, 6)
            for index, side in enumerate(SIDES if group % 2 else tuple(reversed(SIDES)))]


def instrument_manifest():
    names = subprocess.check_output(['git', 'ls-files', 'tests/reader_loading*.py',
              'tests/compare_reader_loading.py', 'tests/fixtures/reader-loading-ab-inputs.json'],
              cwd=ROOT, text=True, timeout=15).splitlines()
    require(len(names) >= 8 and 'tests/reader_loading_ab_ci.py' in names, 'instrument files must be tracked')
    rows = [{'path': name, 'sha256': hashlib.sha256(bound_read(ROOT, name)).hexdigest()} for name in sorted(names)]
    return {'files': rows, 'sha256': hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}


def assert_checkout(root):
    source = json.loads(bound_read(root / 'evidence', 'source.json'))
    require(base.git('rev-parse', 'HEAD') == source['head'] and not base.git('diff', '--name-only', 'HEAD'),
            'immutable checkout changed')
    return source


def gh_json(endpoint):
    # Endpoints are constructed only from the reviewed fixed two-input table.
    require(endpoint.startswith('repos/' + base.REPOSITORY + '/actions/'), 'unexpected API repository')
    process = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
              '-H', 'X-GitHub-Api-Version: 2022-11-28', endpoint], stdout=subprocess.PIPE,
              stderr=subprocess.DEVNULL, timeout=30, check=False)
    require(process.returncode == 0 and len(process.stdout) <= 1024 * 1024, 'fixed metadata read failed or exceeded budget')
    return json.loads(process.stdout)


def verify_metadata(spec, artifact, producer):
    require(artifact['id'] == spec['artifact_id'] and artifact['size_in_bytes'] == spec['artifact_bytes'] and
            artifact['digest'] == 'sha256:' + spec['artifact_sha256'] and artifact['expired'] is False,
            'immutable artifact metadata differs or expired')
    require(artifact['workflow_run']['id'] == spec['producer_run'] and
            artifact['workflow_run']['head_sha'] == spec['head'], 'artifact producer differs')
    require(producer['id'] == spec['producer_run'] and producer['head_sha'] == spec['head'] and
            producer['run_attempt'] == spec['producer_attempt'] and producer['status'] == 'completed' and
            producer['conclusion'] == spec['producer_conclusion'], 'exact producer outcome differs')
    require(producer['repository']['full_name'] == base.REPOSITORY, 'cross-repository producer refused')


def download_one(root, side, spec):
    target = root / 'inputs' / side
    target.mkdir(mode=0o700)
    artifact = gh_json(f'repos/{base.REPOSITORY}/actions/artifacts/{spec["artifact_id"]}')
    producer = gh_json(f'repos/{base.REPOSITORY}/actions/runs/{spec["producer_run"]}/attempts/{spec["producer_attempt"]}')
    verify_metadata(spec, artifact, producer)
    save_new(root / 'evidence' / (side + '-producer.json'), {
        'artifact_id': artifact['id'], 'producer_run': producer['id'], 'producer_attempt': producer['run_attempt'],
        'producer_head': producer['head_sha'], 'producer_conclusion': producer['conclusion'],
        'full_regression_passed': producer['conclusion'] == 'success', 'build_identity_separately_verified': False})
    started = time.monotonic()
    size = 0
    process = None
    args = ['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            f'repos/{base.REPOSITORY}/actions/artifacts/{spec["artifact_id"]}/zip']
    with (target / 'artifact.zip').open('xb') as stream, (root / 'work' / (side + '-download.stderr')).open('xb') as errors:
        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = 90 - (time.monotonic() - started)
                    require(remaining > 0, 'artifact download deadline exceeded')
                    if not selector.select(min(1, remaining)):
                        continue
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    require(size <= spec['artifact_bytes'], 'artifact exceeded exact byte budget')
                    stream.write(chunk)
            require(process.wait(timeout=max(1, 90 - (time.monotonic() - started))) == 0, 'artifact download failed')
        finally:
            if process is not None:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                process.stdout.close()
    raw = bound_read(target, 'artifact.zip', spec['artifact_bytes'])
    require(size == spec['artifact_bytes'] and hashlib.sha256(raw).hexdigest() == spec['artifact_sha256'], 'artifact byte identity differs')


def unpack_one(target, spec):
    raw = bound_read(target, 'artifact.zip', spec['artifact_bytes'])
    require(len(raw) == spec['artifact_bytes'] and hashlib.sha256(raw).hexdigest() == spec['artifact_sha256'], 'input archive changed')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        require(len(infos) <= 1024 and sum(i.file_size for i in infos) <= 64 * 1024 * 1024, 'archive budget exceeded')
        require(len({i.filename for i in infos}) == len(infos) and archive.testzip() is None, 'duplicate member or bad CRC')
        identity = json.loads(archive.read('artifacts/ci-reader-identity.json'))
        require(identity.get('passed') is True and (identity.get('head'), identity.get('tree'), identity.get('src_tree')) ==
                (spec['head'], spec['tree'], spec['src']), 'exact build source identity differs')
        prefix = 'runtime/browser-build/'
        files = {}
        rows = []
        for info in infos:
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            name = info.filename[len(prefix):]
            require(name and str(PurePosixPath(name)) == name and not name.startswith('/') and '\\' not in name and
                    '..' not in PurePosixPath(name).parts and stat.S_ISREG(info.external_attr >> 16), 'unsafe build member')
            data = archive.read(info)
            files[name] = data
            rows.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        manifest = (json.dumps(rows, ensure_ascii=False, indent=2) + '\n').encode()
        require(len(rows) == spec['files'] and hashlib.sha256(manifest).hexdigest() == spec['manifest_sha256'], 'actual candidate file set/manifest differs')
        build = target / 'build'
        build.mkdir(mode=0o700)
        for name, data in files.items():
            path = build / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(data)
        with (target / 'manifest.json').open('xb') as stream:
            stream.write(manifest)
    admit_build(build, target / 'manifest.json', spec['manifest_sha256'])
    return {'spec': spec, 'crc': 'PASSED', 'files': len(rows), 'expanded_build_bytes': sum(len(v) for v in files.values()),
            'build_identity_passed': True, 'full_regression_passed': spec['producer_conclusion'] == 'success'}


def validate_order(observations):
    require(len(observations) == 10, 'ten completed serial samples required')
    previous_end = None
    for expected, observed in zip(schedule(), observations):
        require(all(observed.get(k) == v and type(observed.get(k)) is type(v) for k, v in expected.items()), 'A/B chronology differs')
        start = observed['started_monotonic_ns']; end = observed['finished_monotonic_ns']
        require(type(start) is int and type(end) is int and 0 <= start < end and
                (previous_end is None or previous_end <= start), 'overlapping or invalid sample interval')
        require(observed['status'] == 'PASSED' and observed['exit_code'] == 0, 'failed sample cannot compare')
        previous_end = end


def measure(root, mode):
    source = assert_checkout(root)
    require(source['mode'] == mode and mode in base.MODES, 'fixed mode changed')
    spec = load_spec()
    instruments = instrument_manifest()
    save_new(root / 'evidence/instrument-manifest.json', instruments)
    observations = []
    results = {side: [] for side in SIDES}
    deadline = time.monotonic() + 590  # Same whole measurement budget; never retry/extend on failure.
    report = {'status': 'FAILED', 'schedule': schedule(), 'observations': observations, 'driver': instruments,
              'source': source, 'inputs': spec, 'mode': mode, 'comparison_kind': 'REGRESSION_CANDIDATE',
              'cold_definition': 'new context and new browser process per sample; OS cache is not claimed cold',
              'same_vm': True, 'serial': True, 'physical_device': False, 'product_speedup_claimed': False}
    try:
        for item in schedule():
            assert_checkout(root)
            require(instrument_manifest() == instruments, 'instrument bytes changed between variants')
            side = item['side']; identity = spec['inputs'][side]
            folder = root / 'work' / f'sample-{item["ordinal"]:02d}-{side}'
            folder.mkdir(mode=0o700)
            input_root = root / 'inputs' / side
            args = [sys.executable, '-B', str(ROOT / 'tests/reader_loading_ab_sample.py'), '--pair-index', str(item['pair']),
                    '--build', str(input_root / 'build'), '--manifest', str(input_root / 'manifest.json'),
                    '--manifest-sha', identity['manifest_sha256'], '--artifact-zip', str(input_root / 'artifact.zip'),
                    '--artifact-sha', identity['artifact_sha256'], '--source-tree', identity['tree'], '--phase', side,
                    '--output-parent', str(folder), '--input', 'touch' if mode == 'touch' else 'keyboard', '--transport-profile', GZIP]
            if mode == 'weak-network':
                args.append('--weak-network')
            private = folder / 'driver'
            private.mkdir(mode=0o700)
            env = browser_env(private)
            env.update(PATH=os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1',
                       PLAYWRIGHT_BROWSERS_PATH=str(checked_directory(os.environ['PLAYWRIGHT_BROWSERS_PATH'])))
            observation = {**item, 'started_monotonic_ns': time.monotonic_ns(), 'status': 'FAILED', 'exit_code': None}
            save_new(root / 'evidence' / f'sample-{item["ordinal"]:02d}-started.json', observation)
            process = None
            try:
                remaining = min(90, deadline - time.monotonic())
                require(remaining > 0, 'whole A/B deadline exhausted')
                with (root / 'work' / f'sample-{item["ordinal"]:02d}.stdout').open('xb') as stdout, \
                        (root / 'work' / f'sample-{item["ordinal"]:02d}.stderr').open('xb') as stderr:
                    process = subprocess.Popen(args, env=env, stdout=stdout, stderr=stderr, start_new_session=True)
                    code = process.wait(timeout=remaining)
                observation['exit_code'] = code
                require(code == 0, 'actual sample failed; retained original evidence')
                output = list(folder.glob('reader-perf-*'))
                require(len(output) == 1, 'expected one actual sample output')
                base.private_directory(output[0])
                value = json.loads(bound_read(output[0], 'result.json', 16 * 1024 * 1024))
                require(value['status'] == 'PASSED' and value['phase'] == side and value['pairs_requested'] == 1 and
                        len(value['pairs']) == 1 and value['pairs'][0]['pair'] == item['pair'], 'single actual sample differs')
                require(value['browser_executable_sha256'] == BROWSER_SHA and value['browser_version'] == '153.0.8010.12' and
                        value['playwright'] == '1.63.0' and value['python'].split()[0] == '3.12.14', 'real fixed browser/toolchain differs')
                require((value['identity']['head'], value['identity']['tree'], value['identity']['src']) ==
                        (identity['head'], identity['tree'], identity['src']), 'variant build identity differs')
                results[side].append(value)
                observation['status'] = 'PASSED'
            finally:
                if process is not None and process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=2)
                observation['finished_monotonic_ns'] = time.monotonic_ns()
                observations.append(observation)
                save_new(root / 'evidence' / f'sample-{item["ordinal"]:02d}-finished.json', observation)
                collect_sample(root, folder, item['ordinal'])
        validate_order(observations)
        before, after = (aggregate(results[side], side, instruments) for side in SIDES)
        require(all(before[key] == after[key] for key in ('browser_executable_sha256', 'playwright', 'python', 'instrument_manifest')),
                'A/B executable, interpreter or instruments differ')
        save_new(root / 'evidence/baseline-result.json', before)
        save_new(root / 'evidence/candidate-result.json', after)
        comparison = compare(before, after)
        comparison.update(schedule=observations, instrument_sha256=instruments['sha256'], input_specs=spec,
                          candidate_full_regression_passed=spec['inputs']['candidate']['producer_conclusion'] == 'success')
        save_new(root / 'evidence/comparison.json', comparison)
        report['status'] = 'PASSED'
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        save_new(root / 'evidence/ab-result.json', report)


def aggregate(samples, side, instruments):
    require(len(samples) == 5, 'five samples per side required')
    first = samples[0]
    keys = ('identity', 'measurement_contract', 'transport_profile', 'scenario', 'input', 'weak_network',
            'browser_version', 'browser_executable_sha256', 'playwright', 'python')
    for sample in samples:
        require(sample['status'] == 'PASSED' and sample['phase'] == side and sample['pairs_requested'] == 1 and len(sample['pairs']) == 1,
                'only actual one-pair samples can aggregate')
        require(all(sample[key] == first[key] for key in keys), 'variant conditions or source changed')
    result = {key: first[key] for key in keys}
    result.update(status='PASSED', phase=side, pairs_requested=5,
                  pairs=[sample['pairs'][0] for sample in samples], instrument_manifest=instruments)
    require([p['pair'] for p in result['pairs']] == [1, 2, 3, 4, 5], 'missing/repeated pair identities')
    return result


def collect_sample(root, folder, ordinal):
    target = root / 'evidence' / f'sample-{ordinal:02d}'
    target.mkdir(mode=0o700)
    rows = []
    for output in sorted(folder.glob('reader-perf-*')):
        base.private_directory(output)
        for path in sorted(output.iterdir()):
            if not re.fullmatch(r'(?:result|boundary|pair-[1-5])\.json|pair-[1-5]-failure\.png', path.name):
                continue
            raw = bound_read(output, path.name, 16 * 1024 * 1024)
            name = output.name + '-' + path.name
            with (target / name).open('xb') as stream:
                stream.write(raw)
            rows.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    save_new(target / 'manifest.json', rows)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'inputs', 'measure', 'finish'))
    parser.add_argument('--root'); parser.add_argument('--expected-head'); parser.add_argument('--mode', choices=base.MODES)
    args = parser.parse_args()
    if args.action == 'prepare':
        root = base.prepare(args.expected_head, args.mode)
        save_new(root / 'evidence/ab-input-spec.json', load_spec())
        return 0
    root = base.private_directory(args.root)
    receipt = {'action': args.action, 'status': 'FAILED'}
    try:
        assert_checkout(root)
        if args.action == 'inputs':
            for side, spec in load_spec()['inputs'].items():
                download_one(root, side, spec)
                save_new(root / 'evidence' / (side + '-input.json'), unpack_one(root / 'inputs' / side, spec))
        elif args.action == 'measure':
            measure(root, args.mode)
        elif args.action == 'finish':
            path = root / 'evidence/ab-result.json'
            status = json.loads(bound_read(path.parent, path.name))['status'] if path.exists() else 'NOT_RUN'
            save_new(root / 'evidence/ab-job-outcome.json', {'job_status_before_upload': os.environ['READER_LOADING_JOB_STATUS'],
                     'measurement_status': status, 'synthetic_only': True, 'product_speedup_claimed': False})
        receipt['status'] = 'PASSED'
        return 0
    except Exception as exc:
        receipt.update(error_type=type(exc).__name__, error=str(exc))
        print('A/B action failed:', args.action, type(exc).__name__, file=sys.stderr)
        return 1
    finally:
        save_new(root / 'evidence' / ('ab-' + args.action + '-status.json'), receipt)


if __name__ == '__main__':
    raise SystemExit(main())
