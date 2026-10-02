"""Synthetic stop-policy proofs; all provider/credential/service boundaries mocked."""
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import cloud_bridge
import cloud_cycle
import lane_scheduler as scheduler
from batch_control import Controller
from dispatch_policy import (ConfigGuard,DispatchStopped,automatic_allowed,
    manual_target,readonly_reconcile)
from queue_dispatch import claimed_entries
from quota_guard import may_start
from recovery_policy import ProviderError
from test_required_ledgers import batch,bridge_config,fresh,manifest,topology,run_bridge


def config_file(tmp_path,root,enabled=True):
    path,cfg=bridge_config(tmp_path,root,[])
    cfg.update(schedule_enabled=enabled,interval_hours=6,batch_limit=20,
               cycle_timeout_seconds=60,drain_queue=True)
    path.write_text(json.dumps(cfg))
    return path,cfg


def change(path,**kw):
    cfg=json.loads(path.read_text());cfg.update(kw);path.write_text(json.dumps(cfg))


def cycle(monkeypatch,path,*args):
    monkeypatch.setattr(sys,'argv',['cloud_cycle','--config',str(path),*args])
    cloud_cycle.main()


def no_commands(monkeypatch):
    forbidden=Mock(side_effect=AssertionError('no external boundary'))
    monkeypatch.setattr(Controller,'_cli',forbidden)
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=forbidden))
    return forbidden


@pytest.mark.parametrize('flag',[False,None,0,1,'true',[],{}])
def test_disabled_scheduler_old_batch_and_counters_are_untouched(tmp_path,monkeypatch,flag):
    roots,configs=topology(tmp_path,monkeypatch)
    c=Controller(roots[4],'fixture');key=c.prepare(manifest(17),'MANIFEST = None\n')
    c._set(key,'submit_unknown')
    recovery=roots[4]/'recovery.json';recovery.write_text('{"failures":17,"retry_at":0,"at":0}')
    before=(roots[4]/'batches.sqlite3').read_bytes(),recovery.read_bytes()
    for cfg in configs.values():cfg['schedule_enabled']=flag
    forbidden=Mock(side_effect=AssertionError('disabled dispatch cannot inspect provider/services/ledger'))
    monkeypatch.setattr(scheduler,'claimed_entries',forbidden)
    monkeypatch.setattr(scheduler,'query_config',forbidden)
    report=scheduler.tick(run=forbidden,starter=forbidden,now=100)
    assert report['state']=='schedule_disabled' and report['started']==[]
    forbidden.assert_not_called()
    assert before==((roots[4]/'batches.sqlite3').read_bytes(),recovery.read_bytes())
    assert claimed_entries([roots[4]])=={17}


@pytest.mark.parametrize('state',['prepared','submitting','submitted','running','submit_unknown','terminal','downloaded'])
@pytest.mark.parametrize('flag',[False,None,'true'])
def test_old_recovery_never_bypasses_stop_in_plan(state,flag):
    lane={'outstanding':{'state':state},'quota_gate':{'allowed':True},'schedule_enabled':flag}
    assert not may_start(lane)


def test_mixed_flags_query_only_enabled_mutating_lanes(tmp_path,monkeypatch):
    roots,configs=topology(tmp_path,monkeypatch)
    for key,cfg in configs.items():cfg['schedule_enabled']=key=='secondary'
    configs['third']['schedule_enabled']=True;configs['third']['reconcile_only']=True
    calls=[]
    def quota(cfg,*,authorize):
        authorize();calls.append(cfg['state_root']);return {'allowed':True,'state':'available'}
    monkeypatch.setattr(scheduler,'query_config',quota)
    lanes=scheduler.snapshot_lanes(100,{k:'inactive' for k in scheduler.KEYS},configs)
    assert calls==[str(roots[1])]
    assert lanes['secondary']['ready']
    assert all(not lanes[k]['ready'] for k in scheduler.KEYS if k!='secondary')
    assert scheduler.plan(lanes,40,0)[0]==['secondary']


