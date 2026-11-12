"""Per-task failure isolation. Never infer absence or release an article claim."""
import json
from pathlib import Path
import time

from batch_control import ROW_FIELDS, RECEIPT_ERRORS, TERMINAL, atomic_json, digest
from recovery_policy import ACCOUNT_ERRORS, ProviderError, backoff, exception_code

ACTIVE = {'submitting','submitted','running','submit_unknown'}
DONE = {'imported','resolved','retired','quarantined'}


def failure_scope(exc):
    code = exception_code(exc)
    return 'account' if code in ACCOUNT_ERRORS or getattr(exc,'operation',None) == 'quota' else 'task'


def pending(control, batch_id, now=None):
    now = time.time() if now is None else now
    with control.db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_failure_context'").fetchone():
            return None
        record = db.execute('SELECT * FROM task_failure_context WHERE batch_id=?',(batch_id,)).fetchone()
        if not record or record['next_try'] <= now:
            return None
    return {'state':'task_retry_scheduled','scope':'task','batch_id':batch_id,
            'retry_at':record['next_try'],'code':record['code'],'failures':record['failures'],
            'gpu_resubmitted':False}


def success(control, batch_id):
    if not hasattr(control,'db'):
        return
    with control.db() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_failure_context'").fetchone():
            db.execute('UPDATE task_failure_context SET failures=0,first_at=0,next_try=0 WHERE batch_id=?',(batch_id,))
            # Only clear progress owned by this task policy; preserve native import deferrals.
            db.execute("UPDATE batch_progress SET failures=0,next_try=0 WHERE batch_id=? AND error LIKE 'task:%'",(batch_id,))


def recover(control, batch_id, exc, audit, *, now=None):
    now = time.time() if now is None else now
    code = exception_code(exc)
    if failure_scope(exc) == 'account':
        raise exc
    row = control.row(batch_id)
    if row['state'] in DONE:
        return {'state':row['state'],'batch_id':batch_id,'scope':'task','gpu_resubmitted':False}
    control.manifest(batch_id)  # No mutation/parking on a corrupt immutable manifest.
    operation = getattr(exc,'operation','unknown')
    if operation not in {'kernels.status','kernels.output','kernels.push','kernels.list','quota'}:
        operation = 'local_recovery'
    audit.append('task_failure_observed',batch_id=batch_id,code=code,operation=operation,
                 scope='task',error_type=type(exc).__name__)
    with control.db() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
        if not current or any(current[key] != row[key] for key in ROW_FIELDS):
            raise ProviderError('local_state')
        db.execute('''CREATE TABLE IF NOT EXISTS task_failure_context (
            batch_id TEXT PRIMARY KEY,manifest_hash TEXT NOT NULL,first_at REAL NOT NULL,
            last_at REAL NOT NULL,failures INTEGER NOT NULL,code TEXT NOT NULL,
            operation TEXT NOT NULL,next_try REAL NOT NULL)''')
        previous = db.execute('SELECT * FROM task_failure_context WHERE batch_id=?',(batch_id,)).fetchone()
        failures = previous['failures']+1 if previous and previous['manifest_hash']==row['manifest_hash'] else 1
        first_at = previous['first_at'] if previous and previous['failures'] else now
        retry_at = now + backoff(code,failures)
        db.execute('INSERT OR REPLACE INTO task_failure_context VALUES (?,?,?,?,?,?,?,?)',
                   (batch_id,row['manifest_hash'],first_at,now,failures,code,operation,retry_at))
        db.execute('INSERT OR REPLACE INTO batch_progress VALUES (?,?,?,?)',
                   (batch_id,retry_at,failures,'task:'+code))
    report = {'state':'task_retry_scheduled','scope':'task','batch_id':batch_id,'code':code,
              'operation':operation,'failures':failures,'retry_at':retry_at,'gpu_resubmitted':False}
    folder = control.root/batch_id
    no_receipt = (row['error'] not in RECEIPT_ERRORS and all(not (folder/name).exists() and not (folder/name).is_symlink()
                  for name in ('submit-receipt.json','submit-receipt.json.pending')))
    eligible = (row['state']=='submit_unknown' and row['remote_status'] is None and no_receipt
                and failures>=3 and now-first_at>=3600)
    if not eligible:
        report['action'] = ('terminal_output_deferred_new_work_allowed' if row['remote_status'] in TERMINAL
                            else 'observe_existing_job_only')
        return report
    # A healthy account is required before isolating an ambiguous old attempt.
    # It is NOT proof that the old remote notebook is absent.
    from quota_guard import query_client
    gate = query_client(control.client)
    if not gate['allowed']:
        return {**report,'action':'isolation_deferred_account_not_ready'}
    with control.db() as db:
        total = db.execute("SELECT COUNT(*) FROM batches WHERE state='quarantined'").fetchone()[0]
        recent = db.execute("SELECT COUNT(*) FROM batches WHERE state='quarantined' AND updated>=?",(now-86400,)).fetchone()[0]
    if total>=8 or recent>=2:
        audit.append('task_isolation_limited',batch_id=batch_id,code=code,scope='task')
        return {**report,'action':'isolation_budget_exhausted_attention_required'}
    expected = {key:row[key] for key in ROW_FIELDS}
    review = {'policy':'task-v2-observed-one-hour','expected':expected,'first_at':first_at,
              'failures':failures,'remote_absence_proven':False,'claims_released':False}
    path = folder/'automatic-task-isolation.json'
    if path.is_symlink():
        raise ProviderError('local_state')
    if path.exists():
        saved = json.loads(path.read_text())
        if saved.get('expected') != expected or saved.get('policy') != review['policy']:
            return {**report,'action':'previous_isolation_intent_requires_attention'}
    else:
        if path.is_symlink() or path.with_suffix('.json.pending').exists():
            raise ProviderError('local_state')
        control._admit()
        from batch_control import _publish_json_once
        _publish_json_once(path,review)
    audit.append('task_isolation_intent',batch_id=batch_id,expected=expected,
                 policy=review['policy'],remote_absence_proven=False,claims_released=False)
    control.quarantine_unknown(batch_id,expected,
        'Repeated task-only failures observed for at least one hour; preserve all claims and do not resubmit.',str(path),
        require_no_receipt=True)
    audit.append('task_isolated',batch_id=batch_id,scope='task',claims_released=False,remote_absence_proven=False)
    return {**report,'state':'quarantined','action':'isolated_claims_preserved',
            'remote_absence_proven':False,'claims_released':False}


def active_retry(control, now=None):
    """An actual in-flight job keeps its account slot while observations back off."""
    now = time.time() if now is None else now
    if not hasattr(control,'db'):
        return None
    with control.db() as db:
        row = db.execute("SELECT b.id,p.next_try FROM batches b LEFT JOIN batch_progress p ON p.batch_id=b.id "
                         "WHERE b.state IN ('submitting','submitted','running','submit_unknown') ORDER BY b.updated LIMIT 1").fetchone()
    if row and row['next_try'] and row['next_try']>now:
        return {'batch_id':row['id'],'retry_at':row['next_try']}
    return None
