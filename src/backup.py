"""Bounded online Reader backups; production restore is an explicit operator action."""

# Reject incomplete/unknown commands before imports, credentials, locks or writes.
if __name__ == '__main__':
    import argparse
    _parser = argparse.ArgumentParser(allow_abbrev=False)
    _parser.add_argument('--verify-latest', action='store_true')
    _parser.add_argument('--lane-config', action='append', default=[])
    _parser.add_argument('--paused-file')
    _args = _parser.parse_args()
    if not _args.verify_latest and (len(_args.lane_config) != 5 or not _args.paused_file):
        _parser.error('backup requires five --lane-config paths and --paused-file')

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import time
from datetime import datetime, timezone
from initialize_secrets import ROOT, ENV, read_secret

BIN = ROOT / 'runtime/pg/usr/lib/postgresql/16/bin'
BACKUPS = ROOT / 'backups'
LANES = ('primary', 'secondary', 'third', 'fourth', 'fifth')
FINISHED = {'imported', 'retired', 'resolved'}
RECEIPTS = ('submit-receipt.json', 'submit-receipt.json.pending',
            'submit-diagnostic.json', 'quarantine-reviewed.json',
            'download-verified.json', 'verified-results.json')
RETAIN = 2
MAX_BYTES = 1024 ** 3
MIN_FREE_BYTES = 1024 ** 3
TIMEOUT_SECONDS = 600
PRODUCER = 'personal-ai-inbox.backup/online-v2'


class BackupRefused(RuntimeError):
    pass


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise BackupRefused('backup_deadline')
    return value


@contextlib.contextmanager
def task_deadline():
    def expired(*_):
        raise BackupRefused('backup_deadline')
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise BackupRefused('existing_deadline')
    deadline = time.monotonic() + TIMEOUT_SECONDS
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, TIMEOUT_SECONDS)
    try:
        yield deadline
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def stored_bytes(folder, deadline):
    """Count all existing backup objects, including legacy/unknown leftovers."""
    total = 0
    def unreadable(_):
        raise BackupRefused('backup_inventory_unreadable')
    for base, dirs, files in os.walk(folder, followlinks=False, onerror=unreadable):
        remaining(deadline)
        for name in [*dirs, *files]:
            value = (Path(base) / name).lstat()
            if not stat.S_ISDIR(value.st_mode):
                total += max(value.st_size, value.st_blocks * 512)
    return total


def allowance(deadline):
    remaining(deadline)
    value = min(MAX_BYTES - stored_bytes(BACKUPS, deadline),
                shutil.disk_usage(BACKUPS).free - MIN_FREE_BYTES)
    if value < 0:
        raise BackupRefused('backup_budget_or_free_space')
    return value


def identity(path):
    value = path.lstat()
    # Reading a regular file with other hard links does not modify those links.
    if not stat.S_ISREG(value.st_mode):
        raise BackupRefused('non_regular_or_linked_file')
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def digest(path, deadline):
    result = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            remaining(deadline)
            result.update(block)
    return result.hexdigest()


def run(args, deadline, env=ENV):
    result = subprocess.run([str(x) for x in args], env=env, capture_output=True,
                            timeout=remaining(deadline))
    if result.returncode:
        raise BackupRefused('command_failed_' + Path(str(args[0])).name)
    return result.stdout.decode().strip()


def pg_env(owner=False):
    return {**ENV, 'PGCONNECT_TIMEOUT': '5', 'PGPASSWORD': read_secret(
        'postgres.password' if owner else 'database.password')}


def git_identity(deadline):
    try:
        value = run(['git', '-C', ROOT, 'rev-parse', 'HEAD'], deadline)
    except BackupRefused as exc:
        if str(exc) != 'command_failed_git':
            raise
        return None
    except OSError:
        return None
    return value if re.fullmatch(r'[0-9a-f]{40}', value) else None


