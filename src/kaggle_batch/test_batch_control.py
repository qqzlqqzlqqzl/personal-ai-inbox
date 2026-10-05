"""Failure/recovery tests. Test artifacts are retained, never deleted."""
import json
from pathlib import Path
import subprocess
import sqlite3
from contextlib import contextmanager
import unittest
from unittest.mock import patch
import uuid
from batch_control import Controller, parse_submit_success
from recovery_policy import ProviderError
import batch_control

TEMPLATE = 'MANIFEST = None\n'

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        self.calls = []
        self.remote = 'COMPLETE'
        self.fail_push = False
        self.fail_download = False
        self.omit_results = False
        self.control = Controller(self.path,'owner',self.client, initialize=True)
        self.manifest = {'runtime_source':'owner/runtime','session_timeout':600,
            'items':[{'id':'analysis-1','input_hash':'version-a','source_refs':[{'entry_id':1}],
                      'messages':[{'role':'user','content':'public test fixture'}]}]}

    def client(self,args,timeout):
        self.assertGreater(timeout,0)
        self.calls.append(args)
        if args[0]=='quota':
            return '[{"resource":"GPU","remaining":"20h"}]'
        if args[1]=='push':
            if self.fail_push:
                raise subprocess.TimeoutExpired('simulated-client',90)
            ref = json.loads((Path(args[args.index('-p')+1])/'kernel-metadata.json').read_text())['id']
            return f'Kernel version 1 successfully pushed.  Please check progress at https://www.kaggle.com/code/{ref}'
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
                     'id':'analysis-1','input_hash':'version-a','source_refs':[{'entry_id':1}],'status':'ok','content':'{}'}
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
            {'id':'analysis-2','input_hash':'version-b','source_refs':[{'entry_id':2}],'messages':[{'role':'user','content':'b'}]},
            {'id':'analysis-3','input_hash':'version-c','source_refs':[{'entry_id':3}],'messages':[{'role':'user','content':'c'}]}]
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


