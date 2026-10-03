"""Production-shaped synthetic manifest/ledger identity checks for dot imports."""
import copy
import hashlib
import importlib.util
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
import dot_import_coordination as coordination
import dot_article_import as full_import
import dot_url_article_import as url_import
import export_dot_articles_readonly as full_export
import export_dot_urls_readonly as url_export
from test_dot_import_coordination import imported, ddl

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return full_export.digest({k: v for k, v in value.items()
                               if k not in {'batch_id', 'manifest_hash'}})


def legacy(base, case=None, *, claims=False, finished=False, no_claims_table=False):
    root = base.peer
    value = {'items': [{'id': 'legacy-item', 'source_refs': [{'entry_id': 99}]}]}
    value.update(batch_id='legacy', manifest_hash=canonical(value))
    expected = value['manifest_hash']
    if case == 'changed_refs_old_hash':
        value['items'][0]['source_refs'][0]['entry_id'] = 98
    elif case == 'wrong_batch_identity':
        value['batch_id'] = 'different'
    elif case == 'missing_item_id':
        del value['items'][0]['id']
        expected = value['manifest_hash'] = canonical(value)
    elif case == 'duplicate_item_id':
        value['items'].append(copy.deepcopy(value['items'][0]))
        expected = value['manifest_hash'] = canonical(value)
    elif case == 'empty_item_id':
        value['items'][0]['id'] = ''
        expected = value['manifest_hash'] = canonical(value)
    elif case == 'nonstring_item_id':
        value['items'][0]['id'] = 1
        expected = value['manifest_hash'] = canonical(value)
    elif case == 'missing_manifest_hash':
        del value['manifest_hash']
    elif case == 'wrong_manifest_hash':
        value['manifest_hash'] = 'b' * 64
    elif case == 'wrong_ledger_hash':
        expected = 'b' * 64
    elif case == 'invalid_ledger_hash':
        expected = 'not-a-hash'
    elif case == 'uppercase_ledger_hash':
        expected = 'A' * 64
    with sqlite3.connect(root / 'batches.sqlite3') as db:
        if no_claims_table:
            db.execute('DROP TABLE batch_claims')
        db.execute('INSERT INTO batches VALUES (?,?,?,NULL,NULL,0)',
                   ('legacy', expected, 'resolved' if finished else 'running'))
        if claims:
            db.execute("INSERT INTO batch_claims VALUES ('legacy',99)")
    folder = root / 'legacy'
    folder.mkdir()
    (folder / 'manifest.json').write_text(json.dumps(value), encoding='utf-8')


CASES = ['changed_refs_old_hash', 'wrong_batch_identity', 'missing_item_id', 'invalid_ledger_hash',
         'wrong_ledger_hash', 'wrong_manifest_hash', 'missing_manifest_hash', 'duplicate_item_id',
         'empty_item_id', 'nonstring_item_id', 'uppercase_ledger_hash']


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('phase', ['dry_run', 'before_backup', 'during_backup'])
def test_import_rejects_invalid_manifest_identity(imported, case, phase):
    kind, fixture, base = imported
    backups = []
    def backup(*_):
        backups.append(1)
        if phase == 'during_backup':
            legacy(base, case)
        return 'b' * 64
    if phase != 'during_backup':
        legacy(base, case)
    with closing(sqlite3.connect(base.db)) as db:
        before = list(db.iterdump())
    module = full_import if kind == 'fulltext' else url_import
    results = fixture.result if kind == 'fulltext' else fixture.results
    kwargs = {'batch_limit': 12} if kind == 'url' else {}
    with pytest.raises(ValueError):
        module.import_batch(base.config, fixture.packet, results,
                            lambda: copy.deepcopy(fixture.upstream),
                            apply=phase != 'dry_run', backup=backup, **kwargs)
    with closing(sqlite3.connect(base.db)) as db:
        assert list(db.iterdump()) == before
    base.assert_clean()
    if kind == 'url':
        fixture.clean()
    assert backups == ([1] if phase == 'during_backup' else [])