def load_scope(config_paths, paused_path):
    """Resolve only the five operator-supplied, currently used lane configs."""
    if len(config_paths) != len(LANES):
        raise BackupRefused('five_lane_configs_required')
    configs, fingerprints = {}, {}
    for value in config_paths:
        path = Path(value)
        match = re.fullmatch(r'cloud-config-month-(primary|secondary|third|fourth|fifth)\.json', path.name)
        if (not path.is_absolute() or not match
                or path.parent.resolve() != (ROOT/'src/kaggle_batch').resolve()):
            raise BackupRefused('unexpected_config_path')
        path = ROOT/'src/kaggle_batch'/path.name
        key = match[1]
        if key in configs:
            raise BackupRefused('duplicate_lane_config')
        fingerprints[path] = identity(path)
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
            config = json.load(source)
        expected = ROOT / 'state' / ('kaggle-month-' + key)
        source = Path(config['source'])
        if (Path(config['state_root']).resolve() != expected.resolve()
                or Path(config['database']).resolve() != (ROOT/'state/analysis.sqlite3').resolve()
                or not source.is_absolute() or not source.is_dir()):
            raise BackupRefused('unexpected_database_scope')
        if identity(path) != fingerprints[path]:
            raise BackupRefused('configuration_changed')
        configs[key] = path
    # This is the single reviewed runtime exception used by dispatch_policy.
    from kaggle_batch.dispatch_policy import PAUSE_FILE
    supplied_pause = Path(paused_path)
    pause = Path(PAUSE_FILE)
    if not supplied_pause.is_absolute() or supplied_pause.resolve() != pause.resolve():
        raise BackupRefused('unexpected_pause_path')
    databases = [ROOT/'state/analysis.sqlite3', *[
        ROOT/'state'/('kaggle-month-'+key)/'batches.sqlite3' for key in LANES]]
    return configs, pause, databases, fingerprints


def write_stream(source, target, deadline, owned):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    owned.add(target)
    with target.open('xb') as output:
        while block := source.read(1024 * 1024):
            # Round up the next allocation; preserve the free-space floor.
            if ((len(block) + 4095) // 4096) * 4096 > allowance(deadline):
                raise BackupRefused('backup_budget_or_free_space')
            output.write(block)
            output.flush()
        os.fsync(output.fileno())
    allowance(deadline)


def copy_file(source, target, deadline, owned, observed, optional=False):
    remaining(deadline)
    try:
        before = identity(source)
    except FileNotFoundError:
        if not optional:
            raise BackupRefused('required_metadata_missing') from None
        observed[source] = None
        return
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        value = os.fstat(stream.fileno())
        if (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns) != before:
            raise BackupRefused('metadata_changed')
        write_stream(stream, target, deadline, owned)
    if identity(source) != before:
        raise BackupRefused('metadata_changed')
    observed[source] = before


def sqlite_snapshot(source, target, deadline, owned):
    identity(source)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    owned.add(target)
    with target.open('xb'):
        pass
    cap = allowance(deadline) // 4096 * 4096
    old_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
    old_signal = signal.getsignal(signal.SIGXFSZ)
    soft = cap if old_limit[0] == resource.RLIM_INFINITY else min(cap, old_limit[0])
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (soft, old_limit[1]))
    try:
        with contextlib.closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True,
                                               timeout=min(5, remaining(deadline)))) as src, \
             contextlib.closing(sqlite3.connect(target)) as dst:
            src.execute('PRAGMA query_only=ON')
            dst.execute('PRAGMA journal_mode=OFF')  # Unpublished, disposable destination only.
            page_size = src.execute('PRAGMA page_size').fetchone()[0]
            if src.execute('PRAGMA page_count').fetchone()[0] * page_size > cap:
                raise BackupRefused('sqlite_exceeds_remaining_budget')
            def progress(status, left, total):
                remaining(deadline)
                if total * page_size > cap:
                    raise BackupRefused('sqlite_exceeds_remaining_budget')
                allowance(deadline)
            src.backup(dst, pages=128, progress=progress, sleep=0.01)
            # WAL source headers must not leave the completed snapshot dependent
            # on later-created sidecars. This changes only the private destination.
            if dst.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                raise BackupRefused('sqlite_snapshot_journal_mode')
            dst.set_progress_handler(lambda: 0 if remaining(deadline) else 1, 1000)
            if dst.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise BackupRefused('sqlite_snapshot_integrity')
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, old_limit)
        signal.signal(signal.SIGXFSZ, old_signal)
    with target.open('rb') as completed:
        os.fsync(completed.fileno())
    allowance(deadline)


