"""Stop admission shared by scheduler, workers and explicit local observation.

Only strict boolean schedule_enabled=True admits automatic work. Manual recovery
is an explicit single existing ID; it never grants new submission or queue drain.
"""
import json
import hashlib
import math
from pathlib import Path
import re
import sqlite3
try:
    from .queue_dispatch import DispatchBlocked, claimed_entries
    from .recovery_policy import effective_retry_at
except ImportError:
    from queue_dispatch import DispatchBlocked, claimed_entries
    from recovery_policy import effective_retry_at

PAUSE_FILE=Path(__file__).resolve().parents[2]/'runtime/qwen-month-20260925/paused.json'
FINISHED={'imported','retired','resolved'}

class DispatchStopped(Exception):
    STATES={'schedule_disabled','paused','reconcile_only','configuration_unavailable',
            'configuration_changed','manual_authorization_required',
            'manual_recovery_requires_existing_batch','manual_recovery_cannot_submit',
            'cooldown'}
    def __init__(self,state):
        self.state=state if state in self.STATES else 'configuration_unavailable'
        super().__init__(self.state)
    def report(self):
        return {'state':self.state,'gpu_started':False}

def load_config(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise ValueError()
        return value
    except (OSError,ValueError,TypeError,RecursionError):
        raise DispatchStopped('configuration_unavailable') from None

def paused(path=PAUSE_FILE):
    try:
        Path(path).stat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        raise DispatchStopped('configuration_unavailable') from None

def automatic_allowed(config):
    return (isinstance(config,dict) and config.get('schedule_enabled') is True
            and (config.get('reconcile_only') is False or config.get('reconcile_only') is None))

def require_automatic(config,pause_file=None):
    if paused(PAUSE_FILE if pause_file is None else pause_file):
        raise DispatchStopped('paused')
    if not isinstance(config,dict) or config.get('schedule_enabled') is not True:
        raise DispatchStopped('schedule_disabled')
    if config.get('reconcile_only') is True:
        raise DispatchStopped('reconcile_only')
    if config.get('reconcile_only') is not False and config.get('reconcile_only') is not None:
        raise DispatchStopped('configuration_unavailable')

def config_fingerprint(config):
    return hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class ConfigGuard:
    """Pin one exact config and reload it at each local/provider boundary."""
    def __init__(self,path,config,*,manual_recovery=False,pause_file=None,expected_fingerprint=None):
        self.path=Path(path).resolve()
        self.config=json.loads(json.dumps(config))
        self.fingerprint=config_fingerprint(self.config)
        if expected_fingerprint is not None and expected_fingerprint!=self.fingerprint:
            raise DispatchStopped('configuration_changed')
        self.manual_recovery=manual_recovery
        self.pause_file=PAUSE_FILE if pause_file is None else Path(pause_file)
    def __call__(self):
        current=load_config(self.path)
        if not self.manual_recovery:require_automatic(current,self.pause_file)
        if config_fingerprint(current)!=self.fingerprint:raise DispatchStopped('configuration_changed')

def local_rows(root,batch_id=None):
    """Strict existing self-ledger, no Controller, migration, status or provider."""
    root=Path(root)
    claimed_entries([root])
    if batch_id is not None and (not isinstance(batch_id,str) or
        not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}',batch_id)):
        raise DispatchStopped('manual_recovery_requires_existing_batch')
    db=None
    try:
        db=sqlite3.connect((root/'batches.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        sql='SELECT id,state,remote_status FROM batches'
        rows=db.execute(sql+' WHERE id=?' if batch_id else sql+' ORDER BY updated',
                        (batch_id,) if batch_id else ()).fetchall()
        if batch_id and not rows:raise DispatchStopped('manual_recovery_requires_existing_batch')
        next_try=0
        if batch_id and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_progress'").fetchone():
            retry=db.execute('SELECT next_try FROM batch_progress WHERE batch_id=?',(batch_id,)).fetchone()
            next_try=retry[0] if retry else 0
            if type(next_try) not in (int,float) or not math.isfinite(next_try) or next_try<0:
                raise DispatchBlocked('invalid_recovery')
        return [dict(row) for row in rows],next_try
    except sqlite3.Error:
        raise DispatchBlocked('unreadable_ledger') from None
    finally:
        if db is not None:db.close()

def recovery_retry(root):
    try:
        path=Path(root)/'recovery.json'
        value=json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(value,dict):raise ValueError()
        failures=value.get('failures',0)
        if type(failures) is not int or failures<0:raise ValueError()
        return effective_retry_at(value)
    except (OSError,ValueError,TypeError,RecursionError):
        raise DispatchBlocked('invalid_recovery') from None

def manual_target(root,batch_id,now):
    if not batch_id:raise DispatchStopped('manual_recovery_requires_existing_batch')
    rows,next_try=local_rows(root,batch_id)
    if rows[0]['state']=='prepared':raise DispatchStopped('manual_recovery_cannot_submit')
    retry=max(recovery_retry(root),next_try)
    # A scheduler local_state cooldown also applies to manual mutating recovery.
    path=Path(root)
    if path.name.startswith('kaggle-month-'):
        retry=max(retry,recovery_retry(path.parent/'kaggle-month-dispatch'))
    if retry>now:raise DispatchStopped('cooldown')
    return rows[0]

def readonly_reconcile(config,batch_id=None):
    rows,next_try=local_rows(config['state_root'],batch_id)
    return {'state':'readonly_reconciliation','scope':'self_ledger',
            'gpu_started':False,'batches':rows,
            'retry_at':max(next_try,recovery_retry(config['state_root']))}
