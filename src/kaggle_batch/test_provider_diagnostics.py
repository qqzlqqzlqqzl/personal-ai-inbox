"""18 handoff cases: synthetic CLI results, temporary ledgers, zero provider I/O."""
import json
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import batch_control
from batch_control import Controller
from provider_diagnostics import failure, project, private_observer, safe_event
from recovery_policy import ProviderError, backoff, classify_failure
from queue_dispatch import claimed_entries
from quota_guard import query_client
from test_required_ledgers import manifest


@pytest.fixture(autouse=True)
def forbid_external(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError('external socket/process forbidden in diagnostics tests')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)


def wrapper(status):
    kind = 'Server' if status >= 500 else 'Client'
    return f'{status} {kind} Error: synthetic for url: https://example.invalid/private?token=CANARY'


def controller(tmp_path, observer=None):
    return Controller(tmp_path/'ledger', 'fixture', initialize=True,
                      diagnostic_observer=observer or private_observer(tmp_path/'audit'))


def invoke(control, monkeypatch, output, args=None, error=None):
    run = Mock(side_effect=error) if error else Mock(return_value=SimpleNamespace(
        returncode=1, stderr=output, stdout=''))
    monkeypatch.setattr(batch_control.subprocess, 'run', run)
    with pytest.raises(ProviderError) as caught:
        control._cli(args or ['kernels', 'status', 'fixture/private'], 20)
    assert run.call_count == 1
    return caught.value


def events(tmp_path):
    return [json.loads(line) for path in (tmp_path/'audit').glob('events-*.jsonl')
            for line in path.read_text().splitlines()]


@pytest.mark.parametrize('status,category,code,cooldown', [
    (401,'authentication_rejected','authentication',86400),
    (403,'http_forbidden','inaccessible',86400),
    (404,'http_not_found','inaccessible',86400),
    (429,'rate_limited','rate_limited',660),
    (503,'http_5xx','provider_unavailable',660)])
def test_cases_01_to_05_recognized_wrappers(tmp_path,monkeypatch,status,category,code,cooldown):
    c=controller(tmp_path);error=invoke(c,monkeypatch,wrapper(status))
    assert error.code==str(error)==code and backoff(code,1)==cooldown
    assert c.last_diagnostic_retained is True
    record,=events(tmp_path)
    assert record['diagnostic']=={'http_status':status,'operation':'kernels.status',
                                  'error_class':'cli_nonzero','category':category}


@pytest.mark.parametrize('text,legacy', [('arbitrary rejection','provider_unavailable'),
    ('article 404 account private','inaccessible')])
def test_cases_05_06_unknown_and_incidental_numbers(tmp_path,monkeypatch,text,legacy):
    c=controller(tmp_path);assert invoke(c,monkeypatch,text).code==legacy
    d=events(tmp_path)[0]['diagnostic']
    assert d['http_status'] is None and d['category']=='unclassified'


@pytest.mark.parametrize('text,category', [
    (wrapper(403)+'\n'+wrapper(404),'ambiguous_http_status'),
    ('True Client Error: no for url: https://example.invalid','unclassified'),
    ('600 Client Error: no for url: https://example.invalid','unclassified'),
    ('503 Client Error: no for url: https://example.invalid','unclassified'),
    ('{"status":403}', 'unclassified'), ('HTTP 403 unknown wrapper','unclassified')])
def test_case_07_ambiguous_and_malformed(text,category):
    result=failure(['kernels','status'],output=text)
    assert result['http_status'] is None and result['category']==category
    assert project({'http_status':True})['http_status'] is None


@pytest.mark.parametrize('error,error_class',[
    (subprocess.TimeoutExpired('CANARY',20,output='CANARY'), 'TimeoutExpired'),
    (FileNotFoundError('CANARY'),'FileNotFoundError'),
    (PermissionError('CANARY'),'PermissionError'),(OSError('CANARY'),'OSError')])
def test_case_08_exception_classes(tmp_path,monkeypatch,error,error_class):
    c=controller(tmp_path);assert invoke(c,monkeypatch,'',error=error).code=='network'
    d=events(tmp_path)[0]['diagnostic'];assert d['http_status'] is None and d['error_class']==error_class
    assert 'CANARY' not in json.dumps(events(tmp_path))


