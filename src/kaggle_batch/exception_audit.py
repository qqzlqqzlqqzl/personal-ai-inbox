"""Append-only, credential-free audit for the scheduler's existing call site."""
import json
import os
import time
from pathlib import Path

LANES = {'primary', 'secondary', 'third', 'fourth', 'fifth'}
ERRORS = {'TimeoutExpired', 'CalledProcessError', 'FileNotFoundError', 'PermissionError', 'OSError', 'SubprocessError'}


class SchedulerAudit:
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

"""Bounded private exception audit; never used for normal scoring payloads."""
import datetime
import json
import os
from pathlib import Path
import re
import time
import uuid
from urllib.parse import urlsplit,urlunsplit

MAX_BYTES=1024**3
MAX_AGE=30*86400
SEGMENT_BYTES=4*1024**2

def clean(value):
    if isinstance(value,dict):
        return {str(k):('[REDACTED]' if re.search(r'token|secret|password|authorization|cookie|api.?key',str(k),re.I)
                        else clean(v)) for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [clean(v) for v in value[:50]]
    if not isinstance(value,str):
        return value
    value=value[:8000]
    def url(m):
        try:
            p=urlsplit(m[0]);return urlunsplit((p.scheme,p.hostname or '',p.path,'',''))
        except ValueError:return '[URL]'
    value=re.sub(r'https?://[^\s<>"\x27]+',url,value)
    value=re.sub(r'(?i)\bBearer\s+\S+','Bearer [REDACTED]',value)
    value=re.sub(r'(?i)\b(?:apify_api_|KGAT_|sk-)[\w-]+','[REDACTED]',value)
    value=re.sub(r'(?i)((?:api[_-]?key|token|password|secret|authorization|cookie)\s*[=:]\s*)[^\s,;]+',r'\1[REDACTED]',value)
    return value

class RecoveryAudit:
    def __init__(self,root,max_bytes=MAX_BYTES,max_age=MAX_AGE,segment_bytes=SEGMENT_BYTES):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.max_bytes=max_bytes;self.max_age=max_age;self.segment_bytes=segment_bytes

    def _prune(self,now,reserve=0):
        files=sorted(self.root.glob('events-*.jsonl'))
        # Explicitly authorized rolling log disposal, not application data cleanup.
        for p in files:
            if not p.is_symlink() and now-p.stat().st_mtime>self.max_age:
                p.unlink()
        files=[p for p in files if p.exists() and not p.is_symlink()]
        total=sum(p.stat().st_size for p in files)
        while files and total+reserve>self.max_bytes:
            p=files.pop(0);total-=p.stat().st_size;p.unlink()

    def append(self,event,**fields):
        import fcntl  # Server-side cross-process lock shared by all four lanes.
        now=time.time()
        record={'at':now,'event':event,**clean(fields)}
        data=(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n').encode()
        if len(data)>65536 or len(data)>self.max_bytes:
            raise ValueError('Exception audit event exceeds size limit')
        with (self.root/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self._prune(now,len(data))
            day=datetime.datetime.fromtimestamp(now,datetime.timezone.utc).strftime('%Y%m%d')
            candidates=sorted(self.root.glob('events-'+day+'-*.jsonl'))
            target=candidates[-1] if candidates else None
            if target is None or target.is_symlink() or target.stat().st_size+len(data)>self.segment_bytes:
                target=self.root/f'events-{day}-{time.time_ns()}-{uuid.uuid4().hex[:8]}.jsonl'
            fd=os.open(target,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'ab') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())

    def prune(self):
        import fcntl
        with (self.root/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);self._prune(time.time())

class Audit:
    def __init__(self,root,**kwargs):
        self.scheduler=SchedulerAudit(root)
        self.recovery=RecoveryAudit(root,**kwargs)
    def append(self,event,**fields):
        if event=='scheduler_tick':return self.scheduler.append(event,**fields)
        return self.recovery.append(event,**fields)
    def prune(self):return self.recovery.prune()
