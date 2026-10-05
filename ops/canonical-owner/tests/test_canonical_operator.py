"""Offline tests only. Every generated file is retained; no provider access."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'canonical_operator.py'
spec=importlib.util.spec_from_file_location('operator_under_test',SOURCE)
op=importlib.util.module_from_spec(spec);spec.loader.exec_module(op)
RUN_ROOT=Path(os.environ.get('OPERATOR_TEST_RETAIN_ROOT',str(SOURCE.parent.parent/'retained-synthetic-tests')))
RUN_ROOT.mkdir(parents=True,exist_ok=True)

class OperatorControls(unittest.TestCase):
    def setUp(self):
        self.directory=Path(tempfile.mkdtemp(prefix='case-',dir=RUN_ROOT))
        self.env=patch.dict(os.environ,{'AI_NEWS_OUTBOUND_PROXY':'http://synthetic-proxy.invalid:8080'},clear=True);self.env.start()
        self.addCleanup(self.env.stop)
        self.evidence=self.directory/'evidence';self.evidence.mkdir(mode=0o700)
        self.root=self.directory/'state';self.root.mkdir()
        self.token=self.directory/'synthetic-token';self.token.write_text('SYNTHETIC_TOKEN_ONLY');self.token.chmod(0o600)
        self.alias=self.directory/'alias';self.alias.symlink_to(self.directory,target_is_directory=True)
        self.config=self.directory/'config.json'
        self.cfg={'owner':'synthetic-owner','kaggle_python':sys.executable,'state_root':str(self.alias/'state'),
                  'token_file':str(self.alias/'synthetic-token'),'peer_state_roots':[]}
        self.config.write_text(json.dumps(self.cfg))
        observed=time.time()-60
        self.row={'id':'synthetic-target','state':'submit_unknown','remote_status':None,'updated':observed-10,'error':'synthetic-error','manifest_hash':'a'*64}
        with sqlite3.connect(self.root/'batches.sqlite3') as db:
            db.execute('CREATE TABLE batches(id TEXT PRIMARY KEY,manifest_hash TEXT NOT NULL,state TEXT NOT NULL,remote_status TEXT,error TEXT,updated REAL NOT NULL)')
            db.execute('CREATE TABLE batch_claims(batch_id TEXT NOT NULL,entry_id INTEGER NOT NULL,PRIMARY KEY(batch_id,entry_id))')
            db.execute('INSERT INTO batches VALUES(:id,:manifest_hash,:state,:remote_status,:error,:updated)',self.row)
            db.execute('INSERT INTO batch_claims VALUES(?,?)',(self.row['id'],1))
        self.context={'lane':'primary','expected_owner':'synthetic-owner','target':{'binding_id':str(uuid.uuid4()),
            'claimed_ref':'synthetic-owner/synthetic-target','ledger_observed_at':observed}}
        self.input={'attempt_id':str(uuid.uuid4()),'evidence_root':str(self.evidence),'expected_root_count':1,
            'sole_executor_no_alias_changes':True,'lanes':[{'config_path':str(self.config),'context':self.context,'ledger_before':self.row}]}
        self.input=op.parse_spec(op.encoded(self.input))

    def snapshot(self):return op.gather_snapshot(self.input)
    def preflight(self):
        with patch.object(op,'sdk_preflight') as mocked:
            result=op.preflight(self.input)
        self.assertEqual(mocked.call_count,1)
        return result
    def receipt(self,context=None,**updates):
        context=context or self.context;r=op.c.base_result(context)
        stamp=op.now()
        r.update(status='OBSERVED',owner_verified_at_observation=True,status_observed=True,exact_request_bound=True,
                 binding_verified_at_finish=True,state='RUNNING',collection_started_at=stamp,collection_finished_at=stamp,
                 request_counts={'identity':1,'status':1},reason='raw_status_observed',
                 observations={k:{'request_started_at':stamp,'response_observed_at':stamp,'http_status':200,'reason':'raw_response_captured'} for k in ('identity','status')})
        r.update(updates);return r
    def fake_child(self,*args,**kwargs):
        context=json.loads(kwargs['input']);return subprocess.CompletedProcess(args,0,op.encoded(self.receipt(context)),b'')
    def execute(self,callback=None):
        with patch.object(op,'sdk_preflight'),patch.object(op.subprocess,'run',side_effect=callback or self.fake_child) as mocked:
            result=op.execute(self.input)
        return result,mocked
    def sql(self,sql,args=()):
        with sqlite3.connect(self.root/'batches.sqlite3') as db:db.execute(sql,args)

    def test_snapshot_stable_complete_and_token_unread(self):
        original=op.c.token_snapshot
        with patch.object(op.c,'token_snapshot',wraps=original) as mocked:
            first=self.snapshot();self.assertEqual(first,self.snapshot())
        self.assertTrue(all(call.kwargs=={'read':False} for call in mocked.call_args_list))
        ledger=next(iter(first['ledgers'].values()));self.assertEqual(ledger['claims_count'],1)
        serialized=op.encoded(first)
        self.assertNotIn(b'SYNTHETIC_TOKEN_ONLY',serialized)
        self.assertNotIn(b'synthetic-proxy.invalid',serialized)

    def test_nested_absolute_and_relative_target_aliases_are_recorded(self):
        nested=self.directory/'nested';nested.symlink_to('alias',target_is_directory=True)
        outer=self.directory/'outer';outer.symlink_to(str(nested),target_is_directory=True)
        trace=op.trace_path(str(outer/'synthetic-token'),'file')
        self.assertEqual(trace['canonical'],str(self.token))
        self.assertEqual(len(trace['aliases']),3)
        self.assertTrue(all({'mtime_ns','ctime_ns','mode','ino','uid','gid','dev'}<=set(x['identity']) for x in trace['aliases']))

    def test_trailing_slash_directory_resolves(self):
        self.assertEqual(op.trace_path(str(self.root)+'/','directory')['canonical'],str(self.root))
        parent=self.directory/'parent';parent.symlink_to('state/..',target_is_directory=True)
        self.assertEqual(op.trace_path(str(parent),'directory')['canonical'],str(self.directory))

    def test_loop_refused(self):
        loop=self.directory/'loop';loop.symlink_to('loop')
        with self.assertRaises(op.Refused):op.trace_path(str(loop),'file')

    def test_logical_dotdot_refused(self):
        with self.assertRaises(op.Refused):op.trace_path(str(self.root)+'/../synthetic-token')

    def test_alias_aba_restore_detected_without_deleting(self):
        before=op.trace_path(str(self.alias/'synthetic-token'),'file')
        retained=self.directory/'original-alias-retained';self.alias.rename(retained)
        self.alias.symlink_to(self.root,target_is_directory=True)
        self.alias.rename(self.directory/'temporary-alias-retained');retained.rename(self.alias)
        after=op.trace_path(str(self.alias/'synthetic-token'),'file')
        self.assertEqual(before['canonical'],after['canonical'])
        self.assertEqual(before['aliases'][0]['identity']['ino'],after['aliases'][0]['identity']['ino'])
        self.assertNotEqual(before,after)

    def test_config_content_drift(self):
        before=self.snapshot();self.cfg['extra']='changed';self.config.write_text(json.dumps(self.cfg))
        self.assertNotEqual(before,self.snapshot())

    def test_target_all_six_fields_and_deleted_row_refused(self):
        changes={'id':'another-id','state':'imported','remote_status':'COMPLETE','updated':self.row['updated']+1,'error':'changed','manifest_hash':'b'*64}
        for field,value in changes.items():
            with self.subTest(field=field):
                self.sql('UPDATE batches SET '+field+'=?',(value,))
                with self.assertRaises(op.Refused):self.snapshot()
                self.sql('UPDATE batches SET '+field+'=?',(self.row[field],))
        self.sql('DELETE FROM batches')
        with self.assertRaises(op.Refused):self.snapshot()

    def test_unrelated_claim_change_bound(self):
        before=self.snapshot();self.sql('INSERT INTO batch_claims VALUES(?,?)',(self.row['id'],999))
        after=self.snapshot();self.assertNotEqual(before,after)
        self.assertEqual(next(iter(after['ledgers'].values()))['claims_count'],2)

    def test_unrelated_batch_row_bound(self):
        before=self.snapshot();self.sql('INSERT INTO batches VALUES(?,?,?,?,?,?)',('other','b'*64,'imported','COMPLETE',None,time.time()-1))
        self.assertNotEqual(before,self.snapshot())

    def test_missing_claims_refused(self):
        self.sql('DELETE FROM batch_claims')
        with self.assertRaisesRegex(op.Refused,'target_claims_missing'):self.snapshot()

    def test_changed_schema_refused(self):
        self.sql('ALTER TABLE batch_claims ADD COLUMN extra TEXT')
        with self.assertRaisesRegex(op.Refused,'claims_schema'):self.snapshot()

    def test_budget_overflow_refused_not_truncated(self):
        self.sql('INSERT INTO batch_claims VALUES(?,?)',(self.row['id'],2))
        with patch.object(op,'MAX_ROWS',1),self.assertRaisesRegex(op.Refused,'ledger_snapshot_budget'):self.snapshot()

    def test_root_count_mismatch(self):
        self.input['expected_root_count']=2
        with self.assertRaisesRegex(op.Refused,'complete_root_count'):self.snapshot()

    def test_token_permissions_and_hardlink_refused(self):
        self.token.chmod(0o644)
        with self.assertRaises(op.Refused):self.snapshot()
        self.token.chmod(0o600);os.link(self.token,self.directory/'retained-token-hardlink')
        with self.assertRaises(op.Refused):self.snapshot()

    def test_wrong_owner_and_interpreter_refused(self):
        for key,value in [('owner','other'),('kaggle_python','/usr/bin/nonexistent')]:
            cfg=dict(self.cfg);cfg[key]=value;self.config.write_text(json.dumps(cfg))
            with self.subTest(key=key),self.assertRaises(op.Refused):self.snapshot()

    def test_existing_proxy_conflict_tls_and_no_proxy_refused(self):
        for env in [{'HTTPS_PROXY':'http://other.invalid'},{'NO_PROXY':'api.kaggle.com'},{'REQUESTS_CA_BUNDLE':'/synthetic/path'}]:
            with self.subTest(env=next(iter(env))),patch.dict(os.environ,env),self.assertRaises(op.Refused):self.snapshot()

    def test_preflight_zero_network_and_does_not_read_token(self):
        with patch.object(op.subprocess,'run',side_effect=AssertionError('no child before execution')):
            result=self.preflight()
        self.assertEqual(result['status'],'PREFLIGHT_OK');self.assertEqual(result['provider_requests'],0)
        plan=json.loads((self.evidence/self.input['attempt_id']/'preflight.json').read_text())
        self.assertFalse(plan['real_token_contents_read']);self.assertFalse(result['cas_admission'])

    def test_preflight_named_directory_cannot_repeat(self):
        self.preflight()
        with self.assertRaises(FileExistsError):self.preflight()

    def test_preflight_evidence_attempt_swap_refused_without_redirected_write(self):
        outside=self.directory/'outside';outside.mkdir(mode=0o700)
        def swap():
            folder=self.evidence/self.input['attempt_id'];folder.rename(self.evidence/'original-attempt-retained')
            folder.symlink_to(outside,target_is_directory=True)
        with patch.object(op,'sdk_preflight',side_effect=swap),self.assertRaises(op.Refused):op.preflight(self.input)
        self.assertEqual(list(outside.iterdir()),[])

    def test_held_dirfd_survives_swap_between_validation_and_open(self):
        outside=self.directory/'outside';outside.mkdir(mode=0o700)
        with op.EvidenceDirectory(self.input,create=True) as evidence:
            original=op.os.open;swapped=False
            def opened(name,flags,*args,**kwargs):
                nonlocal swapped
                if name=='synthetic.json' and not swapped:
                    swapped=True;folder=self.evidence/self.input['attempt_id']
                    folder.rename(self.evidence/'original-attempt-retained');folder.symlink_to(outside,target_is_directory=True)
                return original(name,flags,*args,**kwargs)
            with patch.object(op.os,'open',side_effect=opened),self.assertRaises(op.Refused):evidence.write('synthetic.json',{'safe':True})
        self.assertEqual(list(outside.iterdir()),[])
        self.assertTrue((self.evidence/'original-attempt-retained/synthetic.json').is_file())

    def test_preflight_evidence_root_swap_refused(self):
        outside=self.directory/'outside';outside.mkdir(mode=0o700)
        def swap():
            self.evidence.rename(self.directory/'original-evidence-retained');self.evidence.symlink_to(outside,target_is_directory=True)
        with patch.object(op,'sdk_preflight',side_effect=swap),self.assertRaises(op.Refused):op.preflight(self.input)
        self.assertEqual(list(outside.iterdir()),[])

    def test_copied_preflight_in_replacement_directory_is_refused(self):
        self.preflight();folder=self.evidence/self.input['attempt_id'];old=self.evidence/'original-attempt-retained';folder.rename(old);folder.mkdir(mode=0o700)
        for source in old.iterdir():
            target=folder/source.name;target.write_bytes(source.read_bytes());target.chmod(0o600)
        with self.assertRaisesRegex(op.Refused,'preflight_evidence_directory_changed'):self.execute()
        self.assertFalse((folder/'execution.marker').exists())

    def test_evidence_leaf_symlink_existing_file_not_overwritten(self):
        with op.EvidenceDirectory(self.input,create=True) as evidence:
            target=self.directory/'outside-file';target.write_text('retained')
            (self.evidence/self.input['attempt_id']/'synthetic.json').symlink_to(target)
            with self.assertRaises(FileExistsError):evidence.write('synthetic.json',{'safe':True})
        self.assertEqual(target.read_text(),'retained')

    def test_execution_requires_preflight(self):
        with self.assertRaises(FileNotFoundError):self.execute()

    def test_exact_two_budget_canonical_env_and_no_global_environment_change(self):
        self.preflight();before=dict(os.environ);result,mocked=self.execute()
        self.assertEqual(mocked.call_count,1);self.assertEqual(result['business_attempt_upper_bound'],2)
        self.assertEqual(mocked.call_args.kwargs['env']['KAGGLE_API_TOKEN'],str(self.token))
        self.assertEqual(mocked.call_args.kwargs['env']['AI_NEWS_OUTBOUND_PROXY'],before['AI_NEWS_OUTBOUND_PROXY'])
        self.assertEqual(mocked.call_args.kwargs['timeout'],75);self.assertEqual(dict(os.environ),before)
        self.assertTrue(result['final_outer_binding_verified']);self.assertEqual(result['status'],'OBSERVATIONS_COMPLETE')
        for key in ('cas_admission','absence_proof','logical_lane_continuity_verified','aba_excluded','claim_transition_allowed','submission_allowed'):
            self.assertIs(result[key],False)

    def test_execution_marker_refuses_second_attempt(self):
        self.preflight();self.execute()
        with self.assertRaises(FileExistsError):self.execute()

    def test_final_drift_invalidates_already_observed_lane(self):
        self.preflight();original=op.gather_snapshot;calls=0
        def snapshot(spec):
            nonlocal calls
            calls+=1
            if calls==5:self.sql('INSERT INTO batch_claims VALUES(?,?)',(self.row['id'],909))
            return original(spec)
        with patch.object(op,'gather_snapshot',side_effect=snapshot):result,_=self.execute()
        self.assertEqual(calls,5);self.assertEqual(result['status'],'STOPPED_UNRESOLVED')
        self.assertEqual(result['lanes'][0]['state'],'UNRESOLVED');self.assertFalse(result['final_outer_binding_verified'])
        provisional=json.loads((self.evidence/self.input['attempt_id']/'lane-primary.receipt.json').read_text())
        self.assertTrue(provisional['provisional_canonical_observation']);self.assertTrue(provisional['requires_final_attempt_record'])

    def test_late_outer_exception_retains_consumed_budget_and_invalidates_summary(self):
        self.preflight();original=op.execute
        def broken(spec,result):
            result.update(provider_requests=None,business_attempt_upper_bound=2,lanes=[{'lane':'primary','state':'RUNNING','status':'OBSERVED_CANONICAL_ONLY'}])
            raise op.Refused('pre_execute_binding_changed')
        import io
        class Input:
            buffer=io.BytesIO(op.encoded(self.input))
        output=io.StringIO()
        with patch.object(op,'execute',side_effect=broken),patch.object(sys,'argv',['operator','--execute-reviewed']),patch.object(sys,'stdin',Input()),patch.object(sys,'stdout',output):
            self.assertEqual(op.main(),1)
        result=json.loads(output.getvalue());self.assertEqual(result['business_attempt_upper_bound'],2)
        self.assertIsNone(result['provider_requests']);self.assertFalse(result['final_outer_binding_verified'])
        self.assertEqual(result['lanes'][0]['state'],'UNRESOLVED')

    def test_root_replacement_identical_rows_is_not_same_binding(self):
        self.preflight();self.root.rename(self.directory/'original-state-retained');self.root.mkdir()
        (self.root/'batches.sqlite3').write_bytes((self.directory/'original-state-retained/batches.sqlite3').read_bytes())
        with self.assertRaises(op.Refused):self.execute()

    def test_pre_execute_changed_cfg_consumes_marker_without_child(self):
        self.preflight();self.cfg['extra']=True;self.config.write_text(json.dumps(self.cfg))
        with patch.object(op.subprocess,'run') as child,self.assertRaises(op.Refused):op.execute(self.input)
        child.assert_not_called();self.assertTrue((self.evidence/self.input['attempt_id']/'execution.marker').exists())

    def test_post_child_alias_config_claim_token_and_proxy_drift_refuse(self):
        changes=[lambda:self.token.write_text('SYNTHETIC_CHANGED'),lambda:self.sql('INSERT INTO batch_claims VALUES(?,?)',(self.row['id'],777)),
                 lambda:self.config.write_text(json.dumps(dict(self.cfg,extra='new'))),lambda:os.environ.update(AI_NEWS_OUTBOUND_PROXY='http://changed.invalid')]
        # Each complete attempt is unique and retained, with a newly snapshotted baseline.
        for change in changes:
            with self.subTest(change=change.__code__.co_firstlineno):
                self.input['attempt_id']=str(uuid.uuid4());self.preflight()
                def child(*a,**kw):response=self.fake_child(*a,**kw);change();return response
                result,mocked=self.execute(child)
                self.assertEqual(result['status'],'STOPPED_UNRESOLVED');self.assertFalse(result['final_outer_binding_verified'])
                self.assertEqual(result['lanes'][0]['state'],'UNRESOLVED');self.assertEqual(mocked.call_count,1)

    def test_raw_child_stdout_and_stderr_never_republished(self):
        self.preflight()
        def bad(*a,**kw):return subprocess.CompletedProcess(a,1,b'SYNTHETIC_PRIVATE_RAW',b'SYNTHETIC_PRIVATE_STDERR')
        result,_=self.execute(bad)
        self.assertEqual(result['status'],'STOPPED_UNRESOLVED')
        for path in (self.evidence/self.input['attempt_id']).iterdir():
            if path.is_file():
                self.assertNotIn(b'SYNTHETIC_PRIVATE',path.read_bytes())

    def test_timeout_never_retries_keeps_upper_bound(self):
        self.preflight()
        def timeout(*a,**kw):raise subprocess.TimeoutExpired('synthetic',75)
        result,mocked=self.execute(timeout)
        self.assertEqual(mocked.call_count,1);self.assertEqual(result['business_attempt_upper_bound'],2)
        self.assertIsNone(result['lanes'][0]['request_counts'])

    def test_receipt_rejects_raw_field_unknown_reason_false_binding_and_http_errors(self):
        variants=[{'private_ref':'secret'},{'reason':'NOT_ALLOWED'},{'request_counts':{'identity':True,'status':1}},
                  {'absence_proof':True},{'target_binding_id':str(uuid.uuid4())}]
        for variant in variants:
            with self.subTest(variant=variant),self.assertRaises(op.Refused):op.validated_child(op.encoded(self.receipt(**variant)),self.context)
        receipt=self.receipt();receipt['observations']['identity']['http_status']=403
        with self.assertRaises(op.Refused):op.validated_child(op.encoded(receipt),self.context)
        self.preflight()
        def child(*a,**kw):return subprocess.CompletedProcess(a,0,op.encoded(self.receipt(binding_verified_at_finish=False)),b'')
        result,_=self.execute(child);self.assertEqual(result['status'],'STOPPED_UNRESOLVED')

    def test_receipt_budget_and_reversed_time_rejected(self):
        for value in ('PRIVATE_VALUE',True,3):
            with self.assertRaises(op.Refused):op.validated_child(op.encoded(self.receipt(maximum_business_requests=value)),self.context)
        receipt=self.receipt();receipt['observations']['status']['request_started_at']='2000-01-01T00:00:00+00:00'
        with self.assertRaises(op.Refused):op.validated_child(op.encoded(receipt),self.context)

    def test_five_lane_budget_and_stop_after_first_unresolved(self):
        for lane in ('secondary','third','fourth','fifth'):
            item=copy.deepcopy(self.input['lanes'][0]);item['context']['lane']=lane;item['context']['target']['binding_id']=str(uuid.uuid4())
            self.input['lanes'].append(item)
        self.input=op.parse_spec(op.encoded(self.input));self.preflight();result,mocked=self.execute()
        self.assertEqual(mocked.call_count,5);self.assertEqual(result['business_attempt_upper_bound'],10)
        self.input['attempt_id']=str(uuid.uuid4());self.preflight()
        def unresolved(*a,**kw):
            context=json.loads(kw['input']);return subprocess.CompletedProcess(a,1,op.encoded(op.c.base_result(context)),b'')
        result,mocked=self.execute(unresolved)
        self.assertEqual(mocked.call_count,1);self.assertEqual(result['status'],'STOPPED_UNRESOLVED')
        self.assertEqual(result['reserved_business_attempts'],10);self.assertEqual(result['business_attempt_upper_bound'],2)

    def test_duplicate_lane_and_over_five_refused(self):
        self.input['lanes'].append(copy.deepcopy(self.input['lanes'][0]))
        with self.assertRaises(op.Refused):op.parse_spec(op.encoded(self.input))
        self.input['lanes']*=3
        with self.assertRaises(op.Refused):op.parse_spec(op.encoded(self.input))

    def test_default_cli_is_dryrun_without_input(self):
        result=subprocess.run([sys.executable,'-I','-B',str(SOURCE)],input=b'UNREAD_SYNTHETIC',capture_output=True,timeout=10)
        self.assertEqual(result.returncode,2);receipt=json.loads(result.stdout)
        self.assertEqual(receipt['status'],'DRY_RUN');self.assertEqual(receipt['provider_requests'],0)
        self.assertEqual(result.stderr,b'')

    def test_all_collector_bytes_unchanged(self):
        for filename,expected in op.PINS.items():
            self.assertEqual(hashlib.sha256((op.COLLECTOR.parent/filename).read_bytes()).hexdigest(),expected)

if __name__=='__main__':unittest.main(verbosity=2)
