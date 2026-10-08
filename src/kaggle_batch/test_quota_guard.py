import json
from types import SimpleNamespace

import pytest
from batch_control import Controller
from cloud_cycle import drain
from quota_guard import admission, may_start, plan_lanes, query_client, query_config


WARNING = ("Warning: Looks like you're using an outdated `kaggle` version "
           "(installed: 2.2.3), please consider upgrading to the latest version (2.2.4)")


@pytest.mark.parametrize('newline',['\n','\r\n'])
@pytest.mark.parametrize('value,allowed',[(0,False),(1,False),(1.01,True),(12,True)])
def test_exact_official_warning_preserves_quota_boundaries(newline,value,allowed):
    calls=[]
    def client(args,timeout):
        calls.append(args)
        return WARNING+newline+json.dumps([{'resource':'GPU','remaining':f'{value}h'}])
    result=query_client(client,now=1000)
    assert result['allowed'] is allowed
    assert result['state']==('available' if allowed else 'quota_reserved')
    assert result['remaining_hours']==value
    assert result['checked_at']==1000
    assert calls==[['quota','--format','json']]


@pytest.mark.parametrize('prefix',[
    WARNING,
    WARNING+'\n'+WARNING+'\n',
    '\n'+WARNING+'\n',
    ' '+WARNING+'\n',
    'unrelated warning\n',
    WARNING+'\nNext Page Token = synthetic-token\n',
    WARNING.replace('Warning:','WARNING:')+'\n',
    '\x1b[33m'+WARNING+'\n',
    '\ufeff'+WARNING+'\n',
])
def test_quota_accepts_only_one_complete_official_first_line(prefix):
    payload=prefix+'[{"resource":"GPU","remaining":"12h"}]'
    result=query_client(lambda *args:payload,now=1000)
    assert result['state']=='quota_unknown' and result['allowed'] is False
    assert result['remaining_hours'] is None
    assert 'synthetic-token' not in json.dumps(result)


@pytest.mark.parametrize('payload',[
    '', 'not json', '{}', '[]',
    '[{"resource":"TPU","remaining":"12h"}]',
    '[{"resource":"GPU","remaining":"12h"},{"resource":"GPU","remaining":"0h"}]',
    '[{"resource":"GPU","remaining":"NaN"}]',
    '[{"resource":"GPU","remaining":"Infinity"}]',
    '[{"resource":"GPU","remaining":"-1h"}]',
    '[{"resource":"GPU","remaining":true}]',
    '[{"resource":"GPU","remaining":"12h"}]\n'+WARNING+'\n',
])
def test_official_warning_does_not_relax_quota_payload_validation(payload):
    result=query_client(lambda *args:WARNING+'\n'+payload,now=1000)
    assert result['state']=='quota_unknown' and result['allowed'] is False


def test_query_config_accepts_official_warning_with_one_cli_call():
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0,
            stdout=WARNING+'\n[{"resource":"GPU","remaining":"12h"}]')
    result=query_config({'kaggle_python':'/synthetic/python','token_file':'/synthetic/unused'},run)
    assert result['state']=='available' and result['allowed'] is True
    assert calls==[['/synthetic/python','-m','kaggle','quota','--format','json']]
    assert WARNING not in json.dumps(result)


@pytest.mark.parametrize('value,allowed',[(0,False),(0.26,False),(1,False),(1.01,True),(30,True)])
def test_exact_reserve_boundaries(value,allowed):
    calls=[]
    result=query_client(lambda args,timeout:(calls.append(args) or json.dumps([{'resource':'GPU','remaining':f'{value}h'}])),now=1000)
    assert result['allowed'] is allowed
    assert result['remaining_hours']==value
    assert calls==[['quota','--format','json']]


@pytest.mark.parametrize('value',[None,'','NaN','Infinity','-1h','unknown',True,{},[]])
def test_unknown_nonfinite_negative_and_malformed_values_fail_closed(value):
    result=query_client(lambda *args:json.dumps([{'resource':'GPU','remaining':value}]),now=1000)
    assert not result['allowed']
    assert result['state']=='quota_unknown'


