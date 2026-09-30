import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from absence_proof import prove_absent
from batch_control import Controller, RetiredManifest
from cloud_cycle import drain
from exception_audit import Audit
from recovery_policy import ProviderError, classify_failure, effective_retry_at


@pytest.fixture
def uncertain(tmp_path):
    calls=[]
    def client(args,timeout):
        calls.append(args)
        if args[:2]==['kernels','status']:
            raise ProviderError('inaccessible')
        if args[:2]==['kernels','list']:
            return json.dumps([{'ref':'owner/existing'}]) if args[args.index('--page')+1]=='1' else 'Not found\n'
        if args[0]=='quota':
            return '[{"resource":"GPU","remaining":"0h"}]'
        raise AssertionError('No GPU writes expected')
    c=Controller(tmp_path,'owner',client)
    manifest={'session_timeout':600,'runtime_source':'owner/runtime','items':[
        {'id':'a','input_hash':'fixed','messages':[{'role':'user','content':'fixture'}]}]}
    batch=c.prepare(manifest,'MANIFEST = None\n')
    c._set(batch,'submit_unknown',error='network')
    with c.db() as db:db.execute('UPDATE batches SET updated=100 WHERE id=?',(batch,))
    return c,batch,calls,manifest


@pytest.mark.parametrize('record',[None,[],{'retry_at':True},{'retry_at':float('inf')},{'retry_at':float('nan')},{'retry_at':-1}])
def test_retry_metadata_is_fail_closed(record):
    with pytest.raises((ValueError,TypeError)):effective_retry_at(record)
    assert effective_retry_at({})==0
    assert effective_retry_at({'retry_at':1234})==1234


@pytest.mark.parametrize('message,code',[('404 Not Found https://token.example/secret','inaccessible'),
    ('401 invalid credentials secret','authentication'),('403 forbidden secret','inaccessible'),
    ('429 too many requests secret','rate_limited'),('Connection timed out secret','network'),('secret','provider_unavailable')])
def test_error_classification_never_returns_provider_secrets(message,code):
    error=classify_failure(message)
    assert error.code==code and str(error)==code
    assert str(ProviderError('secret'))=='provider_unavailable'


