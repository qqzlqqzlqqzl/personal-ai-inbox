import hashlib
import json
import sqlite3
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
            db.executescript('CREATE TABLE batches(id TEXT PRIMARY KEY,state TEXT); CREATE TABLE batch_claims(batch_id TEXT,entry_id INTEGER);')
            db.execute('INSERT INTO batches VALUES(?,?)',(f'batch-{index}','submit_unknown'))
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
