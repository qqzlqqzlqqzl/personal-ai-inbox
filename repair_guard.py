"""Durable compensation for two fixed data repairs; no source or shell capability.

A separate systemd unit replays unfinished compensation after SIGKILL or reboot.
One current and one previous journal bound disk use; unresolved conflicts block writes.
"""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time

HOME = Path('/home/ubuntu/ai-news/.private/recovery-agent/repair-guard')
SOURCE = Path('/home/ubuntu/website-release-20261004/reader-3349cf2-source/src')
ROW_FIELDS = ('url', 'feed_id', 'cover_url', 'cover_source', 'preview_error', 'preview_checked_at', 'updated_at')
TERMINAL = {'committed', 'rolled_back', 'cancelled'}
LIMIT = 8*1024*1024


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path):
    if path.is_symlink():
        raise ValueError('guard_symlink')
    with path.open('rb') as stream:
        raw = stream.read(LIMIT+1)
    if len(raw) > LIMIT:
        raise ValueError('guard_size')
    return raw


def write(path, raw):
    if len(raw) > LIMIT or path.is_symlink():
        raise ValueError('guard_write_scope')
    pending = path.with_name(path.name+'.pending')
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    pending.replace(path)
    sync_dir(path.parent)


def save(value):
    write(HOME/'current.json', json.dumps(value, ensure_ascii=True, allow_nan=False).encode())


