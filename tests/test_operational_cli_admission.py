"""Help and invalid argv must exit before any operational module or function.

No product main function is called. A denied import is an isolated sentinel,
not a production read, network request, provider invocation or systemctl call.
"""
import ast
import builtins
from pathlib import Path
import runpy
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ('backup.py', 'finalize_runtime.py', 'import_sources.py', 'x_feed_prefetch.py',
           'kaggle_batch/recovery_watchdog.py', 'kaggle_batch/snapshot_retention.py',
           'kaggle_batch/lane_scheduler.py')


@pytest.mark.parametrize('entry', ENTRIES)
@pytest.mark.parametrize('arguments,exit_code', [(['--help'],0),(['--slot6-unknown'],2),(['--dry-run'],2),(['--verify-l'],2)])
def test_parameter_gate_exits_before_application_imports(entry,arguments,exit_code,monkeypatch,capsys):
    original=builtins.__import__
    imported=[]
    def guarded(name,*args,**kwargs):
        if name.split('.')[0] in {'initialize_secrets','ops_common','httpx','lane_scheduler',
                                  'snapshot_retention','batch_control','live_scope','exception_audit'}:
            imported.append(name)
            raise AssertionError('operational import before argv validation')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    monkeypatch.setattr(sys,'argv',[entry,*arguments])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(ROOT/'src'/entry),run_name='__main__')
    assert caught.value.code==exit_code and imported==[]
    output=capsys.readouterr()
    assert 'usage:' in (output.out+output.err)


def test_backup_verify_option_is_only_declared_operational_option():
    # Inspect the leading gate rather than invoking the authorized-write mode.
    tree=ast.parse((ROOT/'src/backup.py').read_text())
    gate=tree.body[1]
    args=[node.args[0].value for node in ast.walk(gate)
          if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
          and node.func.attr=='add_argument']
    assert args==['--verify-latest', '--lane-config', '--paused-file']


@pytest.fixture
def online_backup(tmp_path, monkeypatch):
    import io
    import json
    import os
    import sqlite3
    import types
    config = types.ModuleType('initialize_secrets')
    config.ROOT, config.ENV = tmp_path, {}
    config.read_secret = lambda *_: pytest.fail('No real credentials in backup tests')
    monkeypatch.setitem(sys.modules, 'initialize_secrets', config)
    ns = runpy.run_path(str(ROOT/'src/backup.py'), run_name='fixture_backup')
    ns = ns['create_backup'].__globals__
    paths = []
    (tmp_path/'src/kaggle_batch').mkdir(parents=True)
    (tmp_path/'state').mkdir()
    with sqlite3.connect(tmp_path/'state/analysis.sqlite3') as db:
        db.execute('CREATE TABLE analyses(value TEXT)')
        db.execute("INSERT INTO analyses VALUES ('synthetic analysis')")
    for key in ns['LANES']:
        lane = tmp_path/'state'/('kaggle-month-'+key); lane.mkdir()
        with sqlite3.connect(lane/'batches.sqlite3') as db:
            db.execute('CREATE TABLE batches(id TEXT,state TEXT,manifest_hash TEXT)')
            db.execute('CREATE TABLE batch_claims(batch_id TEXT,entry_id INTEGER)')
        path = tmp_path/'src/kaggle_batch'/('cloud-config-month-'+key+'.json')
        path.write_text(json.dumps({'state_root': str(lane),
            'database': str(tmp_path/'state/analysis.sqlite3'), 'source': str(tmp_path/'src'),
            'schedule_enabled': False, 'token_file': '/synthetic/not-read'}))
        paths.append(path)
    pause = tmp_path/'runtime/qwen-month-20260925/paused.json'
    pause.parent.mkdir(parents=True); pause.write_text('{"paused": true}')
    package = types.ModuleType('kaggle_batch'); package.__path__ = []
    policy = types.ModuleType('kaggle_batch.dispatch_policy'); policy.PAUSE_FILE = pause
    monkeypatch.setitem(sys.modules, 'kaggle_batch', package)
    monkeypatch.setitem(sys.modules, 'kaggle_batch.dispatch_policy', policy)
    calls = []
    def pg(target, deadline, owned):
        calls.append('online_pg')
        ns['write_stream'](io.BytesIO(b'synthetic custom-format pg dump'), target, deadline, owned)
    def command(args, deadline, env=None):
        names = list(map(str, args)); calls.append(names)
        assert 'systemctl' not in names and '--create' not in names
        assert names[0] == 'git' or names[0].endswith('/pg_restore') and '--list' in names
        return 'a' * 40
    monkeypatch.setitem(ns, 'postgres_snapshot', pg)
    monkeypatch.setitem(ns, 'run', command)
    monkeypatch.setattr(ns['shutil'], 'disk_usage', lambda _: types.SimpleNamespace(free=4*1024**3))
    old_umask = os.umask(0o077)
    try:
        yield ns, tmp_path, paths, pause, calls
    finally:
        os.umask(old_umask)