def scheduler_inputs(monkeypatch,configs):
    monkeypatch.setattr(scheduler,'service_states',lambda *a:{k:'inactive' for k in scheduler.KEYS})
    def quota(cfg,*,authorize):
        authorize();return {'allowed':True,'state':'available'}
    monkeypatch.setattr(scheduler,'query_config',quota)
    monkeypatch.setattr(scheduler,'due_entries',lambda *a:({1},set()))
    monkeypatch.setattr(scheduler,'queue_summary',lambda *a:{'next_item_retry':0,'analyses':{'waiting_model':1}})


def test_enabled_scheduler_preserves_success_and_claims(tmp_path,monkeypatch):
    roots,configs=topology(tmp_path,monkeypatch);scheduler_inputs(monkeypatch,configs)
    starter=Mock()
    assert scheduler.tick(starter=starter,now=100)['started']==['primary']
    starter.assert_called_once()


def test_disable_after_plan_preserves_scheduler_recovery_and_zero_starters(tmp_path,monkeypatch):
    roots,configs=topology(tmp_path,monkeypatch);scheduler_inputs(monkeypatch,configs)
    folder=tmp_path/'state/kaggle-month-dispatch';folder.mkdir(parents=True)
    rec=folder/'recovery.json';rec.write_text('{"failures":17,"retry_at":0,"at":0}')
    before=rec.read_bytes()
    def plan(*args):
        for cfg in configs.values():cfg['schedule_enabled']=False
        return ['fifth'],0
    monkeypatch.setattr(scheduler,'plan',plan);starter=Mock()
    report=scheduler.tick(starter=starter,now=4000)
    assert report['state']=='schedule_disabled' and report['started']==[]
    starter.assert_not_called();assert rec.read_bytes()==before


def test_worker_rechecks_disable_before_nonblocking_child_start(tmp_path,monkeypatch,capsys):
    roots,configs=topology(tmp_path,monkeypatch);scheduler_inputs(monkeypatch,configs)
    path,cfg=config_file(tmp_path,roots[0]);forbidden=no_commands(monkeypatch)
    constructor=Mock(side_effect=AssertionError('worker stopped before Controller'))
    monkeypatch.setattr('batch_control.Controller',constructor)
    def starter(key,run):
        for value in configs.values():value['schedule_enabled']=False
        change(path,schedule_enabled=False)
        cycle(monkeypatch,path)
    report=scheduler.tick(starter=starter,now=100)
    assert json.loads(capsys.readouterr().out)['state']=='schedule_disabled'
    assert report['state']=='schedule_disabled' and report['started']==['primary']
    constructor.assert_not_called();forbidden.assert_not_called()


@pytest.mark.parametrize('flag',[False,None,0,1,'true'])
def test_cycle_flag_closed_before_state_credentials_and_provider(tmp_path,monkeypatch,capsys,flag):
    path=tmp_path/'cfg.json';path.write_text(json.dumps({'schedule_enabled':flag}))
    forbidden=no_commands(monkeypatch)
    constructor=Mock(side_effect=AssertionError('no Controller'))
    monkeypatch.setattr('batch_control.Controller',constructor)
    cycle(monkeypatch,path)
    assert json.loads(capsys.readouterr().out)=={'state':'schedule_disabled','gpu_started':False}
    constructor.assert_not_called();forbidden.assert_not_called()


@pytest.mark.parametrize('value',['{}','{bad','[]','null'])
def test_missing_or_invalid_cycle_config_fails_closed(tmp_path,monkeypatch,capsys,value):
    path=tmp_path/'cfg.json';path.write_text(value);forbidden=no_commands(monkeypatch)
    cycle(monkeypatch,path)
    result=json.loads(capsys.readouterr().out)
    assert result['state'] in {'schedule_disabled','configuration_unavailable'}
    forbidden.assert_not_called()


