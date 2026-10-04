"""No real browser/network in these input/order/negative contract checks."""
import ast
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import reader_loading_ab_ci as ab


class InputContracts(unittest.TestCase):
    def test_frozen_input_identities_and_failed_producer_are_explicit(self):
        spec = ab.load_spec()
        self.assertEqual(spec['inputs']['candidate']['producer_conclusion'], 'failure')
        self.assertEqual(spec['inputs']['candidate']['head'], '7554d64663d11cebb131c7dc8ec7412e36a36b7d')
        self.assertEqual(spec['inputs']['baseline']['artifact_id'], 11299074112)

    def metadata(self):
        spec = ab.load_spec()['inputs']['candidate']
        artifact = {'id': spec['artifact_id'], 'size_in_bytes': spec['artifact_bytes'],
                    'digest': 'sha256:' + spec['artifact_sha256'], 'expired': False,
                    'workflow_run': {'id': spec['producer_run'], 'head_sha': spec['head']}}
        producer = {'id': spec['producer_run'], 'head_sha': spec['head'], 'run_attempt': 1,
                    'status': 'completed', 'conclusion': 'failure', 'repository': {'full_name': ab.base.REPOSITORY}}
        return spec, artifact, producer

    def test_exact_metadata_positive(self):
        ab.verify_metadata(*self.metadata())

    def test_metadata_wrong_head_digest_size_attempt_repo_and_conclusion_refused(self):
        changes = [('artifact', 'id', 1), ('artifact', 'size_in_bytes', 1), ('artifact', 'digest', 'sha256:' + '0'*64),
                   ('artifact', 'expired', True), ('producer', 'id', 1), ('producer', 'head_sha', '0'*40),
                   ('producer', 'run_attempt', 2), ('producer', 'status', 'in_progress'),
                   ('producer', 'conclusion', 'success'), ('producer', 'repository', {'full_name': 'other/repo'}),
                   ('artifact', 'workflow_run', {'id': 1, 'head_sha': '0'*40})]
        for target, key, value in changes:
            with self.subTest(target=target, key=key):
                spec, artifact, producer = self.metadata()
                (artifact if target == 'artifact' else producer)[key] = value
                with self.assertRaises(ValueError):
                    ab.verify_metadata(spec, artifact, producer)

    def sample_archive(self, *, files=None, identity_change=None, member_mode=0o100644):
        files = files or {'index.html': b'<!doctype html>synthetic', 'assets/test.js': b'export default 1'}
        identity = {'head': '1'*40, 'tree': '2'*40, 'src_tree': '3'*40, 'passed': True}
        identity.update(identity_change or {})
        rows = [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()]
        manifest = (json.dumps(rows, ensure_ascii=False, indent=2) + '\n').encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('artifacts/ci-reader-identity.json', json.dumps(identity))
            for name, data in files.items():
                info = zipfile.ZipInfo('runtime/browser-build/' + name)
                info.external_attr = member_mode << 16
                archive.writestr(info, data)
        raw = stream.getvalue()
        spec = {'artifact_bytes': len(raw), 'artifact_sha256': hashlib.sha256(raw).hexdigest(),
                'manifest_sha256': hashlib.sha256(manifest).hexdigest(), 'files': len(files),
                'head': '1'*40, 'tree': '2'*40, 'src': '3'*40, 'producer_conclusion': 'failure'}
        root = Path(tempfile.mkdtemp(prefix='reader-ab-contract-'))
        (root / 'artifact.zip').write_bytes(raw)
        return root, spec

    def test_candidate_exact_file_count_is_not_baseline_125(self):
        root, spec = self.sample_archive()
        with patch.object(ab, 'admit_build') as admission:
            result = ab.unpack_one(root, spec)
        self.assertEqual(result['files'], 2)
        self.assertFalse(result['full_regression_passed'])
        admission.assert_called_once()
        self.assertEqual((root/'build/assets/test.js').read_bytes(), b'export default 1')
        with self.assertRaises(FileExistsError), patch.object(ab, 'admit_build'):
            ab.unpack_one(root, spec)

    def test_wrong_count_manifest_identity_and_symlink_member_fail_before_write(self):
        for kind in ('count', 'manifest', 'identity', 'mode', 'traversal'):
            with self.subTest(kind=kind):
                options = {'identity_change': {'head': '4'*40}} if kind == 'identity' else {}
                if kind == 'mode': options['member_mode'] = 0o120777
                if kind == 'traversal': options['files'] = {'../escape': b'x'}
                root, spec = self.sample_archive(**options)
                if kind == 'count': spec['files'] += 1
                if kind == 'manifest': spec['manifest_sha256'] = '0'*64
                with self.assertRaises(ValueError): ab.unpack_one(root, spec)
                self.assertFalse((root/'build').exists())

    def test_changed_archive_and_input_symlink_are_refused(self):
        root, spec = self.sample_archive()
        (root/'artifact.zip').write_bytes(b'changed')
        with self.assertRaises(ValueError): ab.unpack_one(root, spec)
        other = Path(tempfile.mkdtemp(prefix='reader-ab-link-contract-'))
        (other/'artifact.zip').symlink_to(root/'artifact.zip')
        with self.assertRaises((ValueError, OSError)): ab.unpack_one(other, spec)