@contextmanager
def locked():
    HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    if HOME.is_symlink():
        raise ValueError('guard_home')
    fd = os.open(HOME/'guard.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def row_connection():
    os.environ['AI_NEWS_ROOT'] = str(SOURCE.parent)
    sys.path.insert(0, str(SOURCE))
    import core
    return core.connect()


def validate(record):
    if (not isinstance(record, dict) or record.get('version') != 1
            or record.get('state') not in TERMINAL | {'pending', 'blocked'}):
        raise ValueError('guard_record')
    kind, target = record['kind'], record['target']
    before, after = record['before'], record['after']
    if kind == 'vendor_schedule':
        from ops_runbooks import vendor_path, invalid_schedule
        vendor_path(target)  # Derive paths from live trusted configuration only.
        raw = base64.b64decode(before, validate=True)
        if len(raw) > 2*1024*1024:
            raise ValueError('guard_backup_size')
        original = json.loads(raw)
        if (not isinstance(after, dict) or not invalid_schedule(original.get('next_run_at', 0))
                or type(after.get('next_run_at')) is not int or after['next_run_at'] < 0
                or {k:v for k,v in original.items() if k != 'next_run_at'} !=
                   {k:v for k,v in after.items() if k != 'next_run_at'}):
            raise ValueError('guard_vendor_scope')
        observed = record.get('written_sha256')
        if observed is not None and (not isinstance(observed, str) or len(observed) != 64
                                     or any(c not in '0123456789abcdef' for c in observed)):
            raise ValueError('guard_written_hash')
    elif kind == 'preview_row':
        from production_cases import CASE_IDS
        if (type(target) is not int or target not in CASE_IDS or target == 58
                or not isinstance(before, dict) or not isinstance(after, dict)
                or set(before) != set(ROW_FIELDS) or set(after) != set(ROW_FIELDS)
                or any(before[k] != after[k] for k in ('url', 'feed_id'))
                or not isinstance(after['cover_url'], str) or not after['cover_url']
                or after['preview_error'] is not None
                or any(type(after[k]) not in (int, float) or not math.isfinite(after[k])
                       for k in ('preview_checked_at', 'updated_at'))):
            raise ValueError('guard_row_scope')
    else:
        raise ValueError('guard_kind')
    return record


def current():
    path = HOME/'current.json'
    return validate(json.loads(read(path))) if path.exists() or path.is_symlink() else None


def row_restore(record):
    target, before, after = record['target'], record['before'], record['after']
    with row_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT '+','.join(ROW_FIELDS)+' FROM analyses WHERE entry_id=?', (target,)).fetchone()
        actual = dict(row) if row else None
        if actual == before:
            return
        if actual != after:
            raise ValueError('rollback_row_conflict')
        changed = db.execute('UPDATE analyses SET '+','.join(k+'=?' for k in ROW_FIELDS)+
                             ' WHERE entry_id=? AND '+' AND '.join(k+' IS ?' for k in ROW_FIELDS),
                             (*(before[k] for k in ROW_FIELDS), target, *(after[k] for k in ROW_FIELDS))).rowcount
        row = db.execute('SELECT '+','.join(ROW_FIELDS)+' FROM analyses WHERE entry_id=?', (target,)).fetchone()
        if changed != 1 or dict(row) != before:
            raise ValueError('rollback_row_verification')


def vendor_restore(record, native_locked=False):
    from ops_runbooks import vendor_path
    path = vendor_path(record['target'])
    if not native_locked:
        fd = os.open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return vendor_restore(record, native_locked=True)
    before = base64.b64decode(record['before'], validate=True)
    raw = read(path)
    if raw == before:
        return
    observed = record.get('written_sha256')
    if not (observed and hashlib.sha256(raw).hexdigest() == observed) and json.loads(raw) != record['after']:
        raise ValueError('rollback_vendor_conflict')
    write(path, before)
    if read(path) != before:
        raise ValueError('rollback_vendor_verification')


def restore(record, native_locked=False):
    validate(record)
    try:
        if record['kind'] == 'vendor_schedule':
            vendor_restore(record, native_locked)
        else:
            row_restore(record)
    except BlockingIOError:
        return {'state': 'native_busy', 'verified': False}
    except Exception as exc:
        record.update(state='blocked', error_type=type(exc).__name__)
        save(record)
        return {'state': 'rollback_blocked', 'verified': False, 'error_type': type(exc).__name__}
    record.update(state='rolled_back', recovered_at=int(time.time()))
    record.pop('error_type', None)
    save(record)
    return {'state': 'rolled_back', 'verified': True, 'kind': record['kind']}


def recover():
    try:
        with locked():
            record = current()
            if not record or record['state'] in TERMINAL:
                return {'state': 'clean', 'verified': True}
            return restore(record)
    except BlockingIOError:
        return {'state': 'repair_running', 'verified': False}
    except Exception as exc:
        return {'state': 'rollback_blocked', 'verified': False, 'error_type': type(exc).__name__}


class Transaction:
    def __init__(self, record, native_locked):
        self.record = record
        self.native_locked = native_locked

    def commit(self):
        accepted = {**self.record, 'state': 'committed', 'completed_at': int(time.time())}
        save(accepted)
        self.record.update(accepted)

    def observe_written(self, raw):
        # Called while still holding the native vendor lock. A malformed writer
        # result can then be compensated by exact hash, without guessing later.
        if not self.native_locked or self.record['kind'] != 'vendor_schedule':
            raise ValueError('guard_observe_scope')
        observed = {**self.record, 'written_sha256': hashlib.sha256(raw).hexdigest()}
        save(observed)
        self.record.update(observed)

    def cancel(self):
        # Only call when the native CAS performed no writes.
        accepted = {**self.record, 'state': 'cancelled', 'completed_at': int(time.time())}
        save(accepted)
        self.record.update(accepted)


@contextmanager
def transaction(kind, target, before, after, native_locked=False):
    with locked():
        previous = current()
        if previous and previous['state'] not in TERMINAL:
            raise ValueError('unfinished_repair_blocks_mutation')
        if previous:
            write(HOME/'previous.json', read(HOME/'current.json'))
        if kind == 'vendor_schedule':
            before = base64.b64encode(before).decode('ascii')
        record = validate({'version': 1, 'state': 'pending', 'kind': kind, 'target': target,
                           'before': before, 'after': after, 'created_at': int(time.time())})
        save(record)  # Must be durable before the first business write.
        tx = Transaction(record, native_locked)
        try:
            yield tx
        finally:
            if record['state'] == 'pending':
                result = restore(record, native_locked)
                if result['state'] != 'rolled_back':
                    raise RuntimeError('rollback_requires_attention')


if __name__ == '__main__':
    if sys.argv[1:] != ['--recover']:
        raise SystemExit('fixed recovery mode only')
    outcome = recover()
    print(json.dumps(outcome, allow_nan=False))
    raise SystemExit(1 if outcome['state'] == 'rollback_blocked' else 0)