@pytest.mark.parametrize('case', CASES)
def test_export_rejects_invalid_manifest_identity(imported, case):
    kind, fixture, base = imported
    legacy(base, case)
    before = {p: p.read_bytes() for p in base.root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        if kind == 'fulltext':
            full_export.export(base.config, enabled_feed_ids={1})
        else:
            url_export.export(base.config, scope_user_id=1, limit=12, exclude_entry_ids=[],
                              feed_reader=lambda _: [{'id': 1, 'user_id': 1, 'disabled': False}])
    assert {p: p.read_bytes() for p in base.root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('mode', ['legacy', 'no_claims_table', 'claims_missing_manifest',
                                 'claims_corrupt_manifest', 'finished_missing_manifest'])
def test_valid_historical_controls_remain_accepted(imported, mode):
    kind, fixture, base = imported
    legacy(base, claims=mode.startswith('claims'), finished=mode.startswith('finished'),
           no_claims_table=mode == 'no_claims_table')
    path = base.peer / 'legacy/manifest.json'
    if mode.endswith('missing_manifest'):
        path.unlink()
    elif mode.endswith('corrupt_manifest'):
        path.write_text('not json', encoding='utf-8')
    expected = set() if mode.startswith('finished') else {99}
    assert coordination.lane_claims(base.config) == expected
    assert controller().claimed_entries([base.peer]) == expected
    before = base.db.read_bytes()
    outcome = fixture.run_import()
    assert outcome['state'] == 'validated_no_write'
    assert base.db.read_bytes() == before
    assert base.backups == []


def controller():
    # Load the actual read-only queue validator with its stdlib-only error module.
    path = ROOT / 'src/kaggle_batch'
    prior = list(sys.path)
    try:
        sys.path.insert(0, str(path))
        spec = importlib.util.spec_from_file_location('synthetic_queue_contract', path / 'queue_dispatch.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = prior


@pytest.mark.parametrize('case', CASES)
def test_relevant_manifest_contract_agrees_with_controller(imported, case):
    kind, fixture, base = imported
    legacy(base, case)
    queue = controller()
    with pytest.raises(queue.DispatchBlocked) as blocked:
        queue.claimed_entries([base.peer])
    assert blocked.value.reason == ('invalid_ledger' if case in
                                    {'invalid_ledger_hash', 'uppercase_ledger_hash'} else 'invalid_manifest')
    with pytest.raises(ValueError):
        coordination.lane_claims(base.config)


@pytest.mark.parametrize('mode', ['claims', 'finished'])
@pytest.mark.parametrize('fingerprint', ['invalid', 'A' * 64])
def test_invalid_ledger_hash_blocks_even_authoritative_or_finished_batches(imported, mode, fingerprint):
    kind, fixture, base = imported
    legacy(base, claims=mode == 'claims', finished=mode == 'finished')
    with sqlite3.connect(base.peer / 'batches.sqlite3') as db:
        db.execute('UPDATE batches SET manifest_hash=?', (fingerprint,))
    with pytest.raises(ValueError, match='invalid_lane_ledger'):
        coordination.lane_claims(base.config)
    queue = controller()
    with pytest.raises(queue.DispatchBlocked) as blocked:
        queue.claimed_entries([base.peer])
    assert blocked.value.reason == 'invalid_ledger'
    base.assert_clean()


@pytest.mark.parametrize('column', ['manifest_hash', 'remote_status'])
def test_incomplete_ledger_schema_blocks_even_empty_lanes(imported, column):
    kind, fixture, base = imported
    with sqlite3.connect(base.peer / 'batches.sqlite3') as db:
        db.execute('ALTER TABLE batches DROP COLUMN ' + column)
    with pytest.raises(ValueError, match='invalid_lane_ledger'):
        coordination.lane_claims(base.config)
    queue = controller()
    with pytest.raises(queue.DispatchBlocked) as blocked:
        queue.claimed_entries([base.peer])
    assert blocked.value.reason == 'invalid_ledger'
    base.assert_clean()


def test_signed_metadata_and_shared_refs_preserve_canonical_contract(imported):
    kind, fixture, base = imported
    legacy(base)
    path = base.peer / 'legacy/manifest.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    value['metadata'] = {'nullable': None, 'raw_text': '\\u4e2d\\u6587'.encode().decode('unicode_escape')}
    value['items'].append({'id': 'second-item', 'source_refs': [{'entry_id': 99}, {'entry_id': 98}]})
    value['manifest_hash'] = canonical(value)
    with sqlite3.connect(base.peer / 'batches.sqlite3') as db:
        db.execute('UPDATE batches SET manifest_hash=?', (value['manifest_hash'],))
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    assert coordination.lane_claims(base.config) == {98, 99}
    assert controller().claimed_entries([base.peer]) == {98, 99}