class OrderContracts(unittest.TestCase):
    def observations(self):
        return [{**row, 'started_monotonic_ns': 10*row['ordinal'], 'finished_monotonic_ns': 10*row['ordinal']+1,
                 'status': 'PASSED', 'exit_code': 0} for row in ab.schedule()]

    def test_actual_schedule_is_ab_ba_ab_ba_ab(self):
        self.assertEqual([r['side'][0] for r in ab.schedule()], list('bccbbccbbc'))
        self.assertEqual([r['pair'] for r in ab.schedule()], [1,1,2,2,3,3,4,4,5,5])
        ab.validate_order(self.observations())

    def test_missing_repeat_reordered_overlap_and_failed_sample_are_refused(self):
        for kind in ('missing','repeat','reorder','overlap','failed','bool-time'):
            with self.subTest(kind=kind):
                rows=self.observations()
                if kind=='missing': rows.pop()
                if kind=='repeat': rows[3]=copy.deepcopy(rows[2])
                if kind=='reorder': rows[0],rows[1]=rows[1],rows[0]
                if kind=='overlap': rows[1]['started_monotonic_ns']=rows[0]['started_monotonic_ns']
                if kind=='failed': rows[3]['status']='FAILED'
                if kind=='bool-time': rows[0]['started_monotonic_ns']=True
                with self.assertRaises(ValueError):ab.validate_order(rows)

    def test_collect_excludes_profile_sentinel_and_raw_stderr(self):
        root=Path(tempfile.mkdtemp(prefix='reader-ab-collection-'))
        (root/'evidence').mkdir(mode=0o700);folder=root/'work';folder.mkdir(mode=0o700)
        sample=folder/'reader-perf-test';sample.mkdir(mode=0o700)
        (sample/'result.json').write_text('{}');(sample/'browser-profile').mkdir();(sample/'token.stderr').write_text('private-canary')
        ab.collect_sample(root,folder,1)
        names=[p.name for p in (root/'evidence/sample-01').iterdir()]
        self.assertEqual(sorted(names),['manifest.json','reader-perf-test-result.json'])

    def test_single_sample_main_rejects_invalid_index_before_browser(self):
        import reader_loading_performance as performance
        for index in (False,True,0,6,'1'):
            with self.subTest(index=index),self.assertRaises(ValueError):performance.main([],pair_index=index)


class WorkflowContracts(unittest.TestCase):
    def test_same_pins_minimal_read_permissions_and_no_job_runner_context(self):
        text=(ab.ROOT/'.github/workflows/reader-loading-ab.yml').read_text()
        self.assertIn('runs-on: ubuntu-22.04',text)
        self.assertIn('actions: read',text);self.assertNotRegex(text, r':\s*write(?:\s|$)')
        self.assertNotIn('secrets.',text)
        self.assertEqual(text.count('GH_TOKEN:'),1)
        self.assertIn('matrix:\n        mode: [keyboard, touch, weak-network]',text)
        self.assertIn("if: always() && steps.prepare.outcome == 'success'",text)
        self.assertIn('600s python -B tests/reader_loading_ab_ci.py measure',text)
        self.assertIn("python-version: '3.12.14'",text)
        self.assertIn('ci.py identity',text);self.assertIn('ci.py font-install',text)
        job_env=text.split('    env:\n',1)[1].split('    steps:',1)[0]
        self.assertNotIn('runner.',job_env)

    def test_aggregate_preserves_variant_identity_and_rejects_missing_or_changed_samples(self):
        from test_reader_loading_performance import SamplingContract
        def values(side):
            report=SamplingContract().row(side)
            report.update(browser_executable_sha256=ab.BROWSER_SHA,playwright='1.63.0',python='3.12.14')
            samples=[]
            for row in report['pairs']:
                sample=copy.deepcopy(report);sample.update(pairs_requested=1,pairs=[row]);samples.append(sample)
            return samples
        samples=values('baseline')
        result=ab.aggregate(samples,'baseline',{'sha256':'synthetic'})
        self.assertEqual(len(result['pairs']),5)
        self.assertEqual(result['identity'],samples[0]['identity'])
        for kind in ('missing','repeat','driver','source','phase','partial'):
            with self.subTest(kind=kind):
                rows=values('baseline')
                if kind=='missing':rows.pop()
                if kind=='repeat':rows[3]['pairs'][0]['pair']=2
                if kind=='driver':rows[3]['browser_executable_sha256']='0'*64
                if kind=='source':rows[3]['identity']={'head':'different'}
                if kind=='phase':rows[3]['phase']='candidate'
                if kind=='partial':rows[3]['status']='FAILED'
                with self.assertRaises(ValueError):ab.aggregate(rows,'baseline',{'sha256':'synthetic'})


if __name__=='__main__':unittest.main()