@pytest.mark.parametrize('payload',['not json','{}','[]','[{"resource":"TPU","remaining":"30h"}]',
    '[{"resource":"GPU","remaining":"30h"},{"resource":"GPU","remaining":"0h"}]'])
def test_unreliable_payload_never_submits(payload):
    assert not query_client(lambda *args:payload)['allowed']


def test_stale_old_future_and_missing_readings_fail_closed():
    snapshot={'state':'ok','gpu':{'remaining_hours':30},'checked_at':1000}
    assert admission(snapshot,now=1000)['allowed']
    for modified,now in [(snapshot,1301),(snapshot,999),({**snapshot,'state':'stale'},1000),
                         ({**snapshot,'stale':True},1000),({},1000),(None,1000)]:
        assert not admission(modified,now=now)['allowed']


def test_query_errors_do_not_leak_credential_output():
    def bad(*args):
        raise RuntimeError('secret-token-and-signed-url')
    result=query_client(bad)
    assert result['state']=='quota_unknown'
    assert 'secret' not in json.dumps(result)
    captured={}
    def run(args,**kwargs):
        captured.update(args=args,env=kwargs['env'])
        return SimpleNamespace(returncode=0,stdout='[{"resource":"GPU","remaining":"1.01h"}]')
    result=query_config({'kaggle_python':'/isolated/python','token_file':'/private/test-path'},run)
    assert result['allowed']
    assert captured['args']==['/isolated/python','-m','kaggle','quota','--format','json']
    assert captured['env']['KAGGLE_API_TOKEN']=='/private/test-path'
    assert '/private' not in json.dumps(result)


def lane(state=None,allowed=False,active=False):
    return {'schedule_enabled':True,'active':active,'ready':True,'outstanding':{'state':state} if state else None,
            'quota_gate':{'allowed':allowed}}


def test_planner_recovers_low_quota_without_scheduling_new_or_prepared_jobs():
    keys=('primary','secondary','third','fourth','fifth')
    lanes={'primary':lane(allowed=False),'secondary':lane('prepared'),
           'third':lane('running'),'fourth':lane('downloaded'),'fifth':lane(allowed=True)}
    starts,cursor=plan_lanes(keys,lanes,100,0,5,20)
    assert starts==['third','fourth','fifth']
    assert cursor==0
    for state in ('submitting','submit_unknown','submitted','running','terminal','downloaded'):
        assert may_start(lane(state))
    assert not may_start(lane('prepared'))
    assert not may_start({'outstanding':None})
    lanes['primary']['quota_gate']['allowed']=True
    assert 'primary' in plan_lanes(keys,lanes,100,0,5,20)[0]


def controller(tmp_path,remaining='1h'):
    calls=[]
    quota=[remaining]
    def client(args,timeout):
        calls.append(args)
        if args[0]=='quota':
            return json.dumps([{'resource':'GPU','remaining':quota[0]}])
        if args[:2]==['kernels','push']:
            from pathlib import Path
            metadata=json.loads((Path(args[args.index('-p')+1])/'kernel-metadata.json').read_text())
            return f"Kernel version 1 successfully pushed.  Please check progress at https://www.kaggle.com/code/{metadata['id']}"
        if args[:2]==['kernels','status']:
            return 'has status "KernelWorkerStatus.COMPLETE"'
        if args[:2]==['kernels','output']:
            from pathlib import Path
            folder=Path(args[-1])
            manifest=json.loads((folder.parent/'manifest.json').read_text())
            (folder/'results.jsonl').write_text(json.dumps({'batch_id':folder.parent.name,
                'manifest_hash':manifest['manifest_hash'],'id':'a','input_hash':'test','status':'ok','content':'{}'})+'\n')
            return ''
        raise AssertionError(args)
    control=Controller(tmp_path,'testowner',client, initialize=True)
    manifest={'runtime_source':'owner/runtime','session_timeout':600,
              'items':[{'id':'a','input_hash':'test','source_refs':[{'entry_id':1}],'messages':[{'role':'user','content':'fixture'}]}]}
    batch=control.prepare(manifest,'MANIFEST = None\n')
    return control,batch,quota,calls