def postgres_snapshot(target, deadline, owned):
    args = [BIN/'pg_dump', '-h', '127.0.0.1', '-p', '55432', '-U', 'news_app',
            '-d', 'news', '--no-password', '-Fc']
    process = subprocess.Popen([str(x) for x in args], env=pg_env(),
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        write_stream(process.stdout, target, deadline, owned)
        if process.wait(timeout=remaining(deadline)):
            raise BackupRefused('pg_dump_failed')
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=min(1, max(0, deadline-time.monotonic())))
        except subprocess.TimeoutExpired:
            pass  # The owned child was killed; never extend the task deadline to reap it.
        process.stdout.close()
    run([BIN/'pg_restore', '--list', target], deadline)


def lane_rows(database):
    with contextlib.closing(sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)) as db:
        return db.execute('SELECT id,state,manifest_hash FROM batches ORDER BY id').fetchall()


def metadata_files(point, configs, pause, deadline, owned, observed):
    for path in configs.values():
        copy_file(path, point/'files'/path.relative_to(ROOT), deadline, owned, observed)
    copy_file(pause, point/'files/runtime/qwen-month-20260925/paused.json',
              deadline, owned, observed, optional=True)
    for key in LANES:
        lane = ROOT/'state'/('kaggle-month-'+key)
        copy_file(lane/'recovery.json', point/'files'/('state/kaggle-month-'+key)/'recovery.json',
                  deadline, owned, observed, optional=True)
        rows = lane_rows(point/'sqlite'/('kaggle-month-'+key)/'batches.sqlite3')
        for batch, state, expected_hash in rows:
            if state in FINISHED:
                continue
            if not isinstance(batch, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', batch):
                raise BackupRefused('invalid_batch_identity')
            folder = lane/batch
            dest = point/'files'/folder.relative_to(ROOT)
            copy_file(folder/'manifest.json', dest/'manifest.json', deadline, owned, observed)
            manifest = json.loads((dest/'manifest.json').read_text())
            canonical = {k:v for k,v in manifest.items() if k not in {'batch_id','manifest_hash'}}
            actual = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                               separators=(',', ':')).encode()).hexdigest()
            if manifest.get('batch_id') != batch or manifest.get('manifest_hash') != actual or expected_hash != actual:
                raise BackupRefused('batch_manifest_mismatch')
            for name in RECEIPTS:
                copy_file(folder/name, dest/name, deadline, owned, observed, optional=True)
        if lane_rows(lane/'batches.sqlite3') != rows:
            raise BackupRefused('lane_changed_during_backup')