@pytest.mark.parametrize('args,expected', [
    (['kernels','status','CANARY/slug','/token/path'],'kernels.status'),
    (['kernels','list','--mine','CANARY'],'kernels.list'),
    (['kernels','output','CANARY'],'kernels.output'),
    (['kernels','push','-p','CANARY'],'kernels.push'),
    (['quota','CANARY'],'quota'),(['CANARY','status'],'unknown'),([], 'unknown')])
def test_case_09_operation_is_closed(args,expected):
    d=failure(args,output='CANARY')
    assert d['operation']==expected and 'CANARY' not in json.dumps(d)


def test_cases_10_11_no_canary_and_bounded_payload(tmp_path,monkeypatch,capsys):
    secrets=['Basic CANARY_BASIC','Bearer CANARY_BEARER','Cookie: CANARY_COOKIE',
             '{"token":"CANARY_JSON"}', 'https://CANARY_USER:pwd@example.invalid/CANARY_PATH?q=CANARY_QUERY',
             'CANARY_EMAIL@example.invalid', 'CANARY_ARTICLE', 'locals={CANARY_STACK}']
    for text in ['\n'.join(secrets), '秘密\x00\n'*30000, wrapper(403)+'\n'+'x'*65536]:
        c=controller(tmp_path);error=invoke(c,monkeypatch,text)
        assert str(error)==classify_failure(text+'\n').code
    for record in events(tmp_path):
        encoded=json.dumps(record['diagnostic'],separators=(',',':')).encode()
        assert len(encoded)<=256 and b'CANARY' not in encoded
        assert set(record)=={'at','event','diagnostic'}
    assert capsys.readouterr().out==''
    d=project({'http_status':'403','operation':[], 'error_class':{},'category':'CANARY','CANARY':'secret'})
    assert d=={'http_status':None,'operation':'unknown','error_class':'unknown','category':'unclassified'}


def test_case_12_submit_failure_preserves_claims_and_no_repush(tmp_path,monkeypatch):
    c=controller(tmp_path);key=c.prepare(manifest(1),'MANIFEST = None\n');calls=[]
    def run(argv,**kwargs):
        args=argv[3:];calls.append(args)
        if args[0]=='quota':return SimpleNamespace(returncode=0,stdout='[{"resource":"GPU","remaining":"20h"}]',stderr='')
        assert args[:2]==['kernels','push']
        return SimpleNamespace(returncode=1,stdout='',stderr=wrapper(503))
    monkeypatch.setattr(batch_control.subprocess,'run',run)
    with pytest.raises(ProviderError,match='^provider_unavailable$'):c.submit(key)
    assert len(calls)==2 and sum(a[:2]==['kernels','push'] for a in calls)==1
    assert c.row(key)['state']=='submit_unknown' and claimed_entries([c.root])=={1}
    assert len(events(tmp_path))==1 and events(tmp_path)[0]['diagnostic']['operation']=='kernels.push'
    assert not (c.root/'recovery.json').exists()


def test_case_13_two_attempts_two_events_failed_proof_preserves_job(tmp_path,monkeypatch):
    c=controller(tmp_path);key=c.prepare(manifest(1),'MANIFEST = None\n');c._set(key,'submit_unknown')
    with c.db() as db:db.execute('UPDATE batches SET updated=0 WHERE id=?',(key,))
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv[3:]);return SimpleNamespace(returncode=1,stdout='',stderr=wrapper(404))
    monkeypatch.setattr(batch_control.subprocess,'run',run)
    with pytest.raises(ProviderError,match='^inaccessible$'):c.status(key)
    assert [a[:2] for a in calls]==[['kernels','status'],['kernels','list']]
    assert [e['diagnostic']['operation'] for e in events(tmp_path)]==['kernels.status','kernels.list']
    assert c.row(key)['state']=='submit_unknown' and claimed_entries([c.root])=={1}


def test_case_14_terminal_output_observed_before_local_deferral(tmp_path,monkeypatch):
    c=controller(tmp_path);key=c.prepare(manifest(1),'MANIFEST = None\n');c._set(key,'terminal',remote='COMPLETE')
    run=Mock(return_value=SimpleNamespace(returncode=1,stdout='',stderr=wrapper(503)))
    monkeypatch.setattr(batch_control.subprocess,'run',run)
    with pytest.raises(ProviderError) as caught:c.download(key)
    result=c.defer_local(key,caught.value.code)
    assert result['state']=='local_retry_scheduled' and result['gpu_resubmitted'] is False
    assert result['failures']==1 and run.call_count==1 and claimed_entries([c.root])=={1}
    assert events(tmp_path)[0]['diagnostic']['operation']=='kernels.output'