def test_stop_during_660_poll_sleep_never_advances_or_resets_recovery(tmp_path,monkeypatch):
    c,key=batch(tmp_path/'own',1,'running');path,cfg=config_file(tmp_path,c.root)
    cfg['exception_audit_root']=str(tmp_path/'audit');cfg['cycle_timeout_seconds']=19800;path.write_text(json.dumps(cfg))
    rec=c.root/'recovery.json';rec.write_text('{"failures":17,"retry_at":0,"at":0}')
    before=rec.read_bytes();calls=[]
    def call(path,action,*args,**kwargs):
        calls.append(action);assert action=='prepare';return {'existing_batch':key}
    sleeps=[]
    def sleep(seconds):sleeps.append(seconds);change(path,schedule_enabled=False)
    result=cloud_cycle.drain(path,cfg,c,call=call,sleep=sleep,clock=lambda:0,
                            authorize=ConfigGuard(path,cfg))
    assert result['state']=='schedule_disabled' and calls==['prepare']
    assert sleeps==[660] and rec.read_bytes()==before
    assert claimed_entries([c.root])=={1} and c.row(key)['state']=='running'


def test_stopped_child_report_is_not_successful_recovery_reset(tmp_path):
    c,key=batch(tmp_path/'own');path,cfg=config_file(tmp_path,c.root)
    cfg['exception_audit_root']=str(tmp_path/'audit')
    rec=c.root/'recovery.json';rec.write_text('{"failures":17,"retry_at":0,"at":0}')
    before=rec.read_bytes()
    result=cloud_cycle.drain(path,cfg,c,call=lambda *a,**k:{'state':'schedule_disabled'},clock=lambda:0)
    assert result['state']=='schedule_disabled' and rec.read_bytes()==before


def test_disable_during_quota_before_push_rolls_back_prepared(tmp_path):
    c,key=batch(tmp_path/'own');path,cfg=config_file(tmp_path,c.root)
    guard=ConfigGuard(path,cfg);calls=[]
    def provider(args,timeout):
        calls.append(args);assert args[0]=='quota'
        change(path,schedule_enabled=False)
        return '[{"resource":"GPU","remaining":"20h"}]'
    guarded=Controller(c.root,'fixture',provider,admission=guard)
    with pytest.raises(DispatchStopped):guarded.submit(key)
    assert calls==[['quota','--format','json']]
    assert c.row(key)['state']=='prepared' and claimed_entries([c.root])=={1}


def test_exact_config_identity_change_stops_provider(tmp_path):
    c,key=batch(tmp_path/'own',1,'submit_unknown');path,cfg=config_file(tmp_path,c.root)
    provider=Mock(side_effect=AssertionError('no provider on changed owner/settings'))
    guard=ConfigGuard(path,cfg);control=Controller(c.root,'fixture',provider,admission=guard)
    change(path,owner='different')
    with pytest.raises(DispatchStopped,match='configuration_changed'):control.status(key)
    provider.assert_not_called();assert c.row(key)['state']=='submit_unknown'


@pytest.mark.parametrize('args',[('--manual',),('--manual-recovery',)])
def test_legacy_bypass_or_unscoped_manual_is_not_authority(tmp_path,monkeypatch,capsys,args):
    root=tmp_path/'own';fresh(root);path,cfg=config_file(tmp_path,root,False)
    forbidden=no_commands(monkeypatch)
    cycle(monkeypatch,path,*args)
    assert json.loads(capsys.readouterr().out)['state'] in {'manual_authorization_required','manual_recovery_requires_existing_batch'}
    forbidden.assert_not_called()


@pytest.mark.parametrize('target',['missing','prepared'])
def test_explicit_manual_recovery_cannot_submit_or_create_batch(tmp_path,monkeypatch,capsys,target):
    c,key=batch(tmp_path/'own');path,cfg=config_file(tmp_path,c.root,False)
    before=(c.root/'batches.sqlite3').read_bytes();forbidden=no_commands(monkeypatch)
    cycle(monkeypatch,path,'--manual-recovery','--batch',key if target=='prepared' else 'missing')
    assert json.loads(capsys.readouterr().out)['state'] in {'manual_recovery_cannot_submit','manual_recovery_requires_existing_batch'}
    forbidden.assert_not_called();assert (c.root/'batches.sqlite3').read_bytes()==before