def test_cli_failures_are_typed_without_raw_output(uncertain):
    c,_batch,_calls,_manifest=uncertain
    with patch('batch_control.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='secret-token',stderr='404 Not Found')), pytest.raises(ProviderError,match='^inaccessible$'):
        c._cli(['kernels','status','owner/id'],20)
    with patch('batch_control.subprocess.run',side_effect=subprocess.TimeoutExpired('secret-command',20)), pytest.raises(ProviderError,match='^network$'):
        c._cli(['kernels','status','owner/id'],20)


@pytest.mark.parametrize('pages',[
    ['Not found\n'],
    [json.dumps([{'ref':'foreign/sibling'}]),'Not found\n'],
    [json.dumps([{'ref':'owner/sibling'}]),json.dumps([{'ref':'owner/sibling'}])],
    ['malformed output'],
    [json.dumps([{'ref':'owner/id'}])],
])
def test_ambiguous_account_lists_never_prove_absence(pages):
    calls=[]
    def client(args,timeout):
        calls.append(args)
        assert args[:2]==['kernels','list']
        return pages[min(int(args[args.index('--page')+1])-1,len(pages)-1)]
    if pages[0]=='malformed output':
        with pytest.raises(ValueError):prove_absent(client,'owner','id')
    else:
        assert prove_absent(client,'owner','id') is None
    assert not any(c[0]=='quota' for c in calls)


def test_listing_continues_short_pages_and_finds_late_exact_id():
    calls=[]
    def client(args,timeout):
        calls.append(args)
        page=int(args[args.index('--page')+1])
        return json.dumps([{'ref':'owner/other' if page==1 else 'owner/target'}])
    assert prove_absent(client,'owner','target') is None
    assert len(calls)==2


def test_listing_at_bound_is_unknown_not_negative():
    def client(args,timeout):
        page=args[args.index('--page')+1]
        return json.dumps([{'ref':'owner/item'+page}])
    assert prove_absent(client,'owner','target') is None


def test_zero_quota_allows_proof_but_unknown_quota_does_not():
    def client(args,timeout):
        if args[0]=='quota':return '[{"resource":"GPU","remaining":"0h"}]'
        return 'ref,title\nowner/sibling,Visible\n' if args[args.index('--page')+1]=='1' else 'ref,title\n'
    proof=prove_absent(client,'owner','target')
    assert proof['pages']==2 and proof['listed_count']==1
    def bad_quota(args,timeout):
        if args[0]=='quota':raise ProviderError('authentication')
        return client(args,timeout)
    assert prove_absent(bad_quota,'owner','target') is None


@pytest.mark.parametrize('state,remote',[('running',None),('submitted',None),('downloaded','COMPLETE'),('submit_unknown','RUNNING')])
def test_previously_observed_or_known_remote_jobs_never_retire(uncertain,state,remote):
    c,batch,calls,_manifest=uncertain
    c._set(batch,state,remote=remote)
    with c.db() as db:db.execute('UPDATE batches SET updated=100 WHERE id=?',(batch,))
    if state=='downloaded' and remote=='COMPLETE':
        assert c.status(batch)['state']=='downloaded'
        assert calls==[]
        return
    with patch('batch_control.time.time',return_value=5000),pytest.raises(ProviderError):c.status(batch)
    assert c.row(batch)['state']==state
    assert calls==[['kernels','status','owner/'+batch]]


def test_two_separated_bound_proofs_then_stable_retired_manifest_obstruction(uncertain):
    c,batch,calls,manifest=uncertain
    with patch('batch_control.time.time',return_value=2000),pytest.raises(ProviderError):c.status(batch)
    with patch('batch_control.time.time',return_value=2300),pytest.raises(ProviderError):c.status(batch)
    assert c.row(batch)['state']=='submit_unknown'
    with patch('batch_control.time.time',return_value=2700):assert c.status(batch)['state']=='retired'
    assert c.row(batch)['error']=='confirmed_not_found_after_network'
    with pytest.raises(RetiredManifest,match='retired_manifest_requires_new_attempt'):
        c.prepare(manifest,'MANIFEST = None\n')
    assert not any(args[:2]==['kernels','push'] for args in calls)


def test_legacy_unbound_stale_and_corrupt_proofs_cannot_retire(uncertain):
    c,batch,_calls,_manifest=uncertain
    receipt=c.root/batch/'absence-observations.json'
    receipt.write_text(json.dumps({'count':1,'last_at':1000,'first_at':1000}))
    with patch('batch_control.time.time',return_value=2000),pytest.raises(ProviderError):c.status(batch)
    assert json.loads(receipt.read_text())['count']==1
    with patch('batch_control.time.time',return_value=7000),pytest.raises(ProviderError):c.status(batch)
    assert json.loads(receipt.read_text())['first_at']==7000
    assert c.row(batch)['state']=='submit_unknown'


def test_changed_ledger_wins_over_inflight_absence_proof(uncertain):
    c,batch,_calls,_manifest=uncertain
    original=c.client
    def concurrent(args,timeout):
        result=original(args,timeout)
        if args[0]=='quota':c._set(batch,'running',remote='RUNNING')
        return result
    c.client=concurrent
    with patch('batch_control.time.time',return_value=2000):assert c.status(batch)['state']=='running'
    assert not (c.root/batch/'absence-observations.json').exists()


def test_ambiguous_probe_breaks_prior_negative_sequence(uncertain):
    c,batch,_calls,_manifest=uncertain
    with patch('batch_control.time.time',return_value=2000),pytest.raises(ProviderError):c.status(batch)
    original=c.client
    def wrong_account(args,timeout):
        if args[:2]==['kernels','list']:return 'Not found\n'
        return original(args,timeout)
    c.client=wrong_account
    with patch('batch_control.time.time',return_value=2700),pytest.raises(ProviderError):c.status(batch)
    assert json.loads((c.root/batch/'absence-observations.json').read_text())['count']==0
    c.client=original
    with patch('batch_control.time.time',return_value=3400),pytest.raises(ProviderError):c.status(batch)
    assert c.row(batch)['state']=='submit_unknown'


def test_drain_retires_without_counting_import_or_starting_new_job(tmp_path):
    state={'value':'submit_unknown'}
    c=SimpleNamespace(root=tmp_path,row=lambda batch:{'state':state['value']})
    calls=[]
    def bridge(path,action,*args,**kwargs):
        calls.append(action)
        if action=='prepare':return {'existing_batch':'b'}
        state['value']='retired'
        return {'state':'retired','id':'b'}
    result=drain('config',{'batch_limit':20,'drain_queue':True,'cycle_timeout_seconds':60},c,
        call=bridge,sleep=lambda _:None,clock=lambda:0)
    assert calls==['prepare','advance']
    assert result['state']=='retired_missing_remote' and result['completed_batches']==0
    assert result['recovery_required'] is True and result['gpu_started'] is False


def test_audit_is_append_only_redacted_and_refuses_symlinks(tmp_path):
    audit=Audit(tmp_path/'audit')
    payload={'started':['primary'],'start_failures':[{'lane':'primary','error':'SecretToken'}],
             'due_unclaimed':2,'due_claimed':1,'secret':'never-store'}
    audit.append('scheduler_tick',**payload)
    audit.append('scheduler_tick',**payload)
    path=tmp_path/'audit/events.jsonl'
    assert len(path.read_text().splitlines())==2 and 'SecretToken' not in path.read_text()
    assert 'never-store' not in path.read_text() and path.stat().st_mode & 0o777==0o600
    other=tmp_path/'other';other.mkdir();(other/'events.jsonl').symlink_to(path)
    with pytest.raises(OSError):Audit(other).append('scheduler_tick')
    assert len(path.read_text().splitlines())==2


def test_retired_manifest_block_stops_drain_without_advance(tmp_path):
    c=SimpleNamespace(root=tmp_path)
    calls=[]
    def bridge(path,action,*args,**kwargs):
        calls.append(action)
        return {'state':'retired_manifest_requires_new_attempt','batch_id':'b','recovery_required':True}
    result=drain('config',{'batch_limit':20,'drain_queue':True,'cycle_timeout_seconds':60},c,
        call=bridge,sleep=lambda _:None,clock=lambda:0)
    assert calls==['prepare']
    assert result['state']=='retired_manifest_requires_new_attempt'
    assert result['recovery_required'] is True


def test_scheduler_does_not_reextract_blocked_manifest_each_tick(uncertain,monkeypatch):
    import lane_scheduler
    c,batch,_calls,_manifest=uncertain
    (c.root/'cycle-status.json').write_text(json.dumps({'state':'retired_missing_remote','recovery_required':True}))
    monkeypatch.setattr(lane_scheduler,'KEYS',('primary',))
    monkeypatch.setattr(lane_scheduler,'lane_config',lambda key:{'state_root':str(c.root)})
    monkeypatch.setattr(lane_scheduler,'query_config',lambda cfg:{'allowed':True,'state':'available'})
    lanes=lane_scheduler.snapshot_lanes(2000,{'primary':'inactive'})
    assert lanes['primary']['ready'] is False
    assert lanes['primary']['outstanding']['id']==batch
    assert lane_scheduler.plan(lanes,20,0)[0]==[]