def add_batch(fixture, state='submit_unknown', batch='qwen-synthetic'):
    import hashlib, json, sqlite3
    ns, root, paths, pause, calls = fixture
    lane = root/'state/kaggle-month-primary'
    canonical = {'items': [{'id': 'sample', 'source_refs': [{'entry_id': n} for n in range(1,72)]}]}
    checksum = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':')).encode()).hexdigest()
    folder = lane/batch; folder.mkdir()
    (folder/'manifest.json').write_text(json.dumps({**canonical, 'batch_id': batch, 'manifest_hash': checksum}))
    for name in ns['RECEIPTS']:
        (folder/name).write_text(json.dumps({'synthetic': name, 'batch_id': batch}))
    with sqlite3.connect(lane/'batches.sqlite3') as db:
        db.execute('INSERT INTO batches VALUES (?,?,?)', (batch,state,checksum))
        db.executemany('INSERT INTO batch_claims VALUES (?,?)', [(batch,n) for n in range(1,72)])
    return folder


@pytest.mark.parametrize('state', ['submit_unknown', 'quarantined'])
def test_backup_online_six_databases_and_exact_state_whitelist(online_backup, state):
    import sqlite3
    ns, root, configs, pause, calls = online_backup
    folder = add_batch(online_backup, state)
    (folder.parent/'recovery.json').write_text('{"retry_at": 123}')
    (root/'.private').mkdir(); (root/'.private/never-copy').write_text('synthetic excluded')
    (root/'runtime/model.bin').write_bytes(b'excluded model')
    (root/'state/cache.bin').write_bytes(b'excluded cache')
    result = ns['create_backup'](configs, pause)
    point = root/'backups'/result['snapshot']
    assert result['completed'] and result['sqlite_databases'] == 6
    assert len(list((point/'sqlite').rglob('*.sqlite3'))) == 6
    with sqlite3.connect(point/'sqlite/kaggle-month-primary/batches.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM batch_claims').fetchone()[0] == 71
        assert db.execute('SELECT state FROM batches').fetchone()[0] == state
    assert (point/'files/runtime/qwen-month-20260925/paused.json').read_bytes() == pause.read_bytes()
    for name in ['manifest.json', *ns['RECEIPTS']]:
        assert (point/'files/state/kaggle-month-primary'/folder.name/name).read_bytes() == (folder/name).read_bytes()
    assert not (point/'files/.private').exists()
    assert not (point/'files/runtime/model.bin').exists()
    assert not (point/'files/state/cache.bin').exists()
    assert not list(point.rglob('restored-*'))
    assert ns['point_manifest'](point, ns['time'].monotonic()+30)


def test_backup_online_finished_batch_metadata_is_excluded(online_backup):
    ns, root, configs, pause, calls = online_backup
    folder = add_batch(online_backup, 'imported')
    point = root/'backups'/ns['create_backup'](configs, pause)['snapshot']
    assert not (point/'files/state/kaggle-month-primary'/folder.name).exists()


def test_backup_online_absent_pause_is_explicit_and_other_runtime_is_excluded(online_backup):
    import json
    ns, root, configs, pause, calls = online_backup
    pause.unlink()
    point = root/'backups'/ns['create_backup'](configs, pause)['snapshot']
    manifest = json.loads((point/'manifest.json').read_text())
    assert 'runtime/qwen-month-20260925/paused.json' in manifest['absent_optional_files']
    assert not (point/'files/runtime').exists()