@pytest.mark.parametrize('state',['submitting','submitted','running','submit_unknown'])
def test_explicit_recover_same_id_while_stopped_never_queries_quota_or_pushes(tmp_path,monkeypatch,capsys,state):
    c,key=batch(tmp_path/'own',1,state);path,cfg=config_file(tmp_path,c.root,False)
    calls=[]
    def provider(self,args,timeout):
        calls.append(args);assert args==['kernels','status','fixture/'+key]
        return 'has status "KernelWorkerStatus.RUNNING"'
    monkeypatch.setattr(Controller,'_cli',provider)
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=lambda *a:{}))
    run_bridge(monkeypatch,path,'recover','--batch',key)
    assert len(calls)==1 and json.loads(capsys.readouterr().out)['id']==key
    assert claimed_entries([c.root])=={1} and c.row(key)['state']=='running'


def test_manual_unknown_not_found_never_retires_or_creates_absence_proof(tmp_path,monkeypatch):
    c,key=batch(tmp_path/'own',1,'submit_unknown');path,cfg=config_file(tmp_path,c.root,False)
    with c.db() as db:db.execute('UPDATE batches SET updated=0')
    provider=Mock(side_effect=ProviderError('not_found'))
    monkeypatch.setattr(Controller,'_cli',provider)
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=lambda *a:{}))
    with pytest.raises(ProviderError):run_bridge(monkeypatch,path,'recover','--batch',key)
    assert provider.call_count==1
    assert c.row(key)['state']=='submit_unknown' and claimed_entries([c.root])=={1}
    assert not (c.root/key/'absence-observations.json').exists()


def test_manual_cooldown_is_preserved_before_credentials_and_controller(tmp_path,monkeypatch,capsys):
    c,key=batch(tmp_path/'own',1,'running');path,cfg=config_file(tmp_path,c.root,False)
    rec=c.root/'recovery.json';rec.write_text('{"failures":17,"retry_at":99999999999,"at":0}')
    before=rec.read_bytes();forbidden=no_commands(monkeypatch)
    constructor=Mock(side_effect=AssertionError('no Controller during cooldown'))
    monkeypatch.setattr('batch_control.Controller',constructor)
    cycle(monkeypatch,path,'--manual-recovery','--batch',key)
    assert json.loads(capsys.readouterr().out)['state']=='cooldown'
    assert rec.read_bytes()==before;constructor.assert_not_called();forbidden.assert_not_called()


def test_manual_drain_is_bound_to_one_id_and_never_prepares(tmp_path):
    c,key=batch(tmp_path/'own',1,'submit_unknown');path,cfg=config_file(tmp_path,c.root,False)
    cfg['cycle_timeout_seconds']=60;calls=[]
    def call(path,action,*args,**kwargs):
        calls.append((action,args));c._set(key,'imported');return {'import_states':{'imported':1}}
    result=cloud_cycle.drain(path,cfg,c,call=call,clock=lambda:0,
                            authorize=ConfigGuard(path,json.loads(path.read_text()),manual_recovery=True),recovery_batch=key)
    assert result['state']=='completed' and calls==[('recover',('--batch',key))]
    with c.db() as db:assert db.execute('SELECT count(*) FROM batches').fetchone()[0]==1