def test_case_14_real_bridge_sink_wiring_and_defer_path(tmp_path,monkeypatch,capsys):
    import cloud_bridge
    import sys
    from test_required_ledgers import bridge_config
    c=controller(tmp_path);key=c.prepare(manifest(1),'MANIFEST = None\n');c._set(key,'terminal',remote='COMPLETE')
    path,cfg=bridge_config(tmp_path,c.root,[])
    cfg.update(schedule_enabled=True,exception_audit_root=str(tmp_path/'audit'))
    path.write_text(json.dumps(cfg))
    run=Mock(return_value=SimpleNamespace(returncode=1,stdout='',stderr=wrapper(503)))
    monkeypatch.setattr(batch_control.subprocess,'run',run)
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=lambda *a:{}))
    monkeypatch.setattr(sys,'argv',['cloud_bridge','--config',str(path),'advance','--batch',key])
    cloud_bridge.main()
    result=json.loads(capsys.readouterr().out)
    assert result['state']=='local_retry_scheduled' and result['gpu_resubmitted'] is False
    assert run.call_count==1 and len(events(tmp_path))==1
    assert events(tmp_path)[0]['event']=='provider_call_failure'


def test_case_15_quota_failure_stays_fail_closed(tmp_path,monkeypatch):
    c=controller(tmp_path);monkeypatch.setattr(batch_control.subprocess,'run',Mock(
        return_value=SimpleNamespace(returncode=1,stdout='',stderr=wrapper(403))))
    result=query_client(c.client,now=100)
    assert result['allowed'] is False and result['state']=='quota_unknown'
    assert events(tmp_path)[0]['diagnostic']['operation']=='quota'


@pytest.mark.parametrize('observer',[Mock(side_effect=OSError('private disk full')),lambda d:False])
def test_case_16_sink_failure_does_not_change_failure(tmp_path,monkeypatch,observer):
    c=controller(tmp_path,observer);error=invoke(c,monkeypatch,wrapper(404))
    assert error.code=='inaccessible' and c.last_diagnostic_retained is False
    assert events(tmp_path)==[]


def test_case_17_legacy_classification_and_backoff_parity(tmp_path,monkeypatch):
    samples=[wrapper(x) for x in [401,403,404,429,503]]+['timeout','unknown','article 404']
    baseline=[];observed=[]
    for sink,results in [(lambda d:False,baseline),(private_observer(tmp_path/'audit'),observed)]:
        c=controller(tmp_path,sink)
        for text in samples:
            error=invoke(c,monkeypatch,text);results.append((error.code,str(error),[backoff(error.code,n) for n in [0,1,2,9]]))
    assert baseline==observed
    for code in ProviderError.CODES:
        assert str(ProviderError(code))==code


def test_case_18_safe_export_projects_only_fixed_schema(tmp_path,monkeypatch):
    c=controller(tmp_path);invoke(c,monkeypatch,wrapper(403));record=events(tmp_path)[0]
    record.update(owner='CANARY_OWNER',batch_id='CANARY_BATCH',lane='primary',traceback='CANARY_STACK')
    record['diagnostic']['raw']='CANARY_RAW'
    export=safe_event(record)
    assert set(export)=={'event','at','lane','diagnostic'} and 'CANARY' not in json.dumps(export)
    assert safe_event({'event':'bridge_failure','at':1}) is None
    for path in (tmp_path/'audit').glob('events-*.jsonl'):
        assert path.stat().st_mode & 0o777==0o600


def test_stop_before_diagnostic_write_preserves_original_exception(tmp_path,monkeypatch):
    from dispatch_policy import DispatchStopped
    def stopped():raise DispatchStopped('schedule_disabled')
    c=controller(tmp_path,private_observer(tmp_path/'audit',authorize=stopped))
    assert invoke(c,monkeypatch,wrapper(403)).code=='inaccessible'
    assert c.last_diagnostic_retained is False and not (tmp_path/'audit').exists()