def test_backup_online_retains_two_and_preserves_last_good_on_failure(online_backup, monkeypatch):
    ns, root, configs, pause, calls = online_backup
    names = [ns['create_backup'](configs, pause)['snapshot'] for _ in range(3)]
    assert not (root/'backups'/names[0]).exists()
    assert all((root/'backups'/name).exists() for name in names[1:])
    def fail(*_): raise ns['BackupRefused']('synthetic_pg_failure')
    monkeypatch.setitem(ns, 'postgres_snapshot', fail)
    with pytest.raises(ns['BackupRefused']): ns['create_backup'](configs, pause)
    assert (root/'backups'/names[-1]).exists()
    assert not list((root/'backups').glob('*.partial'))


@pytest.mark.parametrize('limit', ['budget', 'free', 'deadline'])
def test_backup_online_bounds_preserve_last_good(online_backup, monkeypatch, limit):
    import types
    ns, root, configs, pause, calls = online_backup
    name = ns['create_backup'](configs, pause)['snapshot']
    point = root/'backups'/name
    before = (point/'manifest.json').read_bytes()
    if limit == 'budget':
        used = ns['stored_bytes'](root/'backups', ns['time'].monotonic()+30)
        monkeypatch.setitem(ns, 'MAX_BYTES', used+4096)
    elif limit == 'free':
        monkeypatch.setattr(ns['shutil'], 'disk_usage', lambda _: types.SimpleNamespace(free=ns['MIN_FREE_BYTES']-1))
    else:
        original = ns['postgres_snapshot']
        def expire(target, deadline, owned):
            original(target, deadline, owned)
            ns['remaining'](ns['time'].monotonic()-1)
        monkeypatch.setitem(ns, 'postgres_snapshot', expire)
    with pytest.raises((ns['BackupRefused'], ns['sqlite3'].Error)):
        ns['create_backup'](configs, pause)
    assert (point/'manifest.json').read_bytes() == before
    assert not list((root/'backups').glob('*.partial'))
    assert ns['signal'].getitimer(ns['signal'].ITIMER_REAL) == (0.0, 0.0)


def test_backup_online_unknown_payload_counts_toward_budget_and_is_not_deleted(online_backup, monkeypatch):
    ns, root, configs, pause, calls = online_backup
    backups = root/'backups'; backups.mkdir()
    unknown = backups/'preserved-fullcycle'; unknown.mkdir()
    (unknown/'payload').write_bytes(b'not our object')
    monkeypatch.setitem(ns, 'MAX_BYTES', 1)
    with pytest.raises(ns['BackupRefused'], match='budget'):
        ns['create_backup'](configs, pause)
    assert (unknown/'payload').read_bytes() == b'not our object'
    assert calls == []


@pytest.mark.parametrize('mutation', ['extra_file', 'checksum', 'symlink'])
def test_backup_online_does_not_retire_unproven_point(online_backup, mutation):
    ns, root, configs, pause, calls = online_backup
    name = ns['create_backup'](configs, pause)['snapshot']
    old = root/'backups'/name
    if mutation == 'extra_file': (old/'rollback-needed').write_text('keep')
    elif mutation == 'checksum': (old/'miniflux.dump').write_bytes(b'changed')
    else:
        (old/'miniflux.dump').unlink(); (old/'miniflux.dump').symlink_to(root/'state/analysis.sqlite3')
    for _ in range(3): ns['create_backup'](configs, pause)
    assert old.exists()


@pytest.mark.parametrize('problem', ['duplicate', 'wrong_database', 'wrong_pause', 'missing_ledger'])
def test_backup_online_scope_refuses_before_publication(online_backup, problem):
    import json
    ns, root, configs, pause, calls = online_backup
    if problem == 'duplicate': configs = [configs[0]]*5
    elif problem == 'wrong_database':
        value = json.loads(configs[0].read_text()); value['database'] = str(root/'private.sqlite3')
        configs[0].write_text(json.dumps(value))
    elif problem == 'wrong_pause': pause = root/'runtime/whole-tree'
    else: (root/'state/kaggle-month-fifth/batches.sqlite3').unlink()
    with pytest.raises((ns['BackupRefused'], FileNotFoundError)):
        ns['create_backup'](configs, pause)
    assert not list((root/'backups').glob('online-*'))


