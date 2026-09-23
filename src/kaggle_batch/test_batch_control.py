"""Failure/recovery tests. Test artifacts are retained, never deleted."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import uuid
from batch_control import Controller

TEMPLATE = 'MANIFEST = None\n'

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        self.calls = []
        self.remote = 'COMPLETE'
        self.fail_push = False
        self.fail_download = False
        self.omit_results = False
        self.control = Controller(self.path,'owner',self.client)
        self.manifest = {'runtime_source':'owner/runtime','session_timeout':600,
            'items':[{'id':'analysis-1','input_hash':'version-a',
                      'messages':[{'role':'user','content':'public test fixture'}]}]}

    def client(self,args,timeout):
        self.assertGreater(timeout,0)
        self.calls.append(args)
        if args[1]=='push':
            if self.fail_push:
                raise subprocess.TimeoutExpired('simulated-client',90)
            return 'Kernel version 1 successfully pushed.'
        if args[1]=='status':
            return 'has status "KernelWorkerStatus.'+self.remote+'"'
        if args[1]=='output':
            if self.fail_download:
                raise ConnectionError('simulated interruption')
            if self.omit_results:
                return 'no output files'
            folder = Path(args[-1])
            manifest = json.loads((folder.parent/'manifest.json').read_text())
            value = {'batch_id':folder.parent.name,'manifest_hash':manifest['manifest_hash'],
                     'id':'analysis-1','input_hash':'version-a','status':'ok','content':'{}'}
            (folder/'results.jsonl').write_text(json.dumps(value)+'\n')
            return 'downloaded'
        raise AssertionError(args)

    def prepare(self):
        return self.control.prepare(self.manifest,TEMPLATE)

    def test_empty_queue_does_not_submit(self):
        self.assertIsNone(self.control.prepare({**self.manifest,'items':[]},TEMPLATE))
        self.assertEqual([],self.calls)

    def test_retire_only_never_submitted_batch_preserves_files_and_cannot_launch(self):
        batch=self.prepare()
        self.control.retire(batch,'User selected existing public model cache')
        self.control.submit(batch)
        self.assertEqual('retired',self.control.row(batch)['state'])
        self.assertTrue((self.path/batch/'manifest.json').exists())
        self.assertFalse(self.calls)

    def test_active_or_uncertain_batch_cannot_be_retired(self):
        batch=self.prepare()
        for state in ('submitting','submit_unknown','submitted','running','terminal'):
            with self.subTest(state=state):
                self.control._set(batch,state)
                with self.assertRaises(ValueError):
                    self.control.retire(batch,'Model change')

    def test_required_cache_absence_rejected_before_gpu_submission(self):
        with self.assertRaises(ValueError):
            self.control.prepare({**self.manifest,'require_model_cache':True},TEMPLATE)
        self.assertFalse(self.calls)

    def test_cached_batch_disables_external_network_and_attaches_dataset(self):
        batch=self.control.prepare({**self.manifest,'require_model_cache':True,
                                    'dataset_sources':['owner/exact-model']},TEMPLATE)
        metadata=json.loads((self.path/batch/'kernel-metadata.json').read_text())
        self.assertFalse(metadata['enable_internet'])
        self.assertEqual(['owner/exact-model'],metadata['dataset_sources'])

    def test_isolated_kaggle_python_used_without_changing_business_runtime(self):
        control=Controller(self.path,'owner',kaggle_python='/isolated/bin/python')
        with patch('batch_control.subprocess.run') as run:
            run.return_value.returncode=0
            run.return_value.stdout='safe status'
            control._cli(['kernels','status','owner/job'],45)
        self.assertEqual('/isolated/bin/python',run.call_args.args[0][0])
        self.assertEqual(45,run.call_args.kwargs['timeout'])

    def test_same_manifest_and_repeated_submit_start_once(self):
        batch = self.prepare()
        self.assertEqual(batch,self.prepare())
        self.control.submit(batch)
        self.control.submit(batch)
        self.assertEqual(1,len([c for c in self.calls if c[1]=='push']))

    def test_uncertain_submit_reconciles_without_duplicate_gpu(self):
        batch = self.prepare()
        self.fail_push = True
        with self.assertRaises(RuntimeError):
            self.control.submit(batch)
        self.assertEqual('submit_unknown',self.control.row(batch)['state'])
        self.control.submit(batch)
        self.remote = 'RUNNING'
        self.assertEqual('running',self.control.status(batch)['state'])
        self.assertEqual(1,len([c for c in self.calls if c[1]=='push']))

    def test_second_batch_blocked_until_first_terminal(self):
        first = self.prepare()
        self.control.submit(first)
        second = self.control.prepare({**self.manifest,'attempt':2},TEMPLATE)
        with self.assertRaises(RuntimeError):
            self.control.submit(second)
        self.control.status(first)
        self.control.submit(second)
        self.assertEqual(2,len([c for c in self.calls if c[1]=='push']))

    def test_download_interruption_retries_without_gpu(self):
        batch = self.prepare()
        self.control.submit(batch)
        self.fail_download = True
        with self.assertRaises(ConnectionError):
            self.control.download(batch)
        self.assertEqual('terminal',self.control.row(batch)['state'])
        self.fail_download = False
        self.assertEqual([],self.control.download(batch)['missing_ids'])
        self.control.download(batch)
        self.assertEqual(1,len([c for c in self.calls if c[1]=='push']))

    def test_wrong_input_version_is_never_accepted(self):
        batch = self.prepare()
        self.control.submit(batch)
        self.control.download(batch)
        path = self.path/batch/'output/results.jsonl'
        row = json.loads(path.read_text())
        row['input_hash']='newer-or-unrelated-version'
        path.write_text(json.dumps(row)+'\n')
        with self.assertRaises(ValueError):
            self.control.verify_output(batch)

    def test_controller_crash_after_reservation_does_not_resubmit(self):
        batch = self.prepare()
        self.control._set(batch,'submitting')
        recovered = Controller(self.path,'owner',self.client)
        recovered.submit(batch)
        self.remote='RUNNING'
        self.assertEqual('running',recovered.status(batch)['state'])
        self.assertFalse(any(c[1]=='push' for c in self.calls))

    def test_observation_timeout_keeps_running_job(self):
        batch = self.prepare()
        self.control.submit(batch)
        self.remote='RUNNING'
        self.assertEqual('running',self.control.wait(batch,timeout=0)['state'])

    def test_wait_never_polls_early_at_observation_deadline(self):
        batch=self.prepare()
        self.control.submit(batch)
        self.remote='RUNNING'
        clock=[0]
        def sleep(seconds):
            clock[0]+=seconds
        with patch('batch_control.time.monotonic',side_effect=lambda:clock[0]), \
             patch('batch_control.time.sleep',side_effect=sleep) as sleeping, \
             patch.object(self.control,'status',wraps=self.control.status) as status:
            self.control.wait(batch,timeout=700)
        self.assertEqual([660,40],[call.args[0] for call in sleeping.call_args_list])
        self.assertEqual(2,status.call_count)

    def test_code_change_gets_new_immutable_batch(self):
        self.assertNotEqual(self.prepare(),self.control.prepare(self.manifest,TEMPLATE+'# changed\n'))

    def test_changed_manifest_is_rejected_before_submission(self):
        batch=self.prepare()
        path=self.path/batch/'manifest.json'
        manifest=json.loads(path.read_text())
        manifest['items'][0]['messages'][0]['content']='changed'
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.control.submit(batch)
        self.assertFalse(self.calls)

    def test_changed_runner_is_rejected_before_submission(self):
        batch=self.prepare()
        (self.path/batch/'runner.py').write_text('wrong source')
        with self.assertRaises(ValueError):
            self.control.submit(batch)
        self.assertFalse(self.calls)

    def test_partial_failure_retry_contains_only_failed_and_missing_items(self):
        self.manifest['items'] += [
            {'id':'analysis-2','input_hash':'version-b','messages':[{'role':'user','content':'b'}]},
            {'id':'analysis-3','input_hash':'version-c','messages':[{'role':'user','content':'c'}]}]
        batch=self.prepare()
        self.control.submit(batch)
        self.control.download(batch)
        manifest=self.control.manifest(batch)
        failed={'batch_id':batch,'manifest_hash':manifest['manifest_hash'],'id':'analysis-2',
                'input_hash':'version-b','status':'error','error':'timeout'}
        with (self.path/batch/'output/results.jsonl').open('a') as file:
            file.write(json.dumps(failed)+'\n')
        retry=self.control.retry(batch,TEMPLATE)
        self.assertEqual(['analysis-2','analysis-3'],[item['id'] for item in self.control.manifest(retry)['items']])
        self.assertEqual(retry,self.control.retry(batch,TEMPLATE))
        self.assertEqual(1,len([call for call in self.calls if call[1]=='push']))

    def test_successful_batch_does_not_create_retry(self):
        batch=self.prepare()
        self.control.submit(batch)
        self.control.download(batch)
        self.assertIsNone(self.control.retry(batch,TEMPLATE))

    def test_early_remote_failure_without_results_retries_all_items(self):
        batch=self.prepare()
        self.control.submit(batch)
        self.remote='ERROR'
        self.omit_results=True
        self.assertEqual(['analysis-1'],self.control.download(batch)['missing_ids'])
        retry=self.control.retry(batch,TEMPLATE)
        self.assertEqual(1,len(self.control.manifest(retry)['items']))

    def test_complete_without_results_is_not_accepted(self):
        batch=self.prepare()
        self.control.submit(batch)
        self.omit_results=True
        with self.assertRaises(ValueError):
            self.control.download(batch)

    def test_killed_writer_preserves_prior_valid_results_and_reports_partial_tail(self):
        batch=self.prepare()
        self.control.submit(batch)
        self.remote='ERROR'
        self.control.download(batch)
        with (self.path/batch/'output/results.jsonl').open('ab') as file:
            file.write(b'{\"content\":\"partial UTF8 \xe4\xb8')
        evidence=self.control.verify_output(batch)
        self.assertEqual(1,len(evidence['results']))
        self.assertTrue(evidence['interrupted_partial_record'])
        normalized=json.loads((self.path/batch/'verified-results.json').read_text())
        self.assertEqual(1,len(normalized['results']))

    def test_corrupt_middle_record_is_not_hidden_as_interruption(self):
        batch=self.prepare()
        self.control.submit(batch)
        self.remote='ERROR'
        self.control.download(batch)
        with (self.path/batch/'output/results.jsonl').open('ab') as file:
            file.write(b'invalid-json\n')
        with self.assertRaises(ValueError):
            self.control.verify_output(batch)

if __name__=='__main__':
    unittest.main()
