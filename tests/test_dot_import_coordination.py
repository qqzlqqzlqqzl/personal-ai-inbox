"""Synthetic importer coordination races; no services, credentials or real imports."""
import ast
import copy
import gc
import json
import sqlite3
import time
from pathlib import Path

import pytest
import test_dot_article_import as full_fixture
import test_dot_url_article_import as url_fixture
import dot_article_import as full_import
import dot_url_article_import as url_import
import export_dot_articles_readonly as full_export
import export_dot_urls_readonly as url_export

ROOT = Path(__file__).resolve().parents[1]
# Exact production DDL: Controller.__init__ and cloud_bridge.select_manifest.
LEASE_DDL = 'CREATE TABLE IF NOT EXISTS kaggle_prepare_leases (entry_id INTEGER PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)'


def ddl(table):
    tree = ast.parse((ROOT / 'src/kaggle_batch/batch_control.py').read_text(encoding='utf-8'))
    return next(node.args[0].value for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'execute' and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith('CREATE TABLE IF NOT EXISTS ' + table + ' '))


@pytest.fixture(params=['fulltext', 'url'])
def imported(request):
    fixture = full_fixture.ImportTests() if request.param == 'fulltext' else url_fixture.URLImportTests()
    fixture.setUp()
    base = fixture if request.param == 'fulltext' else fixture.f
    # Replace only disposable coordination tables with their production schema.
    with sqlite3.connect(base.peer / 'batches.sqlite3') as db:
        db.executescript('DROP TABLE batches; DROP TABLE batch_claims;')
        db.execute(ddl('batches'))
        db.execute(ddl('batch_claims'))
    base.change('DROP TABLE kaggle_prepare_leases')
    base.change(LEASE_DDL)
    yield request.param, fixture, base
    gc.collect()
    fixture.tearDown()
    gc.collect()