def test_backup_online_metadata_drift_refuses_without_partial_point(online_backup, monkeypatch):
    ns, root, configs, pause, calls = online_backup
    add_batch(online_backup)
    original = ns['metadata_files']
    def drift(*args):
        original(*args); pause.write_text('{"paused": false}')
    monkeypatch.setitem(ns, 'metadata_files', drift)
    with pytest.raises(ns['BackupRefused'], match='metadata_changed'):
        ns['create_backup'](configs, pause)
    assert not list((root/'backups').glob('online-*'))


def test_backup_online_checksum_verification_does_not_restore_or_stop_services(online_backup):
    ns, root, configs, pause, calls = online_backup
    result = ns['create_backup'](configs, pause)
    verified = ns['verify_latest']()
    assert verified == {'passed': True, 'snapshot': result['snapshot'], 'restore_executed': False}
    assert any(isinstance(call,list) and '--list' in call for call in calls)
    assert not any(isinstance(call,list) and ('systemctl' in call or '--dbname' in call) for call in calls)


def test_backup_online_copies_committed_wal_without_stopping_writer(online_backup):
    import sqlite3
    ns, root, configs, pause, calls = online_backup
    with sqlite3.connect(root/'state/analysis.sqlite3') as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute("INSERT INTO analyses VALUES ('committed in WAL')"); writer.commit()
        point = root/'backups'/ns['create_backup'](configs, pause)['snapshot']
        with sqlite3.connect(point/'sqlite/analysis.sqlite3') as snapshot:
            assert snapshot.execute('SELECT COUNT(*) FROM analyses').fetchone()[0] == 2
        writer.execute("INSERT INTO analyses VALUES ('writer still alive')"); writer.commit()
    assert ns['point_manifest'](point, ns['time'].monotonic()+30)


@pytest.mark.parametrize('mode', ['success', 'budget', 'command', 'deadline'])
def test_backup_online_pg_stream_is_bounded_and_reaps_own_child(online_backup, monkeypatch, mode):
    import io
    ns, root, configs, pause, calls = online_backup
    # Restore only this function from the same source, retaining the synthetic namespace.
    tree = ast.parse((ROOT/'src/backup.py').read_text())
    node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name == 'postgres_snapshot')
    exec(compile(ast.Module(body=[node],type_ignores=[]), '<tested-pg-function>', 'exec'), ns)
    (root/'backups').mkdir(); target = root/'backups/fixture.dump'
    killed, timeouts = [], []
    class Child:
        stdout = io.BytesIO(b'synthetic pg archive')
        code = None
        def poll(self): return self.code
        def kill(self): killed.append(True); self.code = -9
        def wait(self, timeout):
            timeouts.append(timeout)
            if self.code is None: self.code = 1 if mode == 'command' else 0
            return self.code
    child = Child()
    commands = []
    def popen(args, **kwargs):
        commands.append(args)
        assert kwargs['stdout'] == ns['subprocess'].PIPE
        assert kwargs['stderr'] == ns['subprocess'].DEVNULL
        assert kwargs['env'] == {'synthetic': True}
        return child
    monkeypatch.setattr(ns['subprocess'], 'Popen', popen)
    monkeypatch.setitem(ns, 'pg_env', lambda: {'synthetic': True})
    if mode == 'budget': monkeypatch.setitem(ns, 'MAX_BYTES', 1)
    deadline = ns['time'].monotonic() + (30 if mode != 'deadline' else -1)
    if mode == 'success':
        ns['postgres_snapshot'](target, deadline, set())
        assert target.read_bytes() == b'synthetic pg archive' and not killed
        assert any(isinstance(call,list) and '--list' in call for call in calls)
    else:
        with pytest.raises(ns['BackupRefused']): ns['postgres_snapshot'](target, deadline, set())
        if mode != 'command': assert killed == [True]
    assert all(0 <= timeout <= 30 for timeout in timeouts)
    assert '--no-password' in commands[0] and '-Fc' in commands[0]
    assert all('systemctl' not in word for word in commands[0])