@pytest.mark.parametrize('explicit',[True,False])
def test_readonly_reconcile_never_constructs_migrates_or_contacts_provider(tmp_path,monkeypatch,capsys,explicit):
    c,key=batch(tmp_path/'own',1,'submit_unknown');path,cfg=config_file(tmp_path,c.root,not explicit)
    with c.db() as db:db.execute('DROP TABLE batch_claims')
    cfg['reconcile_only']=True;path.write_text(json.dumps(cfg))
    before={p:p.read_bytes() for p in c.root.rglob('*') if p.is_file()}
    constructor=Mock(side_effect=AssertionError('readonly must not construct Controller'))
    monkeypatch.setattr('batch_control.Controller',constructor);forbidden=no_commands(monkeypatch)
    cycle(monkeypatch,path,*(['--reconcile-readonly'] if explicit else []))
    result=json.loads(capsys.readouterr().out)
    assert result['state']=='readonly_reconciliation' and result['batches'][0]['id']==key
    assert before=={p:p.read_bytes() for p in c.root.rglob('*') if p.is_file()}
    constructor.assert_not_called();forbidden.assert_not_called()


def test_disabled_reconcile_only_does_not_implicitly_authorize_observation(tmp_path,monkeypatch,capsys):
    path=tmp_path/'cfg.json';path.write_text('{"schedule_enabled":false,"reconcile_only":true}')
    forbidden=no_commands(monkeypatch);cycle(monkeypatch,path)
    assert json.loads(capsys.readouterr().out)['state']=='schedule_disabled'
    forbidden.assert_not_called()


def test_disable_during_extraction_blocks_manifest_publication(tmp_path,monkeypatch):
    root=tmp_path/'own';fresh(root);path,cfg=config_file(tmp_path,root)
    with sqlite3.connect(cfg['database']) as db:
        db.execute('CREATE TABLE kaggle_prepare_leases(entry_id INTEGER,owner TEXT,expires REAL)')
    Path(cfg['versions']).write_text('{}')
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=lambda *a:{}))
    monkeypatch.setattr('quota_guard.query_client',lambda *a:{'allowed':True})
    monkeypatch.setattr(cloud_bridge,'validate_model_config',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'backup_before_import',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'resolve_entry_ids',lambda *a:[1])
    async def extract(*args,**kwargs):
        kwargs['on_claimed']();change(path,schedule_enabled=False)
        return {'samples':[],'considered':1,'skipped':[],'next_retry_at':None}
    monkeypatch.setattr(cloud_bridge,'prepare_sample',extract)
    publish=Mock(side_effect=AssertionError('stop before real publication'))
    monkeypatch.setattr(Controller,'prepare',publish)
    with pytest.raises(DispatchStopped):run_bridge(monkeypatch,path,'prepare')
    publish.assert_not_called();assert not list(root.glob('*/manifest.json'))


def test_paused_scheduler_snapshot_and_worker_quota_have_zero_provider_calls(tmp_path,monkeypatch):
    roots,configs=topology(tmp_path,monkeypatch)
    scheduler.STAGE.mkdir();(scheduler.STAGE/'paused.json').write_text('{}')
    quota=Mock(side_effect=AssertionError('paused provider boundary'));monkeypatch.setattr(scheduler,'query_config',quota)
    lanes=scheduler.snapshot_lanes(100,{k:'inactive' for k in scheduler.KEYS},configs)
    assert all(not lane['ready'] for lane in lanes.values());quota.assert_not_called()


@pytest.mark.parametrize('fault',['missing','corrupt'])
def test_readonly_missing_or_corrupt_self_does_not_rebuild(tmp_path,monkeypatch,capsys,fault):
    root=tmp_path/'own';fresh(root);path,cfg=config_file(tmp_path,root,False)
    database=root/'batches.sqlite3'
    if fault=='missing':database.unlink()
    else:database.write_bytes(b'private corrupt ledger')
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    constructor=Mock(side_effect=AssertionError('no recovery reconstruction'))
    monkeypatch.setattr('batch_control.Controller',constructor);forbidden=no_commands(monkeypatch)
    cycle(monkeypatch,path,'--reconcile-readonly')
    result=json.loads(capsys.readouterr().out)
    assert result['state']=='dispatch_blocked'
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert str(tmp_path) not in json.dumps(result) and 'private' not in json.dumps(result)
    constructor.assert_not_called();forbidden.assert_not_called()