def mutate(base, case):
    if case == 'missing_leases':
        base.change('DROP TABLE kaggle_prepare_leases')
    elif case in {'negative_infinity', 'positive_infinity', 'legacy_null', 'text_expiry', 'invalid_lease_id'}:
        if case == 'legacy_null':
            base.change('DROP TABLE kaggle_prepare_leases')
            base.change('CREATE TABLE kaggle_prepare_leases(entry_id INTEGER, owner TEXT, expires REAL)')
        expiry = {'negative_infinity': float('-inf'), 'positive_infinity': float('inf'),
                  'legacy_null': None, 'text_expiry': 'unknown', 'invalid_lease_id': time.time() + 100}[case]
        # Malformed state must block even when the lease is unrelated to the packet.
        base.change('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)',
                    -1 if case == 'invalid_lease_id' else 99, 'synthetic-owner', expiry)
    else:
        with sqlite3.connect(base.peer / 'batches.sqlite3') as db:
            if case == 'orphan':
                db.execute("INSERT INTO batch_claims VALUES ('orphan',2)")
                return
            state = 'unknown' if case == 'unknown_state' else 'resolved' if case == 'invalid_finished_claim' else 'running'
            db.execute('INSERT INTO batches VALUES (?,?,?,NULL,NULL,0)', ('active', 'a' * 64, state))
            if case in {'invalid_claim', 'invalid_finished_claim'}:
                db.execute("INSERT INTO batch_claims VALUES ('active',-1)")
                return
        folder = base.peer / 'active'
        folder.mkdir()
        manifest = {'items': []} if case == 'empty_active' else {'items': [{'source_refs': []}]}
        fingerprint = full_export.digest(manifest)
        with sqlite3.connect(base.peer / 'batches.sqlite3') as db:
            db.execute('UPDATE batches SET manifest_hash=? WHERE id=?', (fingerprint, 'active'))
        manifest.update(batch_id='active', manifest_hash=fingerprint)
        (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


CASES = ['orphan', 'empty_active', 'missing_leases', 'negative_infinity', 'legacy_null',
         'positive_infinity', 'text_expiry', 'invalid_lease_id', 'invalid_claim',
         'invalid_finished_claim', 'unknown_state', 'empty_refs']


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('phase', ['dry_run', 'before_backup', 'during_backup'])
def test_import_rejects_malformed_coordination(imported, case, phase):
    kind, fixture, base = imported
    backup_calls = []
    def backup(*args):
        backup_calls.append(1)
        if phase == 'during_backup':
            mutate(base, case)
        return 'b' * 64
    if phase != 'during_backup':
        mutate(base, case)
    # Includes analyses, cards, settings, old Kaggle receipts and all other tables.
    def snapshot():
        with sqlite3.connect(base.db) as db:
            tables = db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name!='kaggle_prepare_leases' ORDER BY name").fetchall()
            return [(name, sql, db.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall())
                    for name, sql in tables]
    before = snapshot()
    module = full_import if kind == 'fulltext' else url_import
    packet = fixture.packet
    results = fixture.result if kind == 'fulltext' else fixture.results
    kwargs = {'batch_limit': 12} if kind == 'url' else {}
    with pytest.raises((ValueError, sqlite3.Error)):
        module.import_batch(base.config, packet, results, lambda: copy.deepcopy(fixture.upstream),
                            apply=phase != 'dry_run', backup=backup, **kwargs)
    assert snapshot() == before
    base.assert_clean()
    if kind == 'url':
        fixture.clean()
    assert backup_calls == ([1] if phase == 'during_backup' else [])


@pytest.mark.parametrize('case', CASES)
def test_exporters_reject_same_malformed_coordination(imported, case):
    kind, fixture, base = imported
    mutate(base, case)
    before = {p: p.read_bytes() for p in base.root.rglob('*') if p.is_file()}
    with pytest.raises((ValueError, sqlite3.Error)):
        if kind == 'fulltext':
            full_export.export(base.config, enabled_feed_ids={1})
        else:
            url_export.export(base.config, scope_user_id=1, limit=12, exclude_entry_ids=[],
                              feed_reader=lambda _: [{'id': 1, 'user_id': 1, 'disabled': False}])
    assert {p: p.read_bytes() for p in base.root.rglob('*') if p.is_file()} == before


def test_production_ddl_rejects_null_but_accepts_negative_infinity(tmp_path):
    with sqlite3.connect(tmp_path / 'synthetic.sqlite3') as db:
        db.execute(LEASE_DDL)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO kaggle_prepare_leases VALUES (1,?,NULL)', ('synthetic-owner',))
        db.execute('INSERT INTO kaggle_prepare_leases VALUES (1,?,?)', ('synthetic-owner', float('-inf')))
        assert db.execute('SELECT expires FROM kaggle_prepare_leases').fetchone()[0] == float('-inf')


def test_historical_receipt_replay_is_not_current_reconciliation(imported):
    kind, fixture, base = imported
    prior = fixture.run_import(apply=True)
    # A later independent success changes the current row; replay returns old counts.
    base.change("UPDATE analyses SET model='later-synthetic-model' WHERE entry_id=2")
    mutate(base, 'orphan')
    base.change('DROP TABLE kaggle_prepare_leases')
    before = base.db.read_bytes()
    old_backups = list(base.backups)
    result = fixture.run_import(apply=True)
    assert result['state'] == 'already_imported'
    assert result['analysis_count'] == prior['analysis_count']
    assert base.db.read_bytes() == before
    assert base.backups == old_backups
    assert base.query('SELECT model FROM analyses WHERE entry_id=2') == [('later-synthetic-model',)]


def test_production_lease_ddl_matches_source():
    tree = ast.parse((ROOT / 'src/kaggle_batch/cloud_bridge.py').read_text(encoding='utf-8'))
    assert any(isinstance(node, ast.Constant) and node.value == LEASE_DDL for node in ast.walk(tree))


def test_shared_validator_is_readonly_and_closes_connections(imported, monkeypatch):
    import dot_import_coordination as coordination
    kind, fixture, base = imported
    before = {p: p.read_bytes() for p in base.root.rglob('*') if p.is_file()}
    connections, statements = [], []
    original = coordination.ro
    writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
              sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_TEMP_TABLE,
              sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_CREATE_TRIGGER,
              sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH}
    def readonly(path):
        db = original(path)
        db.set_trace_callback(statements.append)
        db.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in writes else sqlite3.SQLITE_OK)
        connections.append(db)
        return db
    monkeypatch.setattr(coordination, 'ro', readonly)
    with pytest.MonkeyPatch.context() as blocked:
        import socket
        blocked.setattr(socket, 'socket', lambda *_a, **_k: pytest.fail('network_forbidden'))
        assert coordination.exclusions(base.config) == (set(), set())
    assert sum(statement == 'BEGIN' for statement in statements) == 2
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')
    assert {p: p.read_bytes() for p in base.root.rglob('*') if p.is_file()} == before


def test_import_lock_is_retained_through_backup_and_fresh_read(imported):
    import fcntl
    kind, fixture, base = imported
    stages = []
    def assert_locked(stage):
        with (base.coord / 'bridge.lock').open('rb') as other:
            for operation in (fcntl.LOCK_SH, fcntl.LOCK_EX):
                with pytest.raises(BlockingIOError):
                    fcntl.flock(other, operation | fcntl.LOCK_NB)
        stages.append(stage)
    def upstream():
        assert_locked('fresh_read')
        return copy.deepcopy(fixture.upstream)
    def backup(*_):
        assert_locked('backup')
        return 'b' * 64
    module = full_import if kind == 'fulltext' else url_import
    results = fixture.result if kind == 'fulltext' else fixture.results
    kwargs = {'batch_limit': 12} if kind == 'url' else {}
    out = module.import_batch(base.config, fixture.packet, results, upstream,
                              apply=True, backup=backup, **kwargs)
    assert out['state'] == 'imported'
    assert stages == ['fresh_read', 'backup', 'fresh_read']
    with (base.coord / 'bridge.lock').open('rb') as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(other, fcntl.LOCK_UN)