def test_backup_online_uses_existing_lock_and_refuses_competing_backup(online_backup):
    ns, root, configs, pause, calls = online_backup
    backups = root/'backups'; backups.mkdir()
    with (backups/'.backup.lock').open('a') as lock:
        ns['fcntl'].flock(lock, ns['fcntl'].LOCK_EX | ns['fcntl'].LOCK_NB)
        with pytest.raises(BlockingIOError): ns['create_backup'](configs, pause)
    assert calls == [] and not list(backups.glob('online-*'))


def test_backup_online_real_config_aliases_resolve_to_same_approved_files(online_backup):
    ns, root, configs, pause, calls = online_backup
    alias = root/'config-alias'; alias.symlink_to(root/'src/kaggle_batch')
    result = ns['create_backup']([alias/p.name for p in configs], pause)
    assert result['completed']
    assert ns['point_manifest'](root/'backups'/result['snapshot'], ns['time'].monotonic()+30)


@pytest.mark.parametrize('failure', ['not_repository', 'no_git_binary'])
def test_backup_deployment_without_git_still_records_success_with_null_identity(online_backup, monkeypatch, failure):
    import json
    ns, root, configs, pause, calls = online_backup
    original = ns['run']
    def command(args, deadline, env=None):
        if args[0] == 'git':
            if failure == 'no_git_binary': raise FileNotFoundError('synthetic missing git')
            raise ns['BackupRefused']('command_failed_git')
        return original(args, deadline, env)
    monkeypatch.setitem(ns, 'run', command)
    result = ns['create_backup'](configs, pause)
    manifest = json.loads((root/'backups'/result['snapshot']/'manifest.json').read_text())
    assert result['completed'] and manifest['git_commit'] is None


def test_backup_deployment_git_identity_does_not_swallow_deadline(online_backup, monkeypatch):
    ns, root, configs, pause, calls = online_backup
    def expired(*_): raise ns['BackupRefused']('backup_deadline')
    monkeypatch.setitem(ns, 'run', expired)
    with pytest.raises(ns['BackupRefused'], match='backup_deadline'):
        ns['git_identity'](ns['time'].monotonic()+1)


@pytest.mark.parametrize('source_kind', ['external_existing', 'relative', 'missing'])
def test_backup_deployment_primary_source_is_independent_from_database_scope(online_backup, source_kind):
    import json
    ns, root, configs, pause, calls = online_backup
    external = root/'reader-reviewed-source/src'
    if source_kind == 'external_existing':
        external.mkdir(parents=True); (external/'do-not-backup.py').write_text('synthetic source')
    value = json.loads(configs[0].read_text())
    value['source'] = str(external) if source_kind != 'relative' else 'src'
    configs[0].write_text(json.dumps(value))
    if source_kind != 'external_existing':
        with pytest.raises(ns['BackupRefused'], match='unexpected_database_scope'):
            ns['create_backup'](configs, pause)
    else:
        result = ns['create_backup'](configs, pause)
        point = root/'backups'/result['snapshot']
        assert result['completed'] and not list(point.rglob('do-not-backup.py'))
        copied = json.loads((point/'files/src/kaggle_batch'/configs[0].name).read_text())
        assert copied['source'] == str(external)


@pytest.mark.parametrize('present', [False, True])
def test_backup_deployment_uses_actual_dispatch_policy_pause_path(online_backup, monkeypatch, present):
    import json
    ns, root, configs, old_pause, calls = online_backup
    actual = root/'actual-dispatch-source/runtime/qwen-month-20260925/paused.json'
    if present:
        actual.parent.mkdir(parents=True); actual.write_text('{"paused": true, "synthetic": true}')
    monkeypatch.setattr(sys.modules['kaggle_batch.dispatch_policy'], 'PAUSE_FILE', actual)
    result = ns['create_backup'](configs, actual)
    point = root/'backups'/result['snapshot']
    manifest = json.loads((point/'manifest.json').read_text())
    target = point/'files/runtime/qwen-month-20260925/paused.json'
    assert manifest['pause_file'] == str(actual)
    assert target.exists() == present
    assert ('runtime/qwen-month-20260925/paused.json' in manifest['absent_optional_files']) == (not present)
    if present: assert target.read_bytes() == actual.read_bytes()
    assert 'active' not in manifest and old_pause.exists()
    with pytest.raises(ns['BackupRefused'], match='unexpected_pause_path'):
        ns['load_scope'](configs, old_pause)
