import json
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import month_control


def test_nonfinite_quota_cannot_break_dashboard_serialization():
    for value in ('NaN','Infinity','-1h',True,None):
        parsed=month_control._parse_quota_rows([{'resource':'GPU','remaining':value}])
        assert parsed['gpu']['remaining_hours'] is None
        json.dumps(parsed,allow_nan=False)
    assert month_control._hours(0)==0


def test_parse_quota_rows_normalizes_hours():
    parsed = month_control._parse_quota_rows([
        {"resource": "GPU", "used": "29.70h", "remaining": "0.30h", "total": "30.00h", "refreshAt": "2026-10-03T00:00:00"},
        {"resource": "TPU", "used": "1.25h", "remaining": "18.75h", "total": "20.00h", "refreshAt": "2026-10-03T00:00:00"},
    ])
    assert parsed["gpu"] == {
        "used_hours": 29.7,
        "remaining_hours": 0.3,
        "total_hours": 30.0,
        "refresh_at": "2026-10-03T00:00:00",
    }
    assert parsed["tpu"]["remaining_hours"] == 18.75


def test_quota_for_lane_uses_isolated_token_file_without_returning_it(tmp_path, monkeypatch):
    root = tmp_path
    config_dir = root / "src/kaggle_batch"
    config_dir.mkdir(parents=True)
    token = root / ".private/account-token"
    token.parent.mkdir()
    token.write_text("secret")
    (config_dir / "cloud-config-month-primary.json").write_text(json.dumps({
        "schedule_enabled": True,
        "kaggle_python": "/opt/kaggle-python",
        "token_file": str(token),
    }))
    monkeypatch.setattr(month_control,"KEYS",("primary",))
    monkeypatch.setattr(month_control,"STAGE",tmp_path/"stage")
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout=json.dumps([
            {"resource": "GPU", "used": "12.00h", "remaining": "18.00h", "total": "30.00h", "refreshAt": "2026-10-03T00:00:00"},
        ]))

    monkeypatch.setattr(month_control, "ROOT", root)
    monkeypatch.setattr(month_control.subprocess, "run", fake_run)
    result = month_control._quota_for_lane("primary")
    assert captured["args"] == ["/opt/kaggle-python", "-m", "kaggle", "quota", "--format", "json"]
    assert captured["env"]["KAGGLE_API_TOKEN"] == str(token)
    assert result["gpu"]["remaining_hours"] == 18.0
    assert str(token) not in json.dumps(result)


def test_quota_status_caches_all_lanes(tmp_path, monkeypatch):
    monkeypatch.setattr(month_control, "ROOT", tmp_path)
    monkeypatch.setattr(month_control, "KEYS", ("primary", "secondary"))
    config_dir=tmp_path/"src/kaggle_batch";config_dir.mkdir(parents=True)
    for key in month_control.KEYS:
        (config_dir/f"cloud-config-month-{key}.json").write_text(json.dumps({"schedule_enabled":True}))
    monkeypatch.setattr(month_control,"STAGE",tmp_path/"stage")
    calls = []
    def fake_lane(key):
        calls.append(key)
        return {"state": "ok", "gpu": {
            "used_hours": 10.0,
            "remaining_hours": 20.0,
            "total_hours": 30.0,
            "refresh_at": "2026-10-03T00:00:00",
        }}

    monkeypatch.setattr(month_control, "_quota_for_lane", fake_lane)
    first = month_control.quota_status(now=1000, ttl=300)
    second = month_control.quota_status(now=1100, ttl=300)
    assert set(first["lanes"]) == {"primary", "secondary"}
    assert second == first
    assert sorted(calls) == ["primary", "secondary"]
    cache = tmp_path / "state/kaggle-month-dispatch/quota-status.json"
    assert cache.exists()


def test_quota_status_preserves_last_good_value_on_refresh_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(month_control, "ROOT", tmp_path)
    monkeypatch.setattr(month_control, "KEYS", ("primary",))
    config_dir=tmp_path/"src/kaggle_batch";config_dir.mkdir(parents=True)
    (config_dir/"cloud-config-month-primary.json").write_text(json.dumps({"schedule_enabled":True}))
    monkeypatch.setattr(month_control,"STAGE",tmp_path/"stage")
    monkeypatch.setattr(month_control, "_quota_for_lane", lambda key: {
        "state": "ok", "gpu": {"used_hours": 5.0, "remaining_hours": 25.0, "total_hours": 30.0, "refresh_at": "2026-10-03T00:00:00"}
    })
    month_control.quota_status(now=1000, ttl=10)
    monkeypatch.setattr(month_control, "_quota_for_lane", lambda key: {"state": "error", "error": "quota_unavailable"})
    value = month_control.quota_status(now=1020, ttl=10)
    assert value["lanes"]["primary"]["state"] == "stale"
    assert value["lanes"]["primary"]["gpu"]["remaining_hours"] == 25.0


