"""Fail-closed dispatch contracts; all state is temporary, providers are mocked."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import batch_control
from batch_control import Controller, digest
import cloud_bridge
import cloud_cycle
import lane_scheduler as scheduler
from queue_dispatch import claimed_entries, required_roots, DispatchBlocked, block_with_backoff


def manifest(entry=1):
    return {'session_timeout':600, 'runtime_source':'fixture/runtime', 'items':[
        {'id':'a', 'messages':[{'role':'user','content':'fixture'}],
         'input_hash':'fixture', 'source_refs':[{'entry_id':entry}]}]}


def fresh(root, **kwargs):
    return Controller(root, 'fixture', client=Mock(side_effect=AssertionError('provider forbidden')), initialize=True, **kwargs)


def batch(root, entry=1, state='prepared'):
    control=fresh(root)
    key=control.prepare(manifest(entry), 'MANIFEST = None\n')
    control._set(key, state)
    return control,key


def test_required_roots_always_include_self(tmp_path):
    own=tmp_path/'self';peer=tmp_path/'peer'
    assert required_roots({'state_root':str(own),'peer_state_roots':[]}) == [own]
    assert required_roots({'state_root':str(own),'peer_state_roots':[str(peer),str(own)]}) == [own,peer]
    for config in [{}, {'state_root':str(own),'peer_state_roots':None}, {'state_root':str(own),'peer_state_roots':'oops'}]:
        with pytest.raises(DispatchBlocked):required_roots(config)
    with pytest.raises(DispatchBlocked):claimed_entries([])


@pytest.mark.parametrize('first',[True,False])
def test_missing_peer_never_returns_partial_set_or_creates_db(tmp_path,first):
    healthy,_=batch(tmp_path/'healthy',11,'running');missing=tmp_path/'secret-peer'
    roots=[missing,healthy.root] if first else [healthy.root,missing]
    with pytest.raises(DispatchBlocked) as caught:claimed_entries(roots)
    assert caught.value.code=='local_state'
    assert caught.value.reason=='missing_ledger'
    assert not missing.exists()
    assert str(tmp_path) not in json.dumps(caught.value.report())


@pytest.mark.parametrize('state',['prepared','submitting','submitted','running','submit_unknown','terminal','downloaded'])
def test_active_claim_survives_missing_and_corrupt_manifest_and_expired_retry(tmp_path,state):
    c,key=batch(tmp_path,4,state)
    with c.db() as db:db.execute('INSERT INTO batch_progress VALUES (?,0,99,?)',(key,'local_state'))
    path=tmp_path/key/'manifest.json'
    path.write_text('{ corrupt secret }')
    assert claimed_entries([tmp_path])=={4}
    path.unlink()
    assert claimed_entries([tmp_path])=={4}
    # Constructor must retain transactional-claim recovery of the same ID.
    recovered=Controller(tmp_path,'fixture',client=Mock())
    assert recovered.row(key)['state']==state


@pytest.mark.parametrize('state',['imported','retired','resolved'])
def test_only_finished_states_release(tmp_path,state):
    c,key=batch(tmp_path,4,state)
    (tmp_path/key/'manifest.json').unlink()
    assert claimed_entries([tmp_path])==set()


@pytest.mark.parametrize('mutation,reason',[
    ("UPDATE batches SET state='mystery'",'invalid_state'),
    ("UPDATE batches SET id='../secret'",'invalid_id'),
    ("INSERT INTO batch_claims VALUES ('orphan',3)",'orphan_claim'),
    ("UPDATE batch_claims SET entry_id=0",'invalid_id'),
    ("UPDATE batch_claims SET entry_id=-1",'invalid_id'),
    ("UPDATE batch_claims SET entry_id='secret-token'",'invalid_id'),
    ("UPDATE batch_claims SET entry_id=1.5",'invalid_id')])
def test_bad_ledger_never_returns_partial(tmp_path,mutation,reason):
    c,key=batch(tmp_path,1)
    with c.db() as db:db.execute(mutation)
    with pytest.raises(DispatchBlocked) as caught:claimed_entries([tmp_path])
    assert caught.value.reason==reason
    assert 'secret' not in str(caught.value)


@pytest.mark.parametrize('claims_table',[False,True])
def test_legacy_complete_manifest_fallback(tmp_path,claims_table):
    c,key=batch(tmp_path,2,'submit_unknown')
    with c.db() as db:
        db.execute('DELETE FROM batch_claims')
        if not claims_table:db.execute('DROP TABLE batch_claims')
    assert claimed_entries([tmp_path])=={2}
    value=json.loads((tmp_path/key/'manifest.json').read_text())
    value['items'][0]['source_refs'][0]['entry_id']=3
    (tmp_path/key/'manifest.json').write_text(json.dumps(value))
    with pytest.raises(DispatchBlocked) as caught:claimed_entries([tmp_path])
    assert caught.value.reason=='invalid_manifest'


@pytest.mark.parametrize('bad',[True,0,-1,'4',None,1.25])
def test_fallback_invalid_entry_ids_fail_even_with_matching_hash(tmp_path,bad):
    c,key=batch(tmp_path)
    value=json.loads((tmp_path/key/'manifest.json').read_text())
    value['items'][0]['source_refs'][0]['entry_id']=bad
    value['manifest_hash']=digest({k:v for k,v in value.items() if k not in {'batch_id','manifest_hash'}})
    (tmp_path/key/'manifest.json').write_text(json.dumps(value))
    with c.db() as db:
        db.execute('DELETE FROM batch_claims')
        db.execute('UPDATE batches SET manifest_hash=?',(value['manifest_hash'],))
    with pytest.raises(DispatchBlocked):claimed_entries([tmp_path])


def test_locked_and_corrupt_ledgers_are_typed(tmp_path):
    fresh(tmp_path)
    db=sqlite3.connect(tmp_path/'batches.sqlite3')
    try:
        db.execute('BEGIN EXCLUSIVE')
        with pytest.raises(DispatchBlocked) as caught:claimed_entries([tmp_path])
        assert caught.value.reason=='unreadable_ledger'
    finally:db.close()
    (tmp_path/'batches.sqlite3').write_bytes(b'corrupt secret content')
    before=(tmp_path/'batches.sqlite3').read_bytes()
    with pytest.raises(DispatchBlocked):Controller(tmp_path,'fixture')
    with pytest.raises(DispatchBlocked):Controller(tmp_path,'fixture',initialize=True)
    assert (tmp_path/'batches.sqlite3').read_bytes()==before


def test_read_only_snapshot_and_close_on_success_and_error(tmp_path,monkeypatch):
    c,key=batch(tmp_path,2)
    with c.db() as db:db.execute('PRAGMA journal_mode=WAL')
    original=sqlite3.connect;connections=[];injected=[False]
    class Tracked(sqlite3.Connection):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.closed=False;self.statements=[]
            self.set_trace_callback(self.statements.append);connections.append(self)
        def execute(self,sql,*args,**kwargs):
            if sql=='SELECT batch_id,entry_id FROM batch_claims' and not injected[0]:
                injected[0]=True
                with original(tmp_path/'batches.sqlite3') as writer:
                    writer.execute("INSERT INTO batch_claims VALUES ('orphan',9)")
            return super().execute(sql,*args,**kwargs)
        def close(self):self.closed=True;super().close()
    def connect(target,*args,**kwargs):
        assert target.endswith('?mode=ro') and kwargs['uri'] is True
        return original(target,*args,**kwargs,factory=Tracked)
    monkeypatch.setattr(sqlite3,'connect',connect)
    assert claimed_entries([tmp_path])=={2} # Writer changed ledger after snapshot started.
    assert connections[-1].closed and connections[-1].statements.count('BEGIN')==1
    with pytest.raises(DispatchBlocked):claimed_entries([tmp_path])
    assert all(conn.closed for conn in connections)
    assert not any(sql.startswith(('INSERT','UPDATE','DELETE','CREATE')) for conn in connections for sql in conn.statements)


def test_constructor_db_context_and_error_handler_do_not_recreate_self(tmp_path,monkeypatch):
    missing=tmp_path/'missing'
    with pytest.raises(DispatchBlocked):Controller(missing,'fixture')
    assert not missing.exists()
    c=fresh(tmp_path/'own');db=c.root/'batches.sqlite3';db.unlink()
    with pytest.raises(DispatchBlocked):
        with c.db():pass
    assert not db.exists()
    monkeypatch.setattr(cloud_bridge,'BRIDGE_CONTEXT',{'config':{'state_root':str(c.root),'owner':'fixture','kaggle_python':'never'},'action':'advance','batch':'b'},raising=False)
    assert cloud_bridge.handle_failure(OSError('secret'))['recovery_error']=='unknown'
    assert not db.exists()
    constructor=Mock(side_effect=AssertionError('no constructor on typed block'))
    monkeypatch.setattr(cloud_bridge,'Controller',constructor)
    assert cloud_bridge.handle_failure(DispatchBlocked('missing_ledger'))['state']=='dispatch_blocked'
    constructor.assert_not_called();assert not db.exists()


@pytest.mark.parametrize('evidence',['batch/manifest.json','batches.sqlite3-wal','recovery.json','cycle.lock'])
def test_init_refuses_existing_evidence(tmp_path,evidence):
    p=tmp_path/evidence;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
    with pytest.raises(DispatchBlocked) as caught:fresh(tmp_path)
    assert caught.value.reason=='initialization_evidence'
    assert not (tmp_path/'batches.sqlite3').exists()


def test_backoff_fixed_redaction_never_releases_claims(tmp_path,monkeypatch):
    c,key=batch(tmp_path,7,'submit_unknown')
    monkeypatch.setattr('recovery_policy.time.time',lambda:100)
    for delay in [660,1320,2640,3600,3600]:
        report=block_with_backoff(tmp_path,DispatchBlocked('missing_ledger',2))
        assert report['retry_at']==100+delay
        assert report['reason']=='missing_ledger' and report['peer']=='peer_2'
        assert str(tmp_path) not in json.dumps(report)
        assert claimed_entries([tmp_path])=={7}
    assert c.row(key)['state']=='submit_unknown'


def test_prepared_submit_never_calls_provider_when_peer_missing(tmp_path):
    c,key=batch(tmp_path/'own')
    c.required_roots=[c.root,tmp_path/'missing']
    with pytest.raises(DispatchBlocked):c.submit(key)
    c.client.assert_not_called()
    assert c.row(key)['state']=='prepared'


@pytest.mark.parametrize('state',['submitting','submitted','running','submit_unknown'])
def test_same_id_recovery_does_not_need_missing_peer_or_resubmit(tmp_path,state):
    c,key=batch(tmp_path/'own',2,state)
    c.required_roots=[c.root,tmp_path/'missing']
    calls=[]
    def provider(args,timeout):
        calls.append(args)
        assert args[:2]==['kernels','status']
        assert args[-1]=='fixture/'+key
        return 'has status "KernelWorkerStatus.RUNNING"'
    c.client=provider
    assert c.status(key)['state']=='running'
    assert len(calls)==1
    assert claimed_entries([c.root])=={2}


def bridge_config(tmp_path,root,peers):
    cfg={'state_root':str(root),'peer_state_roots':[str(p) for p in peers],
         'owner':'fixture','kaggle_python':'never','source':str(tmp_path),
         'database':str(tmp_path/'inbox.sqlite3'),'versions':str(tmp_path/'versions.json'),
         'token_file':'fixture-unused','model_dataset':'fixture/model',
         'runtime_sha256':'fixture','runtime_source':'fixture/runtime','qwen_exception_review':False}
    p=tmp_path/'config.json';p.write_text(json.dumps(cfg));return p,cfg


def run_bridge(monkeypatch,path,action,*extra):
    monkeypatch.setattr(sys,'argv',['cloud_bridge','--config',str(path),action,*extra])
    cloud_bridge.main()


def test_bridge_prepared_gate_precedes_credentials(tmp_path,monkeypatch):
    c,key=batch(tmp_path/'own')
    path,cfg=bridge_config(tmp_path,c.root,[tmp_path/'missing'])
    creds=Mock(side_effect=AssertionError('no credential read'))
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=creds))
    with pytest.raises(DispatchBlocked):run_bridge(monkeypatch,path,'advance','--batch',key)
    creds.assert_not_called();c.client.assert_not_called()


def test_bridge_init_only_five_fresh_lanes_then_dispatch(tmp_path,monkeypatch):
    roots=[tmp_path/key for key in scheduler.KEYS]
    forbidden=Mock(side_effect=AssertionError('no external activity'))
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=forbidden))
    monkeypatch.setattr(cloud_bridge,'prepare_sample',forbidden)
    monkeypatch.setattr(cloud_bridge,'import_validated',forbidden)
    monkeypatch.setattr(Controller,'_cli',forbidden)
    for root in roots:
        path,cfg=bridge_config(tmp_path,root,roots)
        run_bridge(monkeypatch,path,'init')
    forbidden.assert_not_called()
    assert claimed_entries(roots)==set()
    c=Controller(roots[0],'fixture',required_roots=roots)
    key=c.prepare(manifest(9),'MANIFEST = None\n')
    assert c.row(key)['state']=='prepared' and claimed_entries(roots)=={9}


@pytest.mark.parametrize('lost',['peer','self'])
@pytest.mark.parametrize('phase',['extraction','build'])
def test_peer_disappears_during_extraction_without_exception_review(tmp_path,monkeypatch,lost,phase):
    root=tmp_path/'own';peer=tmp_path/'peer';fresh(root);fresh(peer)
    path,cfg=bridge_config(tmp_path,root,[peer])
    with sqlite3.connect(cfg['database']) as db:
        db.execute('CREATE TABLE kaggle_prepare_leases(entry_id INTEGER,owner TEXT,expires REAL)')
    Path(cfg['versions']).write_text('{}')
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=lambda *a:{}))
    monkeypatch.setattr('quota_guard.query_client',lambda *a:{'allowed':True})
    monkeypatch.setattr(cloud_bridge,'validate_model_config',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'backup_before_import',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'resolve_entry_ids',lambda *a:[1])
    async def extract(*args,**kwargs):
        kwargs['on_claimed']()
        if phase=='extraction':((peer if lost=='peer' else root)/'batches.sqlite3').unlink()
        return {'samples':[],'considered':1,'skipped':[],'next_retry_at':None}
    monkeypatch.setattr(cloud_bridge,'prepare_sample',extract)
    def build(*args,**kwargs):
        if phase=='build':((peer if lost=='peer' else root)/'batches.sqlite3').unlink()
        return manifest()
    monkeypatch.setattr(cloud_bridge,'build',build)
    publish=Mock(side_effect=AssertionError('must block before publication'))
    monkeypatch.setattr(Controller,'prepare',publish)
    with pytest.raises(DispatchBlocked):run_bridge(monkeypatch,path,'prepare')
    publish.assert_not_called()
    assert not list(root.glob('*/manifest.json'))
    assert not ((peer if lost=='peer' else root)/'batches.sqlite3').exists()


def topology(tmp_path,monkeypatch):
    roots=[tmp_path/key for key in scheduler.KEYS]
    configs={key:{'state_root':str(root),'peer_state_roots':[str(p) for p in roots]} for key,root in zip(scheduler.KEYS,roots)}
    for root in roots:fresh(root)
    monkeypatch.setattr(scheduler,'ROOT',tmp_path)
    monkeypatch.setattr(scheduler,'STAGE',tmp_path/'stage')
    monkeypatch.setattr(scheduler,'lane_config',configs.__getitem__)
    return roots,configs


@pytest.mark.parametrize('fault',['missing','corrupt','orphan','unknown','duplicate_root','missing_peer','bad_config'])
def test_scheduler_block_starts_zero_services_including_recovery(tmp_path,monkeypatch,fault):
    roots,configs=topology(tmp_path,monkeypatch)
    if fault=='missing':(roots[2]/'batches.sqlite3').unlink()
    if fault=='corrupt':(roots[2]/'batches.sqlite3').write_bytes(b'secret corrupt')
    if fault in {'orphan','unknown'}:
        c=Controller(roots[2],'fixture');key=c.prepare(manifest(),'MANIFEST = None\n')
        with c.db() as db:
            if fault=='orphan':db.execute("INSERT INTO batch_claims VALUES ('orphan',99)")
            else:db.execute("UPDATE batches SET state='invalid'")
    if fault=='duplicate_root':configs['third']['state_root']=str(roots[0])
    if fault=='missing_peer':configs['third']['peer_state_roots']=[]
    if fault=='bad_config':configs['third']=None
    run=Mock(side_effect=AssertionError('no systemctl or network'))
    starter=Mock(side_effect=AssertionError('no service start'))
    report=scheduler.tick(run=run,starter=starter,now=100)
    assert report['state']=='dispatch_blocked' and report['started']==[]
    assert report['recovery_error']=='local_state'
    run.assert_not_called();starter.assert_not_called()
    assert str(tmp_path) not in json.dumps(report)


def test_scheduler_rechecks_after_snapshot_before_any_start(tmp_path,monkeypatch):
    roots,configs=topology(tmp_path,monkeypatch)
    monkeypatch.setattr(scheduler,'service_states',lambda *a:{})
    lanes={k:{'active':False,'ready':True,'outstanding':{'state':'submit_unknown'},'retry_at':0,'cycle':{}} for k in scheduler.KEYS}
    def snapshot(*args):
        (roots[4]/'batches.sqlite3').unlink();return lanes
    monkeypatch.setattr(scheduler,'snapshot_lanes',snapshot)
    monkeypatch.setattr(scheduler,'due_entries',lambda *a:({1},set()))
    monkeypatch.setattr(scheduler,'queue_summary',lambda *a:{'next_item_retry':0})
    starter=Mock()
    assert scheduler.tick(starter=starter,now=100)['state']=='dispatch_blocked'
    starter.assert_not_called()


def test_cycle_missing_self_reports_block_without_bridge_or_credentials(tmp_path,monkeypatch):
    path=tmp_path/'config.json';root=tmp_path/'missing'
    path.write_text(json.dumps({'state_root':str(root),'owner':'fixture','kaggle_python':'never',
                               'interval_hours':6,'schedule_enabled':True,'peer_state_roots':[]}))
    monkeypatch.setattr(sys,'argv',['cloud_cycle','--config',str(path)])
    drain=Mock(side_effect=AssertionError('no bridge'))
    monkeypatch.setattr(cloud_cycle,'drain',drain)
    output=io.StringIO()
    with contextlib.redirect_stdout(output):cloud_cycle.main()
    assert json.loads(output.getvalue())['state']=='dispatch_blocked'
    drain.assert_not_called();assert not root.exists()


def test_named_five_lane_topology_cannot_be_truncated(tmp_path):
    roots=[tmp_path/('kaggle-month-'+key) for key in scheduler.KEYS]
    cfg={'state_root':str(roots[0]),'peer_state_roots':[str(p) for p in roots]}
    assert set(required_roots(cfg))==set(roots)
    for peers in [[],[str(p) for p in roots[:-1]]]:
        with pytest.raises(DispatchBlocked):required_roots({**cfg,'peer_state_roots':peers})


def test_bridge_block_record_is_counted_once_by_supervising_cycle(tmp_path,monkeypatch):
    root=tmp_path/'own';fresh(root)
    recorded={'state':'dispatch_blocked','recovery_error':'local_state','reason':'missing_ledger',
              'peer':'peer_1','code':'local_state','failures':1,'retry_at':760,'at':100}
    monkeypatch.setattr(cloud_cycle.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(recorded)))
    with pytest.raises(DispatchBlocked) as caught:cloud_cycle.bridge('fixture','prepare',timeout=1)
    assert caught.value.retry_record['failures']==1
    report=block_with_backoff(root,caught.value)
    assert report['failures']==1 and report['retry_at']==760
    assert not (root/'recovery.json').exists() # subprocess owns its already-persisted record


def test_nullable_legacy_state_and_missing_manifest_fail_typed(tmp_path):
    with sqlite3.connect(tmp_path/'batches.sqlite3') as db:
        db.execute('CREATE TABLE batches(id TEXT,manifest_hash TEXT,state TEXT,remote_status TEXT,error TEXT,updated REAL)')
        db.execute("INSERT INTO batches VALUES ('b',?,NULL,NULL,NULL,0)",('a'*64,))
    with pytest.raises(DispatchBlocked) as caught:claimed_entries([tmp_path])
    assert caught.value.reason=='invalid_state'
    with sqlite3.connect(tmp_path/'batches.sqlite3') as db:db.execute("UPDATE batches SET state='submitting'")
    with pytest.raises(DispatchBlocked) as caught:claimed_entries([tmp_path])
    assert caught.value.reason=='invalid_manifest'


@pytest.mark.parametrize('fault',['corrupt','unknown','orphan'])
@pytest.mark.parametrize('first',[True,False])
def test_bad_peer_before_or_after_healthy_never_returns_partial(tmp_path,fault,first):
    healthy,key=batch(tmp_path/'healthy',1)
    bad,key=batch(tmp_path/'bad',2)
    if fault=='corrupt':(bad.root/'batches.sqlite3').write_bytes(b'private secret')
    else:
        with bad.db() as db:
            if fault=='unknown':db.execute("UPDATE batches SET state='bogus'")
            else:db.execute("INSERT INTO batch_claims VALUES ('orphan',9)")
    roots=[bad.root,healthy.root] if first else [healthy.root,bad.root]
    with pytest.raises(DispatchBlocked):claimed_entries(roots)


def test_incomplete_schema_is_invalid_even_for_empty_ledger(tmp_path):
    with sqlite3.connect(tmp_path/'batches.sqlite3') as db:db.execute('CREATE TABLE batches(id,state)')
    with pytest.raises(DispatchBlocked) as caught:claimed_entries([tmp_path])
    assert caught.value.reason=='invalid_ledger'


def test_bridge_happy_prepare_publishes_with_all_fresh_peers(tmp_path,monkeypatch):
    root=tmp_path/'own';peer=tmp_path/'peer';fresh(root);fresh(peer)
    path,cfg=bridge_config(tmp_path,root,[peer])
    with sqlite3.connect(cfg['database']) as db:
        db.execute('CREATE TABLE kaggle_prepare_leases(entry_id INTEGER,owner TEXT,expires REAL)')
    Path(cfg['versions']).write_text('{}')
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=lambda *a:{}))
    monkeypatch.setattr('quota_guard.query_client',lambda *a:{'allowed':True})
    monkeypatch.setattr(cloud_bridge,'validate_model_config',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'backup_before_import',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'resolve_entry_ids',lambda *a:[1])
    monkeypatch.setattr(cloud_bridge,'build',lambda *a,**kw:manifest())
    async def extract(*args,**kwargs):
        with sqlite3.connect(cfg['database']) as db:
            db.execute('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)',(1,kwargs['lease_owner'],10**12))
        kwargs['on_claimed']()
        return {'samples':[{'entry_id':1}],'considered':1,'skipped':[],'next_retry_at':None}
    monkeypatch.setattr(cloud_bridge,'prepare_sample',extract)
    monkeypatch.setattr(Controller,'_cli',Mock(side_effect=AssertionError('no provider calls')))
    output=io.StringIO()
    with contextlib.redirect_stdout(output):run_bridge(monkeypatch,path,'prepare')
    result=json.loads(output.getvalue());assert result['selected']==1
    assert claimed_entries([root,peer])=={1}
    assert Controller(root,'fixture').row(result['batch_id'])['state']=='prepared'


def test_scheduler_fresh_complete_topology_can_start_mock_service(tmp_path,monkeypatch):
    roots,configs=topology(tmp_path,monkeypatch)
    monkeypatch.setattr(scheduler,'service_states',lambda *a:{})
    lanes={k:{'active':False,'ready':True,'outstanding':None,'retry_at':0,'cycle':{},'quota_gate':{'allowed':True}} for k in scheduler.KEYS}
    monkeypatch.setattr(scheduler,'snapshot_lanes',lambda *a:lanes)
    monkeypatch.setattr(scheduler,'due_entries',lambda *a:({1},set()))
    monkeypatch.setattr(scheduler,'queue_summary',lambda *a:{'next_item_retry':0,'analyses':{'waiting_model':1},'cards':{},'total':1})
    starter=Mock()
    report=scheduler.tick(starter=starter,now=100)
    assert report['state']=='started' and report['started']==['primary']
    starter.assert_called_once();assert claimed_entries(roots)==set()


def test_invalid_roots_are_typed_and_missing_root_still_has_retry(tmp_path,monkeypatch):
    with pytest.raises(DispatchBlocked):claimed_entries(None)
    monkeypatch.setattr('queue_dispatch.time.time',lambda:100)
    root=tmp_path/'never-created'
    report=block_with_backoff(root,DispatchBlocked('missing_ledger'))
    assert report['retry_at']==760 and report['code']=='local_state'
    assert not root.exists()


def test_standalone_cli_missing_self_outputs_redacted_typed_json(tmp_path):
    import subprocess
    root=tmp_path/'secret-path'
    result=subprocess.run([sys.executable,str(Path(batch_control.__file__)),
                           '--root',str(root),'--owner','fixture','status','a'],
                          check=True,capture_output=True,text=True,timeout=5)
    report=json.loads(result.stdout)
    assert report['state']=='dispatch_blocked' and report['recovery_error']=='local_state'
    assert 'secret-path' not in result.stdout and result.stderr==''
    assert not root.exists()


def test_standalone_month_controller_requires_five_peers_but_keeps_same_id_recovery(tmp_path):
    roots=[tmp_path/('kaggle-month-'+key) for key in scheduler.KEYS]
    for root in roots:fresh(root)
    c=Controller(roots[0],'fixture',client=Mock())
    key=c.prepare(manifest(),'MANIFEST = None\n')
    assert set(c.required_roots)==set(roots)
    (roots[-1]/'batches.sqlite3').unlink()
    with pytest.raises(DispatchBlocked):c.submit(key)
    c.client.assert_not_called()
    c._set(key,'submit_unknown')
    recovered=Controller(roots[0],'fixture',client=lambda *a:'has status "KernelWorkerStatus.RUNNING"')
    assert recovered.status(key)['state']=='running'
    assert claimed_entries([roots[0]])=={1}


@pytest.mark.parametrize('recovery',['[]','null','"private secret manifest"','12','{broken','{"code":"local_state","failures":[]}'])
@pytest.mark.parametrize('fault',['missing','corrupt'])
def test_malformed_recovery_and_bad_peer_return_redacted_block_without_provider(tmp_path,monkeypatch,recovery,fault):
    root=tmp_path/'own';peer=tmp_path/'peer';fresh(root);fresh(peer)
    (root/'recovery.json').write_text(recovery)
    if fault=='missing':(peer/'batches.sqlite3').unlink()
    else:(peer/'batches.sqlite3').write_bytes(b'private secret corruption')
    path,cfg=bridge_config(tmp_path,root,[peer])
    provider=Mock(side_effect=AssertionError('provider forbidden'))
    credentials=Mock(side_effect=AssertionError('credentials forbidden'))
    monkeypatch.setattr(Controller,'_cli',provider)
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=credentials))
    output=io.StringIO()
    with contextlib.redirect_stdout(output):
        try:run_bridge(monkeypatch,path,'prepare')
        except Exception as exc:print(json.dumps(cloud_bridge.handle_failure(exc)))
    report=json.loads(output.getvalue())
    assert report['state']=='dispatch_blocked' and report['recovery_error']=='local_state'
    assert report['failures']==1 and report['retry_at']>report['at']
    assert 'private' not in output.getvalue() and str(tmp_path) not in output.getvalue()
    assert json.loads((root/'recovery.json').read_text())['failures']==1
    provider.assert_not_called();credentials.assert_not_called()


@pytest.mark.parametrize('recovery',['[]','null','"private secret manifest"','{broken'])
@pytest.mark.parametrize('fault',['missing','corrupt'])
def test_scheduler_malformed_recovery_and_bad_peer_start_zero(tmp_path,monkeypatch,recovery,fault):
    roots,cfg=topology(tmp_path,monkeypatch)
    folder=tmp_path/'state/kaggle-month-dispatch';folder.mkdir(parents=True)
    (folder/'recovery.json').write_text(recovery)
    if fault=='missing':(roots[2]/'batches.sqlite3').unlink()
    else:(roots[2]/'batches.sqlite3').write_bytes(b'private secret corruption')
    run=Mock(side_effect=AssertionError('provider/systemctl forbidden'));starter=Mock()
    report=scheduler.tick(run=run,starter=starter,now=100)
    assert report['state']=='dispatch_blocked' and report['reason']=='invalid_recovery'
    assert report['retry_at']==760 and report['failures']==1
    assert str(tmp_path) not in json.dumps(report) and 'private' not in json.dumps(report)
    run.assert_not_called();starter.assert_not_called()
    # After the repaired record's cooldown, the bad peer is still checked and
    # cannot be mistaken for an empty queue.
    report=scheduler.tick(run=run,starter=starter,now=760)
    assert report['state']=='dispatch_blocked' and report['reason'] in {'missing_ledger','invalid_ledger'}
    assert report['retry_at']==2080 and report['failures']==2
    run.assert_not_called();starter.assert_not_called()


def test_scheduler_cooldown_honored_then_complete_revalidation_clears_it(tmp_path,monkeypatch):
    roots,cfg=topology(tmp_path,monkeypatch)
    c=Controller(roots[0],'fixture');key=c.prepare(manifest(99),'MANIFEST = None\n');c._set(key,'submit_unknown')
    inbox=tmp_path/'article-attempts.sqlite3'
    with sqlite3.connect(inbox) as db:
        db.execute('CREATE TABLE analyses(entry_id INTEGER,attempts INTEGER)');db.execute('INSERT INTO analyses VALUES (1,0)')
    article_bytes=inbox.read_bytes()
    peer_db=roots[2]/'batches.sqlite3';saved=peer_db.read_bytes();peer_db.unlink()
    run=Mock(side_effect=AssertionError('no commands until retry'));starter=Mock()
    report=scheduler.tick(run=run,starter=starter,now=100)
    assert report['retry_at']==760 and report['failures']==1
    peer_db.write_bytes(saved) # Repair does not bypass the recorded cooldown.
    read_claims=Mock(wraps=claimed_entries);monkeypatch.setattr(scheduler,'claimed_entries',read_claims)
    report=scheduler.tick(run=run,starter=starter,now=759)
    assert report['reason']=='local_state_cooldown' and report['retry_at']==760
    read_claims.assert_not_called();run.assert_not_called();starter.assert_not_called()
    assert claimed_entries(roots)=={99} and c.row(key)['state']=='submit_unknown'
    monkeypatch.setattr(scheduler,'service_states',lambda *a:{})
    lanes={k:{'active':False,'ready':True,'outstanding':None,'retry_at':0,'cycle':{},'quota_gate':{'allowed':True}} for k in scheduler.KEYS}
    monkeypatch.setattr(scheduler,'snapshot_lanes',lambda *a:lanes)
    monkeypatch.setattr(scheduler,'due_entries',lambda *a:({1},set()))
    monkeypatch.setattr(scheduler,'queue_summary',lambda *a:{'next_item_retry':0,'analyses':{'waiting_model':1},'cards':{},'total':1})
    report=scheduler.tick(run=run,starter=starter,now=760)
    assert report['state']=='started' and report['started']==['primary']
    assert read_claims.call_count==2 # All roots both on entry and before starts.
    assert all(set(call.args[0])==set(roots) for call in read_claims.call_args_list)
    reset=json.loads((tmp_path/'state/kaggle-month-dispatch/recovery.json').read_text())
    assert reset['failures']==0 and reset['retry_at']==0
    assert inbox.read_bytes()==article_bytes
    assert claimed_entries(roots)=={99} and c.row(key)['state']=='submit_unknown'
    # A later failure starts a fresh backoff sequence after successful admission.
    peer_db.unlink()
    report=scheduler.tick(run=run,starter=starter,now=761)
    assert report['failures']==1 and report['retry_at']==1421
    assert starter.call_count==1


def test_scheduler_retry_schedule_is_enforced_and_capped(tmp_path,monkeypatch):
    roots,cfg=topology(tmp_path,monkeypatch)
    (roots[4]/'batches.sqlite3').unlink()
    run=Mock(side_effect=AssertionError('no providers on blocked retry'));starter=Mock()
    now=100
    for count,delay in enumerate([660,1320,2640,3600,3600],1):
        report=scheduler.tick(run=run,starter=starter,now=now)
        assert report['failures']==count and report['retry_at']==now+delay
        cooldown=scheduler.tick(run=run,starter=starter,now=now+delay-1)
        assert cooldown['reason']=='local_state_cooldown' and cooldown['failures']==count
        assert cooldown['retry_at']==report['retry_at']
        now=report['retry_at']
    run.assert_not_called();starter.assert_not_called()


@pytest.mark.parametrize('fault',['missing','corrupt'])
def test_real_controller_inner_prepare_guard_rolls_back_without_publication(tmp_path,monkeypatch,fault):
    root=tmp_path/'own';peer=tmp_path/'peer';fresh(root);fresh(peer)
    c=Controller(root,'fixture',client=Mock(),required_roots=[root,peer]);checks=[]
    def check(roots):
        checks.append(tuple(roots))
        if len(checks)==2:
            if fault=='missing':(peer/'batches.sqlite3').unlink()
            else:(peer/'batches.sqlite3').write_bytes(b'private secret corruption')
        return claimed_entries(roots)
    monkeypatch.setattr(batch_control,'claimed_entries',check)
    with pytest.raises(DispatchBlocked):c.prepare(manifest(),'MANIFEST = None\n')
    assert len(checks)==2
    with c.db() as db:
        assert db.execute('SELECT count(*) FROM batches').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM batch_claims').fetchone()[0]==0
    assert not list(root.glob('*/manifest.json')) and not list(root.glob('*/runner.py'))
    c.client.assert_not_called()


def test_peer_claim_added_during_extraction_excludes_article_before_real_publish(tmp_path,monkeypatch):
    root=tmp_path/'own';peer=tmp_path/'peer';fresh(root);fresh(peer)
    peer_control=Controller(peer,'fixture',client=Mock())
    path,cfg=bridge_config(tmp_path,root,[peer])
    with sqlite3.connect(cfg['database']) as db:
        db.execute('CREATE TABLE kaggle_prepare_leases(entry_id INTEGER,owner TEXT,expires REAL)')
    Path(cfg['versions']).write_text('{}')
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=lambda *a:{}))
    monkeypatch.setattr('quota_guard.query_client',lambda *a:{'allowed':True})
    monkeypatch.setattr(cloud_bridge,'validate_model_config',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'backup_before_import',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'resolve_entry_ids',lambda *a:[1,2])
    published=[]
    def build(sample,*args,**kwargs):
        published.extend(row['entry_id'] for row in sample['samples'])
        assert published==[2]
        return manifest(2)
    monkeypatch.setattr(cloud_bridge,'build',build)
    async def extract(*args,**kwargs):
        assert not args[2] # Initial peer ledger was healthy and empty.
        with sqlite3.connect(cfg['database']) as db:
            db.executemany('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)',[(entry,kwargs['lease_owner'],10**12) for entry in (1,2)])
        kwargs['on_claimed']()
        key=peer_control.prepare(manifest(1),'MANIFEST = None\n')
        peer_control._set(key,'submit_unknown')
        return {'samples':[{'entry_id':1},{'entry_id':2}],'considered':2,'skipped':[],'next_retry_at':None}
    monkeypatch.setattr(cloud_bridge,'prepare_sample',extract)
    provider=Mock(side_effect=AssertionError('no Kaggle provider writes'))
    monkeypatch.setattr(Controller,'_cli',provider)
    output=io.StringIO()
    with contextlib.redirect_stdout(output):run_bridge(monkeypatch,path,'prepare')
    result=json.loads(output.getvalue());assert result['selected']==1
    own_control=Controller(root,'fixture',client=Mock())
    own_manifest=own_control.manifest(result['batch_id'])
    assert {ref['entry_id'] for item in own_manifest['items'] for ref in item['source_refs']}=={2}
    assert claimed_entries([peer])=={1} and claimed_entries([root])=={2}
    provider.assert_not_called();peer_control.client.assert_not_called()


@pytest.mark.parametrize('recovery',['[]','null','"private secret manifest"'])
@pytest.mark.parametrize('fault',['missing','corrupt'])
def test_cycle_malformed_recovery_with_bad_peer_remains_redacted_block(tmp_path,monkeypatch,recovery,fault):
    root=tmp_path/'own';peer=tmp_path/'peer';fresh(root);fresh(peer)
    (root/'recovery.json').write_text(recovery)
    if fault=='missing':(peer/'batches.sqlite3').unlink()
    else:(peer/'batches.sqlite3').write_bytes(b'private secret corruption')
    path,cfg=bridge_config(tmp_path,root,[peer])
    cfg.update(interval_hours=6,schedule_enabled=True,exception_audit_root=str(tmp_path/'audit'))
    path.write_text(json.dumps(cfg))
    monkeypatch.setattr(sys,'argv',['cloud_cycle','--config',str(path)])
    call=Mock(side_effect=AssertionError('no provider/bridge activity'))
    monkeypatch.setattr(cloud_cycle,'drain_once',call)
    monkeypatch.setattr(Controller,'_cli',call)
    output=io.StringIO()
    with contextlib.redirect_stdout(output):cloud_cycle.main()
    report=json.loads(output.getvalue())
    assert report['state']=='dispatch_blocked' and report['reason']=='invalid_recovery'
    assert 'private' not in output.getvalue() and str(tmp_path) not in output.getvalue()
    call.assert_not_called()