@pytest.mark.parametrize('remaining',['0h','0.26h','1.00h','unknown'])
def test_submit_itself_checks_fresh_quota_and_preserves_prepared_state(tmp_path,remaining):
    control,batch,quota,calls=controller(tmp_path,remaining)
    result=control.submit(batch)
    assert result['submission_blocked']
    assert control.row(batch)['state']=='prepared'
    assert not any(c[:2]==['kernels','push'] for c in calls)
    quota[0]='1.01h'
    assert control.submit(batch)['state']=='submitted'
    assert sum(c[:2]==['kernels','push'] for c in calls)==1
    quota[0]='0h'
    assert control.submit(batch)['state']=='submitted'
    assert control.status(batch)['state']=='terminal'
    assert sum(c[0]=='quota' for c in calls)==2


def test_low_quota_download_and_import_do_not_require_quota(tmp_path):
    control,batch,_quota,calls=controller(tmp_path,'unknown')
    control._set(batch,'running')
    # Existing output is downloaded/verified with unreadable quota, never resubmitted.
    result=control.download(batch)
    assert result['missing_ids']==[]
    assert len(result['results'])==1
    assert not any(c[0]=='quota' or c[:2]==['kernels','push'] for c in calls)


def test_drain_finishes_existing_batch_then_stops_before_new_prepare(tmp_path):
    calls=[]
    states={'b':'running'}
    control=SimpleNamespace(root=tmp_path,row=lambda b:{'state':states[b]})
    def bridge(path,action,*args,**kwargs):
        calls.append(action)
        if action=='advance':
            states['b']='imported'
            return {'import_states':{'imported':1}}
        if calls.count('prepare')==1:
            return {'existing_batch':'b'}
        return {'state':'quota_reserved','quota_gate':{'allowed':False,'state':'quota_reserved'},'gpu_started':False}
    result=drain('config',{'schedule_enabled':True,'batch_limit':20,'drain_queue':True,'cycle_timeout_seconds':60},control,
                 call=bridge,sleep=lambda _:None,clock=lambda:0)
    assert calls==['prepare','advance','prepare']
    assert result['state']=='quota_reserved' and result['completed_batches']==1


def test_drain_returns_when_quota_drops_after_preparation(tmp_path):
    control=SimpleNamespace(root=tmp_path,row=lambda b:{'state':'prepared'})
    calls=[]
    def bridge(path,action,*args,**kwargs):
        calls.append(action)
        return ({'batch_id':'b'} if action=='prepare' else
                {'submission_blocked':True,'quota_gate':{'allowed':False,'state':'quota_unknown'}})
    result=drain('config',{'schedule_enabled':True,'batch_limit':20,'drain_queue':True,'cycle_timeout_seconds':60},control,
                 call=bridge,sleep=lambda _:None,clock=lambda:0)
    assert calls==['prepare','advance']
    assert result['state']=='quota_unknown' and result['gpu_started'] is False


@pytest.mark.parametrize('outstanding',[False,True])
def test_bridge_prepare_blocks_before_backup_but_returns_existing_recovery(tmp_path,monkeypatch,capsys,outstanding):
    import sys
    from pathlib import Path

    import cloud_bridge

    import initialize_secrets

    control,batch,_quota,calls=controller(tmp_path/'state','0.26h')
    control._set(batch,'running' if outstanding else 'imported')
    config=tmp_path/'config.json'
    # No versions/database fields: blocked new work must return before touching either.
    config.write_text(json.dumps({'schedule_enabled':True,'source':str(Path(initialize_secrets.__file__).parent),
        'token_file':'/synthetic/unused-token-file','state_root':str(control.root),
        'owner':'testowner','kaggle_python':'/synthetic/python'}))
    monkeypatch.setattr(sys,'argv',['cloud_bridge','--config',str(config),'prepare'])
    monkeypatch.setenv('KAGGLE_API_TOKEN','synthetic-original')
    monkeypatch.setattr(initialize_secrets,'read_env',lambda name:{})
    monkeypatch.setattr(cloud_bridge,'Controller',lambda *args,**kwargs:control)
    cloud_bridge.main()
    result=json.loads(capsys.readouterr().out)
    if outstanding:
        assert result=={'existing_batch':batch}
        assert calls==[]
    else:
        assert result['state']=='quota_reserved'
        assert result['gpu_started'] is False
        assert calls==[['quota','--format','json']]