def status_fixture(tmp_path, monkeypatch, *, active=True, ledger=True):
    stage=tmp_path/'runtime/qwen-month-20260925';stage.mkdir(parents=True)
    (stage/'scope.json').write_text(json.dumps({'from':None,'to':None,'articles':3,'unknown_date_excluded':0}))
    (stage/'allowlist.json').write_text(json.dumps({'entry_ids':[1,2,3]}))
    configs=tmp_path/'src/kaggle_batch';configs.mkdir(parents=True)
    (configs/'cloud-config-month-primary.json').write_text(json.dumps({'queue_scope':'allowlist'}))
    state=tmp_path/'state';state.mkdir()
    with sqlite3.connect(state/'analysis.sqlite3') as db:
        db.execute('CREATE TABLE analyses(entry_id INTEGER,state TEXT)')
        db.execute('CREATE TABLE card_translations(entry_id INTEGER,status TEXT)')
    lane=state/'kaggle-month-primary';lane.mkdir()
    (lane/'cycle-status.json').write_text(json.dumps({'state':'empty','outstanding':{'id':'stale','state':'running','remote_status':'RUNNING'}}))
    if ledger:
        with sqlite3.connect(lane/'batches.sqlite3') as db:
            db.execute('CREATE TABLE batches(id TEXT,state TEXT,remote_status TEXT,error TEXT,updated REAL)')
            db.execute('CREATE TABLE batch_claims(batch_id TEXT,entry_id INTEGER)')
            db.execute("INSERT INTO batches VALUES('old','quarantined',NULL,'unknown',1)")
            db.executemany('INSERT INTO batch_claims VALUES(?,?)',[('old',1),('old',2)])
            if active:db.execute("INSERT INTO batches VALUES('new','running','RUNNING',NULL,2)")
    monkeypatch.setattr(month_control,'ROOT',tmp_path)
    monkeypatch.setattr(month_control,'STAGE',stage)
    monkeypatch.setattr(month_control,'KEYS',('primary',))
    monkeypatch.setattr(month_control,'effective_enabled_lanes',lambda:[])
    monkeypatch.setattr(month_control,'quota_status',lambda:{'checked_at':100,'lanes':{}})
    def local_service(args,**kwargs):
        assert args[:3]==['systemctl','--user','show']
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(month_control.subprocess,'run',local_service)
    return lane


def test_status_separates_quarantined_claims_from_new_outstanding_without_mutation(tmp_path, monkeypatch):
    lane=status_fixture(tmp_path,monkeypatch)
    files=list(tmp_path.rglob('*.json'))+list(tmp_path.rglob('*.sqlite3'))
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    value=month_control.status()['lanes']['primary']
    assert value['outstanding']['id']=='new'
    assert value['quarantine']=={'batches':1,'claims':2}
    assert value['ledger_state']=='ok'
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with sqlite3.connect((lane/'batches.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute("SELECT COUNT(*) FROM batch_claims WHERE batch_id='old'").fetchone()[0]==2
        assert db.execute("SELECT state FROM batches WHERE id='old'").fetchone()[0]=='quarantined'


def test_status_null_outstanding_does_not_erase_quarantine_or_reuse_cached_row(tmp_path, monkeypatch):
    status_fixture(tmp_path,monkeypatch,active=False)
    value=month_control.status()['lanes']['primary']
    assert 'outstanding' not in value
    assert value['quarantine']=={'batches':1,'claims':2}
    assert value['ledger_state']=='ok'


def test_missing_ledger_stays_unknown_and_is_never_created(tmp_path, monkeypatch):
    lane=status_fixture(tmp_path,monkeypatch,ledger=False)
    value=month_control.status()['lanes']['primary']
    assert value['ledger_state']=='unknown'
    assert value['quarantine']=={'batches':None,'claims':None}
    assert 'outstanding' not in value
    assert not (lane/'batches.sqlite3').exists()