def point_manifest(point, deadline):
    """Only this format's exact, checksum-valid file set can be retired."""
    try:
        if point.is_symlink() or not point.is_dir() or not point.name.startswith('online-') or point.name.endswith('.partial'):
            return None
        directory = point.stat()
        if directory.st_uid != os.getuid() or directory.st_mode & 0o022:
            return None
        identity(point/'manifest.json')
        with os.fdopen(os.open(point/'manifest.json', os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
            manifest = json.load(source)
        if manifest.get('producer') != PRODUCER or manifest.get('complete') is not True:
            return None
        files = manifest['sha256']
        required = {'miniflux.dump', 'sqlite/analysis.sqlite3', *[
            'sqlite/kaggle-month-'+key+'/batches.sqlite3' for key in LANES], *[
            'files/src/kaggle_batch/cloud-config-month-'+key+'.json' for key in LANES]}
        if not required <= set(files):
            return None
        if datetime.fromisoformat(manifest['created_utc']).tzinfo is None:
            return None
        actual = {str(p.relative_to(point)) for p in point.rglob('*') if p.is_symlink() or not p.is_dir()}
        if actual != set(files) | {'manifest.json'}:
            return None
        for name, checksum in files.items():
            lane_pattern = '(?:' + '|'.join(LANES) + ')'
            receipt_pattern = '(?:manifest\\.json|' + '|'.join(re.escape(x) for x in RECEIPTS) + ')'
            if (name not in required | {'files/runtime/qwen-month-20260925/paused.json'}
                    and not re.fullmatch('files/state/kaggle-month-'+lane_pattern+'/recovery\\.json', name)
                    and not re.fullmatch('files/state/kaggle-month-'+lane_pattern+'/[A-Za-z0-9_-]{1,200}/'+receipt_pattern, name)):
                return None
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts:
                return None
            path = point/relative
            if any((point/Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts)+1)):
                return None
            identity(path)
            if digest(path, deadline) != checksum:
                return None
        return manifest
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def remove_owned_files(point, files):
    """Delete only known files, then empty directories; never an unknown tree."""
    parents = {point}
    for path in files:
        if path.exists() or path.is_symlink():
            path.unlink()
        parents.update(p for p in path.parents if p == point or point in p.parents)
    for folder in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass


def create_backup(config_paths, paused_path):
    with task_deadline() as deadline:
        os.umask(0o077)
        configs, pause, databases, observed = load_scope(config_paths, paused_path)
        BACKUPS.mkdir(exist_ok=True, mode=0o700)
        with os.fdopen(os.open(BACKUPS/'.backup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            completed = []
            for path in BACKUPS.iterdir():
                manifest = point_manifest(path, deadline)
                if manifest:
                    completed.append((manifest['created_utc'], path, manifest))
            completed.sort(key=lambda row: row[0])
            # Make space for the next point without ever deleting the last good one.
            for _, path, manifest in completed[:-(RETAIN-1)]:
                if point_manifest(path, deadline) != manifest:
                    raise BackupRefused('retention_point_changed')
                remove_owned_files(path, [path/name for name in [*manifest['sha256'], 'manifest.json']])
            allowance(deadline)
            name = 'online-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4)
            point = BACKUPS/(name+'.partial')
            point.mkdir(mode=0o700)
            owned = set()
            try:
                postgres_snapshot(point/'miniflux.dump', deadline, owned)
                for source in databases:
                    sqlite_snapshot(source, point/'sqlite'/source.relative_to(ROOT/'state'), deadline, owned)
                metadata_files(point, configs, pause, deadline, owned, observed)
                for path, before in observed.items():
                    after = identity(path) if path.exists() or path.is_symlink() else None
                    if before != after:
                        raise BackupRefused('metadata_changed')
                manifest = {
                    'producer': PRODUCER, 'complete': True,
                    'created_utc': datetime.now(timezone.utc).isoformat(),
                    'git_commit': git_identity(deadline),
                    'pause_file': str(pause),
                    'consistency': 'individual_online_database_snapshots',
                    'restore_requires_operator_reconciliation': True,
                    'absent_optional_files': [
                        'runtime/qwen-month-20260925/paused.json' if p == pause else str(p.relative_to(ROOT))
                        for p,v in observed.items() if v is None],
                    'sha256': {str(p.relative_to(point)): digest(p, deadline) for p in sorted(owned)},
                }
                import io
                write_stream(io.BytesIO(json.dumps(manifest, sort_keys=True).encode()),
                             point/'manifest.json', deadline, owned)
                allowance(deadline)
                directories = {point}
                for path in owned:
                    directories.update(p for p in path.parents if p == point or point in p.parents)
                for folder in sorted(directories, key=lambda p: len(p.parts), reverse=True):
                    remaining(deadline)
                    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                point.rename(BACKUPS/name)
                fd = os.open(BACKUPS, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                return {'completed': True, 'snapshot': name, 'sqlite_databases': len(databases),
                        'retain': RETAIN, 'max_bytes': MAX_BYTES, 'minimum_free_bytes': MIN_FREE_BYTES}
            except BaseException:
                remove_owned_files(point, owned)
                raise


def verify_latest():
    """Verify stored bytes only. This command does not claim a live restore test."""
    with task_deadline() as deadline:
        with os.fdopen(os.open(BACKUPS/'.backup.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            points = [(p, point_manifest(p, deadline)) for p in BACKUPS.iterdir()]
            good = [(m['created_utc'], p, m) for p,m in points if m]
            if not good:
                raise BackupRefused('no_verified_online_backup')
            _, path, manifest = max(good, key=lambda row: row[0])
            run([BIN/'pg_restore', '--list', path/'miniflux.dump'], deadline)
            for name in manifest['sha256']:
                if name.startswith('sqlite/'):
                    with contextlib.closing(sqlite3.connect((path/name).as_uri()+'?mode=ro', uri=True)) as db:
                        db.set_progress_handler(lambda: 0 if remaining(deadline) else 1, 1000)
                        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                            raise BackupRefused('sqlite_snapshot_integrity')
            return {'passed': True, 'snapshot': path.name, 'restore_executed': False}


if __name__ == '__main__':
    try:
        result = verify_latest() if _args.verify_latest else create_backup(_args.lane_config, _args.paused_file)
        print(json.dumps(result))
    except Exception as exc:
        # No private config, database content, credential or raw command output.
        print(json.dumps({'completed': False, 'error': str(exc) if isinstance(exc, BackupRefused) else type(exc).__name__}))
        raise SystemExit(1)
