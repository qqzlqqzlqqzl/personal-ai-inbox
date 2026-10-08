import hashlib
import json
import sqlite3
import ast
from pathlib import Path
import sys
import types
from unittest.mock import patch

from processing_status import LANES, observe, for_entry


def test_missing_observation_does_not_create_state_or_claim_health(tmp_path):
    root=tmp_path/'missing-root'
    snapshot=observe(root,[1],now=100)
    assert not root.exists()
    assert snapshot['submission_unknown_count'] is None
    status=for_entry({'entry_id':1,'state':'pending'},snapshot,now=100)
    assert status['reason_code']=='unknown'
    assert 'ledger_unavailable' in status['reason_codes']


def test_cache_observation_preserves_databases_and_does_not_call_providers(tmp_path):
    config_dir=tmp_path/'src/kaggle_batch'
    config_dir.mkdir(parents=True)
    files=[]
    for index,lane in enumerate(LANES,1):
        path=config_dir/f'cloud-config-month-{lane}.json'
        path.write_text(json.dumps({'schedule_enabled':False,'reconcile_only':False,
                                    'token_file':'SYNTHETIC_CONFIG_FIELD_NOT_FOR_OUTPUT'}))
        db_path=tmp_path/f'state/kaggle-month-{lane}/batches.sqlite3'
        db_path.parent.mkdir(parents=True)
        with sqlite3.connect(db_path) as db:
            db.executescript('CREATE TABLE batches(id TEXT PRIMARY KEY,state TEXT,error TEXT); CREATE TABLE batch_claims(batch_id TEXT,entry_id INTEGER);')
            db.execute('INSERT INTO batches(id,state) VALUES(?,?)',(f'batch-{index}','submit_unknown'))
            db.execute('INSERT INTO batch_claims VALUES(?,?)',(f'batch-{index}',index))
        files.append(db_path)
    before={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    with patch('subprocess.run',side_effect=AssertionError('No provider or service command')),patch('socket.socket',side_effect=AssertionError('No network')):
        snapshot=observe(tmp_path,[1],now=100)
    assert snapshot['submission_unknown_count']==5
    assert snapshot['entry_claim_unknown_ids']=={1}
    assert 'SYNTHETIC_CONFIG_FIELD_NOT_FOR_OUTPUT' not in repr(snapshot)
    assert {path:hashlib.sha256(path.read_bytes()).hexdigest() for path in files}==before
    status=for_entry({'entry_id':1,'state':'pending'},snapshot,now=100)
    assert status['reason_codes']==['schedule_disabled','submission_unknown']


def test_reader_uses_header_control_root_with_shared_state(tmp_path, monkeypatch):
    runtime=tmp_path/'runtime-root'
    source=tmp_path/'source-root'
    (runtime/'state/kaggle-month-dispatch').mkdir(parents=True)
    source.mkdir()
    (source/'state').symlink_to(runtime/'state', target_is_directory=True)
    for root in (runtime, source):
        (root/'src/kaggle_batch').mkdir(parents=True)
        for lane in LANES:
            (root/f'src/kaggle_batch/cloud-config-month-{lane}.json').write_text(json.dumps(
                {'schedule_enabled': root == runtime and lane == 'primary'}))
    for lane in LANES:
        path=runtime/f'state/kaggle-month-{lane}/batches.sqlite3'
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as db:
            db.executescript('CREATE TABLE batches(id TEXT PRIMARY KEY,state TEXT,error TEXT); CREATE TABLE batch_claims(batch_id TEXT,entry_id INTEGER);')
            if lane == 'primary':
                db.executemany('INSERT INTO batches VALUES(?,?,?)', [
                    ('held','quarantined',None), ('unknown','submit_unknown',None),
                    ('conflict','running','submit_cas_conflict')])
                db.executemany('INSERT INTO batch_claims VALUES(?,?)',
                    [('held',i) for i in range(1,72)] + [('unknown',72),('conflict',73)])
    (runtime/'state/kaggle-month-dispatch/scheduler.json').write_text(
        json.dumps({'state':'processing','at':100}))
    paths=[p for p in tmp_path.rglob('*') if p.is_file()]
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    header=types.ModuleType('month_control'); header.ROOT=runtime
    monkeypatch.setitem(sys.modules,'month_control',header)
    source_ast=ast.parse((Path(__file__).resolve().parents[1]/'src/api.py').read_text())
    fn=next(node for node in source_ast.body if isinstance(node,ast.FunctionDef)
            and node.name=='enrich_reader_entries')
    captured=[]
    def decorate(entry, user_id, *, include_source_fallback, processing_evidence, batch):
        captured.append(processing_evidence)
        return {'state':'waiting_model', 'processing':for_entry(
            {'entry_id':entry['id'],'state':'waiting_model'},processing_evidence,now=105)}
    import core
    import prepared_content
    monkeypatch.setattr(core, 'load_reader_batch', lambda *args, **kwargs: None)
    monkeypatch.setattr(prepared_content, 'prepare_many', lambda entries: types.SimpleNamespace(apply=lambda entry: entry))
    namespace={'ROOT':source,'enqueue_cards':lambda *_args,**_kwargs:None,'decorate':decorate,
               'settings':lambda: {}}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<reader-entry-observation>','exec'),namespace)
    with patch('processing_status.time.time',return_value=105), \
            patch('subprocess.run',side_effect=AssertionError('No provider or service command')), \
            patch('socket.socket',side_effect=AssertionError('No network')):
        rows=namespace['enrich_reader_entries']([{'id':i,'user_id':1} for i in (8269,1,72,73)])
    assert captured[0]['configs']['primary']['schedule_enabled'] is True
    assert captured[0]['quarantined_claim_count']==71
    assert rows[0]['state']=='waiting_model'
    assert rows[0]['processing']['reason_code']=='processing'  # Global activity only.
    assert rows[0]['processing']['claim_held'] is False
    assert [row['processing']['reason_code'] for row in rows[1:]]==[
        'submission_quarantined','submission_unknown','submission_unknown']
    assert all(row['processing']['claim_held'] is True for row in rows[1:])
    assert {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}==before


def test_explicit_control_root_does_not_fall_back_to_deployed_config(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    (source/'src/kaggle_batch').mkdir(parents=True)
    for lane in LANES:
        (source/f'src/kaggle_batch/cloud-config-month-{lane}.json').write_text(
            json.dumps({'schedule_enabled':False}))
    missing=tmp_path/'missing-runtime'
    snapshot=observe(source,control_root=missing,now=100)
    assert snapshot['configs']=={}
    assert not missing.exists()
    assert for_entry({'entry_id':8269,'state':'waiting_model'},snapshot,now=100)['reason_code']=='unknown'


def test_pause_uses_explicit_control_root_without_moving_state(tmp_path):
    source=tmp_path/'source'
    runtime=tmp_path/'runtime'
    old_pause=source/'runtime/qwen-month-20260925/paused.json'
    old_pause.parent.mkdir(parents=True); old_pause.write_text('{}')
    snapshot=observe(source,control_root=runtime,now=100)
    assert snapshot['pause_exists'] is False
    assert not runtime.exists()
    live_pause=runtime/'runtime/qwen-month-20260925/paused.json'
    live_pause.parent.mkdir(parents=True); live_pause.write_text('{}')
    snapshot=observe(source,control_root=runtime,now=100)
    assert snapshot['pause_exists'] is True
    assert for_entry({'entry_id':8269,'state':'waiting_model'},snapshot,now=100)['reason_code']=='paused'
    assert not (runtime/'state').exists()
