"""Stopped UI quota polling is cache-only, with synthetic config/state."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import month_control
from kaggle_batch.recovery_watchdog import run_once


def setup(tmp_path,monkeypatch,flag=False):
    monkeypatch.setattr(month_control,'ROOT',tmp_path)
    stage=tmp_path/'runtime/qwen-month-20260925';stage.mkdir(parents=True)
    monkeypatch.setattr(month_control,'STAGE',stage)
    folder=tmp_path/'src/kaggle_batch';folder.mkdir(parents=True)
    for key in month_control.KEYS:
        (folder/f'cloud-config-month-{key}.json').write_text(json.dumps({'schedule_enabled':flag,
            'kaggle_python':'never','token_file':'synthetic-secret-path','queue_scope':'allowlist',
            'entry_allowlist':str(stage/'allowlist.json')}))
    return stage,folder


@pytest.mark.parametrize('flag',[False,None,0,1,'true'])
@pytest.mark.parametrize('cache_present',[False,True])
def test_disabled_expired_or_missing_cache_never_queries_or_writes(tmp_path,monkeypatch,flag,cache_present):
    stage,folder=setup(tmp_path,monkeypatch,flag)
    cache=tmp_path/'state/kaggle-month-dispatch/quota-status.json'
    if cache_present:
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({'checked_at':1,'lanes':{k:{'state':'ok','gpu':{'remaining_hours':20}} for k in month_control.KEYS}}))
    before=cache.read_bytes() if cache_present else None
    provider=Mock(side_effect=AssertionError('no provider on disabled cache polling'))
    monkeypatch.setattr(month_control,'_quota_for_lane',provider)
    report=month_control.quota_status(now=10000,ttl=300)
    assert report['refresh_disabled'] is True
    assert (cache.read_bytes() if cache.exists() else None)==before
    provider.assert_not_called()


@pytest.mark.parametrize('fault',['missing','malformed','paused'])
def test_unavailable_or_paused_configuration_never_refreshes_quota(tmp_path,monkeypatch,fault):
    stage,folder=setup(tmp_path,monkeypatch,True)
    if fault=='missing':(folder/'cloud-config-month-third.json').unlink()
    if fault=='malformed':(folder/'cloud-config-month-third.json').write_text('{private-secret')
    if fault=='paused':(stage/'paused.json').write_text('{}')
    provider=Mock();monkeypatch.setattr(month_control,'_quota_for_lane',provider)
    assert month_control.quota_status(now=1000)['refresh_disabled']
    assert month_control.effective_enabled_lanes()==[];provider.assert_not_called()


def test_enabled_mixed_ui_refresh_queries_only_enabled_lane(tmp_path,monkeypatch):
    stage,folder=setup(tmp_path,monkeypatch,False)
    p=folder/'cloud-config-month-secondary.json';cfg=json.loads(p.read_text());cfg['schedule_enabled']=True;p.write_text(json.dumps(cfg))
    provider=Mock(return_value={'state':'ok','gpu':{'remaining_hours':20}})
    monkeypatch.setattr(month_control,'_quota_for_lane',provider)
    assert month_control.quota_status(now=1000)['lanes']['secondary']['state']=='ok'
    provider.assert_called_once_with('secondary')


@pytest.mark.parametrize('field,value',[
    ('schedule_enabled',False),('schedule_enabled',1),('schedule_enabled',1.0),
    ('reconcile_only',0),('reconcile_only',0.0)])
def test_quota_revalidates_typed_change_before_subprocess(tmp_path,monkeypatch,field,value):
    stage,folder=setup(tmp_path,monkeypatch,True)
    p=folder/'cloud-config-month-primary.json'
    original=json.loads(p.read_text());original['reconcile_only']=False;p.write_text(json.dumps(original))
    def flags():
        cfg=json.loads(p.read_text());cfg[field]=value;p.write_text(json.dumps(cfg))
        # Reproduce a stale pre-seam admission result, without external calls.
        return ['primary']
    monkeypatch.setattr(month_control,'effective_enabled_lanes',flags)
    process=Mock(side_effect=AssertionError('typed config changed before provider'))
    monkeypatch.setattr(month_control.subprocess,'run',process)
    assert month_control._quota_for_lane('primary')=={'state':'error','error':'configuration_changed'}
    process.assert_not_called()


def test_quota_revalidates_pause_after_enabled_snapshot(tmp_path,monkeypatch):
    stage,folder=setup(tmp_path,monkeypatch,True)
    def flags():
        (stage/'paused.json').write_text('{}');return ['primary']
    monkeypatch.setattr(month_control,'effective_enabled_lanes',flags)
    process=Mock(side_effect=AssertionError('pause appeared before provider'))
    monkeypatch.setattr(month_control.subprocess,'run',process)
    assert month_control._quota_for_lane('primary')=={'state':'error','error':'paused'}
    process.assert_not_called()


def test_status_truthfully_disabled_and_expired_cache_has_zero_kaggle_calls(tmp_path,monkeypatch):
    stage,folder=setup(tmp_path,monkeypatch,False)
    (stage/'scope.json').write_text('{"from":null,"to":null,"articles":1,"unknown_date_excluded":0}')
    (stage/'allowlist.json').write_text('{"entry_ids":[1]}')
    state=tmp_path/'state';state.mkdir()
    with sqlite3.connect(state/'analysis.sqlite3') as db:
        db.execute('CREATE TABLE analyses(entry_id INTEGER,state TEXT)');db.execute("INSERT INTO analyses VALUES(1,'done')")
        db.execute('CREATE TABLE card_translations(entry_id INTEGER,status TEXT)')
    cache=state/'kaggle-month-dispatch/quota-status.json';cache.parent.mkdir()
    cache.write_text(json.dumps({'checked_at':1,'lanes':{k:{'state':'ok','gpu':{'remaining_hours':20}} for k in month_control.KEYS}}))
    before=cache.read_bytes()
    provider=Mock(side_effect=AssertionError('no Kaggle UI refresh'));monkeypatch.setattr(month_control,'_quota_for_lane',provider)
    def systemctl(args,**kwargs):
        assert args[:3]==['systemctl','--user','show'];return SimpleNamespace(stdout='')
    monkeypatch.setattr(month_control.subprocess,'run',systemctl)
    report=month_control.status()
    assert report['enabled'] is False
    assert all(v['quota_gate']=={'allowed':False,'state':'schedule_disabled'} for v in report['lanes'].values())
    assert cache.read_bytes()==before;provider.assert_not_called()


def test_resume_does_not_implicitly_reenable_schedule_flags(tmp_path,monkeypatch):
    stage,folder=setup(tmp_path,monkeypatch,False);(stage/'paused.json').write_text('{}')
    before={p:p.read_bytes() for p in folder.iterdir()}
    monkeypatch.setattr(month_control,'authenticate',lambda *a:None)
    monkeypatch.setattr('kaggle_batch.lane_scheduler.tick',lambda:{'state':'schedule_disabled','started':[]})
    report=month_control.control('resume','synthetic')
    assert report['scheduler']['state']=='schedule_disabled'
    assert before=={p:p.read_bytes() for p in folder.iterdir()}
    assert month_control.effective_enabled_lanes()==[]


def test_watchdog_stopped_dispatch_retains_separate_pre_tick_retention_policy():
    calls=[]
    def prune():calls.append('retention');return {'state':'ok'}
    def tick():calls.append('dispatch');return {'state':'schedule_disabled','started':[]}
    report,code=run_once(tick,prune)
    assert calls==['retention','dispatch'] and code==0
    assert report['state']=='schedule_disabled' and report['snapshot_retention']=={'state':'ok'}


def test_new_month_lane_template_is_automatic_and_scoped():
    root=Path(__file__).resolve().parents[1]
    unit=(root/'deploy/systemd/ai-news-kaggle-month@.service').read_text()
    assert '--manual' not in unit
    assert 'cloud-config-month-%i.json' in unit
    assert 'TimeoutStartSec=20400' in unit
