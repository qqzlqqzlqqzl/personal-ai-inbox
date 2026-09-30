"""Append-only, credential-free audit for the scheduler's existing call site."""
import json
import os
import time
from pathlib import Path

LANES = {'primary', 'secondary', 'third', 'fourth', 'fifth'}
ERRORS = {'TimeoutExpired', 'CalledProcessError', 'FileNotFoundError', 'PermissionError', 'OSError', 'SubprocessError'}


class Audit:
    def __init__(self, root):
        self.root = Path(root)

    def append(self, event, **fields):
        # Only the existing scheduler schema is accepted. Arbitrary diagnostic
        # payloads can contain secrets, signed URLs, or article text.
        if event != 'scheduler_tick':
            raise ValueError('unsupported_audit_event')
        started = [key for key in fields.get('started', []) if isinstance(key,str) and key in LANES]
        failures = []
        for value in fields.get('start_failures', []):
            if not isinstance(value, dict) or value.get('lane') not in LANES:
                continue
            error = str(value.get('error', ''))
            failures.append({'lane': value['lane'], 'error': error if error in ERRORS else 'Error'})
        record = {'event': event, 'at': time.time(), 'started': started, 'start_failures': failures}
        for name in ('due_unclaimed', 'due_claimed'):
            value = fields.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError('invalid_audit_count')
            record[name] = value
        if self.root.is_symlink():
            raise ValueError('audit_symlink_refused')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / 'events.jsonl'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.fchmod(fd, 0o600)
            data = (json.dumps(record, separators=(',', ':')) + '\n').encode()
            with os.fdopen(fd, 'ab', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        return record