@pytest.mark.parametrize('flag',[0,1,'true',{},[]])
def test_invalid_reconcile_policy_cannot_grant_mutating_schedule(flag):
    assert not automatic_allowed({'schedule_enabled':True,'reconcile_only':flag})


def test_parent_fingerprint_rejects_changed_enabled_child_before_controller(tmp_path,monkeypatch):
    c,key=batch(tmp_path/'own');path,cfg=config_file(tmp_path,c.root)
    expected=ConfigGuard(path,cfg).fingerprint
    change(path,owner='different-still-enabled')
    constructor=Mock(side_effect=AssertionError('no changed child Controller'))
    monkeypatch.setattr(cloud_bridge,'Controller',constructor);forbidden=no_commands(monkeypatch)
    with pytest.raises(DispatchStopped,match='configuration_changed'):
        run_bridge(monkeypatch,path,'prepare','--expected-config-sha256',expected)
    constructor.assert_not_called();forbidden.assert_not_called()


def test_cycle_passes_exact_parent_config_to_child_boundary(tmp_path):
    c,key=batch(tmp_path/'own',1,'submit_unknown');path,cfg=config_file(tmp_path,c.root)
    guard=ConfigGuard(path,cfg);calls=[]
    def call(path,action,*args,**kwargs):
        calls.append((action,kwargs));return {'state':'schedule_disabled'}
    result=cloud_cycle.drain(path,cfg,c,call=call,clock=lambda:0,authorize=guard)
    assert result['state']=='schedule_disabled'
    assert calls[0][1]['expected_config_sha256']==guard.fingerprint


def test_manual_identity_fingerprint_distinguishes_bool_and_integer(tmp_path):
    c,key=batch(tmp_path/'own',1,'running');path,cfg=config_file(tmp_path,c.root,False)
    guard=ConfigGuard(path,cfg,manual_recovery=True)
    change(path,schedule_enabled=0)
    with pytest.raises(DispatchStopped,match='configuration_changed'):guard()


@pytest.mark.parametrize('phase',['validation','upstream','import'])
def test_advance_stop_at_import_seams_does_not_defer_resolve_or_release(tmp_path,monkeypatch,phase):
    c=Controller(tmp_path/'own','fixture',initialize=True)
    value=manifest(1);value['items'][0]['kind']='analysis'
    key=c.prepare(value,'MANIFEST = None\n');c._set(key,'downloaded',remote='COMPLETE')
    path,cfg=config_file(tmp_path,c.root);item=value['items'][0]['id']
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=lambda *a:{}))
    monkeypatch.setattr(Controller,'status',lambda self,*a,**kw:self.row(key))
    monkeypatch.setattr(Controller,'download',lambda *a,**kw:{'missing_ids':[],'results':[{'id':item,'status':'ok'}]})
    def validate(*args):
        if phase=='validation':change(path,schedule_enabled=False)
        return {'valid':[],'invalid':[item]}
    async def upstream(*args,**kwargs):
        if phase=='upstream':change(path,schedule_enabled=False)
        return set()
    imports=[]
    def apply(*args,**kwargs):
        imports.append(1)
        if phase=='import':change(path,schedule_enabled=False)
        return {'items':[{'id':item,'state':'invalid'}]}
    monkeypatch.setattr(cloud_bridge,'validate',validate)
    monkeypatch.setattr(cloud_bridge,'verify_upstream',upstream)
    monkeypatch.setattr(cloud_bridge,'backup_before_import',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'import_validated',apply)
    deferred=Mock(side_effect=AssertionError('no stopped article retries or resolution'))
    monkeypatch.setattr(cloud_bridge,'defer_unresolved',deferred)
    with pytest.raises(DispatchStopped):run_bridge(monkeypatch,path,'advance','--batch',key)
    assert imports==([1] if phase=='import' else [])
    deferred.assert_not_called()
    assert not (c.root/key/'import-report.json').exists()
    assert c.row(key)['state']=='downloaded' and claimed_entries([c.root])=={1}