class ReceiptAndQuarantineTests(unittest.TestCase):
    setUp = LifecycleTests.setUp
    client = LifecycleTests.client
    prepare = LifecycleTests.prepare

    def claims(self, batch):
        with self.control.db() as db:
            return [tuple(row) for row in db.execute('SELECT batch_id,entry_id FROM batch_claims WHERE batch_id=? ORDER BY entry_id', (batch,))]

    def unknown(self, claims=1):
        self.manifest['items'][0]['source_refs'] = [{'entry_id': i} for i in range(1, claims+1)]
        batch = self.prepare()
        self.control._set(batch, 'submit_unknown', error='network')
        return batch, self.control.row(batch)

    def quarantine(self, batch, expected):
        return self.control.quarantine_unknown(batch, expected, 'Reviewed old unknown attempt', 'synthetic-operator-review')

    def test_full_official_success_binds_ref_version_url(self):
        ref = 'owner/job'
        for url in ('https://www.kaggle.com/code/'+ref, 'https://www.kaggle.com/'+ref):
            with self.subTest(url=url):
                line = f'Kernel version 3 successfully pushed.  Please check progress at {url}'
                self.assertEqual({'ref': ref, 'version': 3, 'url': url}, parse_submit_success(line, ref))
        warning = "Warning: Looks like you're using an outdated `kaggle` version (installed: 1.0), please consider upgrading to the latest version (2.0)"
        self.assertEqual(3, parse_submit_success(warning+'\n'+line, ref)['version'])

    def test_short_multiple_conflicting_or_unbound_success_is_unknown(self):
        good = 'Kernel version 1 successfully pushed.  Please check progress at https://www.kaggle.com/code/owner/job'
        invalid = [good+'\n'+good, 'prefix '+good, good+'\nKernel push error: quota exceeded',
                   'Kernel version 1 successfully pushed.', good.replace('version 1', 'version 0'),
                   good.replace('owner/job', 'other/job'), good.replace('owner/job', 'owner/other'),
                   good.replace('https:', 'http:'), good.replace('www.kaggle.com', 'www.kaggle.com.evil.test'),
                   good+'?token=synthetic', good+'#fragment', '\x1b[32m'+good,
                   good.replace('successfully pushed.', 'successfully saved without running.'), 'x'*65537]
        for output in invalid:
            with self.subTest(output=output[:120]), self.assertRaises(ValueError):
                parse_submit_success(output, 'owner/job')

    def test_receipt_is_complete_before_submitted_cas(self):
        batch = self.prepare()
        original = batch_control._cas_row
        observed = []
        def cas(db, before, state, updated, error, remote=None):
            if state == 'submitted':
                receipt = json.loads((self.path/batch/'submit-receipt.json').read_text())
                observed.append(receipt)
                self.assertEqual(before['updated'], receipt['reserved_at'])
                self.assertEqual(before['manifest_hash'], receipt['manifest_hash'])
            return original(db, before, state, updated, error, remote)
        with patch('batch_control._cas_row', side_effect=cas):
            self.assertEqual('submitted', self.control.submit(batch)['state'])
        self.assertEqual(1, len(observed))
        self.assertEqual('owner/'+batch, observed[0]['ref'])
        self.assertEqual(1, observed[0]['version'])
        self.assertEqual('https://www.kaggle.com/code/owner/'+batch, observed[0]['url'])
        self.control.submit(batch)
        self.assertEqual(1, len([x for x in self.calls if x[:2] == ['kernels','push']]))

    def test_receipt_write_failure_keeps_unknown_and_never_repushes(self):
        batch = self.prepare()
        original = batch_control._publish_json_once
        def write(path, value):
            if path.name == 'submit-receipt.json':raise OSError('synthetic fsync failure')
            return original(path, value)
        with patch('batch_control._publish_json_once', side_effect=write), self.assertRaises(ProviderError) as error:
            self.control.submit(batch)
        self.assertEqual('unknown', error.exception.code)
        self.assertEqual('submit_unknown', self.control.row(batch)['state'])
        self.assertEqual('submit_receipt_write_failed', self.control.row(batch)['error'])
        restarted = Controller(self.path, 'owner', self.client)
        restarted.submit(batch)
        self.assertEqual(1, len([x for x in self.calls if x[:2] == ['kernels','push']]))
        self.assertEqual(1, len(self.claims(batch)))

    def test_receipt_cas_conflict_survives_restart_and_blocks_absence_retirement(self):
        batch = self.prepare()
        original = batch_control._cas_row
        def cas(db, before, state, updated, error, remote=None):
            return False if state == 'submitted' else original(db, before, state, updated, error, remote)
        with patch('batch_control._cas_row', side_effect=cas), self.assertRaises(ProviderError) as error:
            self.control.submit(batch)
        self.assertEqual('unknown', error.exception.code)
        self.assertTrue((self.path/batch/'submit-receipt.json').is_file())
        self.assertEqual('submit_unknown', self.control.row(batch)['state'])
        with self.control.db() as db:db.execute('UPDATE batches SET updated=0 WHERE id=?', (batch,))
        calls = []
        def inaccessible(args, timeout):
            calls.append(args)
            raise ProviderError('inaccessible')
        restarted = Controller(self.path, 'owner', inaccessible)
        self.assertEqual('submit_unknown', restarted.submit(batch)['state'])
        with self.assertRaises(ProviderError) as error:restarted.status(batch)
        self.assertEqual('unknown', error.exception.code)
        self.assertEqual([['kernels','status','owner/'+batch]], calls)
        self.assertEqual('submit_unknown', restarted.row(batch)['state'])
        self.assertEqual(1, len(self.claims(batch)))
        self.assertFalse((self.path/batch/'absence-observations.json').exists())

    def test_database_commit_failure_after_receipt_cannot_repush(self):
        batch = self.prepare()
        real_db = self.control.db
        fail = [True]
        @contextmanager
        def database():
            with real_db() as db:
                yield db
                state = db.execute('SELECT state FROM batches WHERE id=?', (batch,)).fetchone()[0]
                if fail[0] and state == 'submitted':
                    fail[0] = False
                    raise sqlite3.OperationalError('synthetic commit failure')
        with patch.object(self.control, 'db', database), self.assertRaises(ProviderError) as error:
            self.control.submit(batch)
        self.assertEqual('unknown', error.exception.code)
        self.assertTrue((self.path/batch/'submit-receipt.json').exists())
        self.assertEqual('submit_unknown', self.control.row(batch)['state'])
        self.control.submit(batch)
        self.assertEqual(1, len([x for x in self.calls if x[:2] == ['kernels','push']]))

    def test_conflicting_receipt_and_partial_file_close_all_recovery_paths(self):
        batch = self.prepare(); self.control.submit(batch)
        path = self.path/batch/'submit-receipt.json'
        original = path.read_bytes(); receipt = json.loads(original)
        broken = [b'{bad', json.dumps({**receipt, 'ref': 'other/job'}).encode(),
                  json.dumps({**receipt, 'manifest_hash': '0'*64}).encode(),
                  json.dumps({**receipt, 'version': True}).encode(),
                  json.dumps({**receipt, 'schema': True}).encode(),
                  json.dumps({**receipt, 'url': receipt['url']+'?token=synthetic'}).encode(),
                  original[:-1]+b',"version":2}']
        before = len(self.calls)
        for raw in broken:
            path.write_bytes(raw)
            for action in (self.control.submit, self.control.status, self.control.download,
                           self.control.verify_output, self.control.salvage_output):
                with self.subTest(action=action.__name__, raw=raw[:50]), self.assertRaises(ProviderError) as error:
                    action(batch)
                self.assertEqual('unknown', error.exception.code)
        path.write_bytes(original)
        partial = path.with_suffix('.json.pending'); partial.write_bytes(b'{partial')
        with self.assertRaises(ProviderError):self.control.status(batch)
        self.assertEqual(before, len(self.calls))
        self.assertEqual(1, len(self.claims(batch)))

    def test_quarantine_preserves_71_claims_manifest_error_and_never_calls_provider(self):
        batch, expected = self.unknown(71)
        folder = self.path/batch
        originals = {name: (folder/name).read_bytes() for name in ('manifest.json','prepared-code.json','runner.py','kernel-metadata.json')}
        claims = self.claims(batch)
        row = self.quarantine(batch, expected)
        self.assertEqual('quarantined', row['state']); self.assertEqual('network', row['error'])
        self.assertEqual(claims, self.claims(batch)); self.assertEqual(71, len(claims))
        self.assertEqual(originals, {name: (folder/name).read_bytes() for name in originals})
        self.assertIsNone(self.control.next_pending()); self.assertIsNone(self.control.next_retry()); self.assertIsNone(self.control.outstanding())
        for action in (self.control.submit, self.control.status, self.control.wait):
            with patch('batch_control.time.sleep', side_effect=AssertionError('quarantine must return immediately')):
                self.assertEqual('quarantined', action(batch)['state'])
        for action in (self.control.download, self.control.verify_output, self.control.salvage_output):
            with self.assertRaises(ProviderError):action(batch)
        with self.assertRaises(ValueError):self.control.retry(batch,TEMPLATE)
        with self.assertRaises(ValueError):self.control.retire(batch,'cannot release claims')
        for target in ('prepared','running','terminal','downloaded','imported','resolved','retired'):
            with self.subTest(target=target), self.assertRaises(ProviderError):
                self.control._set(batch,target,'COMPLETE')
        self.assertEqual(row, self.control.row(batch)); self.assertEqual(claims, self.claims(batch))
        self.assertEqual([], self.calls)

    def test_quarantine_requires_explicit_snapshot_and_rejects_stale_cas(self):
        batch, expected = self.unknown()
        for field, value in [('updated', 0), ('error','different'), ('manifest_hash','0'*64)]:
            with self.subTest(field=field), self.assertRaises(ProviderError):
                self.quarantine(batch, {**expected, field:value})
            self.assertEqual(expected, self.control.row(batch))
        with self.assertRaises(ValueError):self.quarantine(batch, {**expected,'state':'running'})
        with self.assertRaises(ValueError):self.quarantine(batch, {**expected,'remote_status':'COMPLETE'})
        with self.assertRaises(ValueError):self.control.quarantine_unknown(batch,expected,'','review')
        with self.assertRaises(ValueError):self.control._set(batch,'quarantined')
        self.assertFalse((self.path/batch/'quarantine-reviewed.json').exists())

    def test_quarantine_receipt_or_cas_failure_never_changes_unknown(self):
        batch, expected = self.unknown()
        with (patch('batch_control._publish_json_once', side_effect=OSError('synthetic audit write failure')),
              self.assertRaises(ProviderError)):
            self.quarantine(batch, expected)
        self.assertEqual(expected, self.control.row(batch))
        original = batch_control._cas_row
        def cas(db, before, state, updated, error, remote=None):
            return False if state == 'quarantined' else original(db,before,state,updated,error,remote)
        with patch('batch_control._cas_row', side_effect=cas), self.assertRaises(ProviderError):
            self.quarantine(batch, expected)
        self.assertEqual(expected, self.control.row(batch))
        path = self.path/batch/'quarantine-reviewed.json'; audit = path.read_bytes()
        self.assertEqual('quarantined', self.quarantine(batch,expected)['state'])
        self.assertEqual(audit, path.read_bytes())  # Retry of the same reviewed CAS retains its evidence.
        self.assertEqual('quarantined', self.quarantine(batch,expected)['state'])
        self.assertEqual([], self.calls)

    def test_inflight_status_cannot_undo_quarantine_winner(self):
        batch, expected = self.unknown()
        def status(args, timeout):
            self.assertEqual(['kernels','status','owner/'+batch], args)
            self.quarantine(batch,expected)
            return 'has status "KernelWorkerStatus.COMPLETE"'
        self.control.client = status
        row = self.control.status(batch)
        self.assertEqual('quarantined', row['state']); self.assertEqual('network', row['error'])
        with self.assertRaises(ProviderError):self.control._set(batch,'resolved','COMPLETE')
        self.assertEqual(1, len(self.claims(batch)))



    def test_receipt_appearing_during_push_is_preserved_not_replaced(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                self.setUp()
                batch = self.prepare(); folder = self.path/batch
                target = folder/('submit-receipt.json.pending' if pending else 'submit-receipt.json')
                original = b'pre-existing pending evidence' if pending else b'{"version":99,"evidence":"another receipt"}'
                def provider(args, timeout):
                    result = self.client(args, timeout)
                    if args[:2] == ['kernels','push']:target.write_bytes(original)
                    return result
                self.control.client = provider
                with self.assertRaises(ProviderError) as error:self.control.submit(batch)
                self.assertEqual('unknown', error.exception.code)
                self.assertEqual(original, target.read_bytes())
                self.assertEqual('submit_unknown', self.control.row(batch)['state'])
                before = len(self.calls)
                restarted = Controller(self.path, 'owner', self.client)
                with self.assertRaises(ProviderError):restarted.submit(batch)
                self.assertEqual(before, len(self.calls)); self.assertEqual(1, len(self.claims(batch)))

    def test_final_created_at_atomic_link_boundary_is_never_clobbered(self):
        batch = self.prepare(); final = self.path/batch/'submit-receipt.json'
        original = b'{"version":99,"evidence":"concurrent final"}'
        link = batch_control.os.link
        def concurrent(source, target, **kwargs):
            Path(target).write_bytes(original)
            return link(source, target, **kwargs)
        with patch('batch_control.os.link', side_effect=concurrent), self.assertRaises(ProviderError) as error:
            self.control.submit(batch)
        self.assertEqual('unknown', error.exception.code)
        self.assertEqual(original, final.read_bytes())
        self.assertTrue(final.with_suffix('.json.pending').is_file())
        self.assertEqual('submit_unknown', self.control.row(batch)['state'])
        self.assertEqual(1, len([x for x in self.calls if x[:2] == ['kernels','push']]))

    def test_quarantine_replay_with_new_pending_is_unknown_and_preserves_71_claims(self):
        batch, expected = self.unknown(71)
        row = self.quarantine(batch, expected)
        final = self.path/batch/'quarantine-reviewed.json'; before = final.read_bytes()
        pending = final.with_suffix('.json.pending'); raw = b'conflicting quarantine intent'
        pending.write_bytes(raw)
        with self.assertRaises(ProviderError) as error:self.quarantine(batch, expected)
        self.assertEqual('unknown', error.exception.code)
        self.assertEqual(row, self.control.row(batch)); self.assertEqual(71, len(self.claims(batch)))
        self.assertEqual(before, final.read_bytes()); self.assertEqual(raw, pending.read_bytes())
        self.assertEqual([], self.calls)


    def test_receipt_arriving_during_absence_observation_keeps_71_claims(self):
        for name in ('submit-receipt.json', 'submit-receipt.json.pending'):
            with self.subTest(name=name):
                self.setUp(); batch, _ = self.unknown(71)
                with self.control.db() as db:db.execute('UPDATE batches SET updated=0 WHERE id=?', (batch,))
                before = self.control.row(batch); claims = self.claims(batch)
                clock = [5000.0]; observations = []
                target = self.path/batch/name; original = b'positive or unresolved submit evidence'
                def provider(args, timeout):
                    if args[:2] == ['kernels','status']:raise ProviderError('inaccessible')
                    if args[:2] == ['kernels','list']:
                        return json.dumps([{'ref':'owner/existing-other'}]) if args[args.index('--page')+1] == '1' else 'Not found\n'
                    if args[0] == 'quota':
                        observations.append(clock[0])
                        if len(observations) == 2:target.write_bytes(original)
                        return '[{"resource":"GPU","remaining":"20h"}]'
                    raise AssertionError('No submit is allowed')
                self.control.client = provider
                with patch('batch_control.time.time', side_effect=lambda: clock[0]):
                    with self.assertRaises(ProviderError):self.control.status(batch)
                    clock[0] = 5700.0
                    with self.assertRaises(ProviderError) as error:self.control.status(batch)
                self.assertEqual('unknown', error.exception.code)
                self.assertEqual([5000.0, 5700.0], observations)
                self.assertEqual(before, self.control.row(batch)); self.assertEqual(claims, self.claims(batch))
                self.assertEqual(original, target.read_bytes())


    def test_submit_and_quarantine_share_lock_and_recheck_snapshot(self):
        import fcntl
        import threading
        batch, expected = self.unknown(71)
        claims = self.claims(batch); manifest = (self.path/batch/'manifest.json').read_bytes()
        submit_entered = threading.Event(); finish_submit = threading.Event()
        quarantine_lock_attempted = threading.Event(); quarantine_finished = threading.Event()
        results = {}; flock = fcntl.flock; original_manifest = self.control.manifest
        def checked_manifest(batch_id):
            if threading.current_thread().name == 'synthetic-submit':
                submit_entered.set()
                if not finish_submit.wait(5):raise AssertionError('Synthetic submit was not released')
            return original_manifest(batch_id)
        def tracked_flock(file, operation):
            if threading.current_thread().name == 'synthetic-quarantine':quarantine_lock_attempted.set()
            return flock(file, operation)
        def submit():
            try:results['submit'] = self.control.submit(batch)
            except Exception as exc:results['submit'] = exc
        def quarantine():
            try:results['quarantine'] = self.quarantine(batch, expected)
            except Exception as exc:results['quarantine'] = exc
            finally:quarantine_finished.set()
        with patch.object(self.control, 'manifest', side_effect=checked_manifest), patch('fcntl.flock', side_effect=tracked_flock):
            producer = threading.Thread(target=submit, name='synthetic-submit')
            operator = threading.Thread(target=quarantine, name='synthetic-quarantine')
            producer.start()
            try:
                self.assertTrue(submit_entered.wait(5))
                operator.start()
                self.assertTrue(quarantine_lock_attempted.wait(5))
                self.assertFalse(quarantine_finished.is_set())
                self.assertEqual(expected, self.control.row(batch))
                self.assertFalse((self.path/batch/'quarantine-reviewed.json').exists())
                self.control._set(batch, 'submit_unknown', error='late observation')
                newer = self.control.row(batch)
            finally:
                finish_submit.set()
                producer.join(5)
                if operator.ident is not None:operator.join(5)
        self.assertFalse(producer.is_alive()); self.assertFalse(operator.is_alive())
        self.assertEqual(newer, results['submit'])
        self.assertIsInstance(results['quarantine'], ProviderError)
        self.assertEqual('unknown', results['quarantine'].code)
        self.assertEqual(newer, self.control.row(batch)); self.assertEqual(claims, self.claims(batch))
        self.assertEqual(manifest, (self.path/batch/'manifest.json').read_bytes())
        self.assertFalse((self.path/batch/'quarantine-reviewed.json').exists())
        self.assertFalse((self.path/batch/'submit-receipt.json').exists())
        self.assertFalse(self.calls)


    def test_inflight_submit_and_absence_retirement_share_the_batch_lock(self):
        import fcntl
        import threading
        self.manifest['items'][0]['source_refs'] = [{'entry_id': i} for i in range(1, 72)]
        batch = self.prepare(); clock = [1000.0]
        push_entered = threading.Event(); finish_push = threading.Event()
        absence_lock_attempted = threading.Event(); absence_finished = threading.Event()
        results = {}; calls = []
        flock = fcntl.flock
        def tracked_flock(file, operation):
            if threading.current_thread().name == 'synthetic-absence':absence_lock_attempted.set()
            return flock(file, operation)
        def provider(args, timeout):
            calls.append(args)
            if args[0] == 'quota':return '[{"resource":"GPU","remaining":"20h"}]'
            if args[:2] == ['kernels','push']:
                push_entered.set()
                if not finish_push.wait(5):raise AssertionError('Synthetic push was not released')
                return f'Kernel version 1 successfully pushed.  Please check progress at https://www.kaggle.com/code/owner/{batch}'
            if args[:2] == ['kernels','status']:raise ProviderError('inaccessible')
            raise AssertionError('Absence listing must not run after the successful receipt')
        self.control.client = provider
        def submit():
            try:results['submit'] = self.control.submit(batch)
            except Exception as exc:results['submit'] = exc
        def absence():
            try:results['absence'] = self.control.status(batch)
            except Exception as exc:results['absence'] = exc
            finally:absence_finished.set()
        with patch('batch_control.time.time', side_effect=lambda: clock[0]), patch('fcntl.flock', side_effect=tracked_flock):
            producer = threading.Thread(target=submit, name='synthetic-submit')
            observer = threading.Thread(target=absence, name='synthetic-absence')
            producer.start()
            try:
                self.assertTrue(push_entered.wait(5))
                clock[0] = 5000.0
                observer.start()
                self.assertTrue(absence_lock_attempted.wait(5))
                self.assertFalse(absence_finished.is_set())
            finally:
                finish_push.set()
                producer.join(5)
                if observer.ident is not None:observer.join(5)
        self.assertFalse(producer.is_alive()); self.assertFalse(observer.is_alive())
        self.assertEqual('submitted', results['submit']['state'])
        self.assertIsInstance(results['absence'], ProviderError)
        self.assertEqual('unknown', results['absence'].code)
        self.assertEqual('submitted', self.control.row(batch)['state'])
        self.assertEqual(71, len(self.claims(batch)))
        self.assertTrue((self.path/batch/'submit-receipt.json').is_file())
        self.assertFalse((self.path/batch/'absence-observations.json').exists())
        self.assertEqual(1, len([args for args in calls if args[:2] == ['kernels','push']]))


if __name__=='__main__':
    unittest.main()
