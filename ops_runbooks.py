"""Trusted low-risk functions. No model code, shell, free paths, URLs or arbitrary args."""
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

VENDOR_SOURCE = Path('/home/ubuntu/ai-news/src/vendor_sources.py')
VENDOR_CONFIG = VENDOR_SOURCE.with_name('vendor_sources.json')
VENDOR_STATES = Path('/home/ubuntu/ai-news/state/vendor-adapters')
MAINTENANCE = Path('/home/ubuntu/cloud-maintenance')
BACKUPS = Path('/home/ubuntu/ai-news/.private/recovery-agent/runbook-backups')
LOG_CONFIG = MAINTENANCE/'newapi-logrotate.conf'
APPROVED_LOG_CONFIG = MAINTENANCE/'approved-newapi-logrotate.conf'
CANARY = Path('/home/ubuntu/ai-news/.private/recovery-agent/qwen-canary')
# Hash of the approved template shipped with this release; no model-supplied config.
LOG_TEMPLATE = b'''/home/ubuntu/public-ai-pool/logs/newapi/oneapi-*.log {
    daily
    maxsize 4M
    rotate 3
    maxage 14
    missingok
    notifempty
    copytruncate
    compress
}
'''
TEMPLATE_SHA256 = hashlib.sha256(LOG_TEMPLATE).hexdigest()


def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def state_bytes(path):
    with path.open('rb') as stream:
        raw = stream.read(2*1024*1024+1)
    if len(raw) > 2*1024*1024:
        raise ValueError('state_size')
    return raw


def vendor_keys():
    configs = json.loads(VENDOR_CONFIG.read_text())
    return {c['key'] for c in configs if re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', c['key'])}


def vendor_path(key):
    if key not in vendor_keys():
        raise ValueError('unapproved_vendor')
    path = VENDOR_STATES/(key+'.json')
    if VENDOR_STATES.is_symlink() or path.is_symlink() or path.resolve().parent != VENDOR_STATES.resolve():
        raise ValueError('state_path')
    return path


def invalid_schedule(value):
    return type(value) not in (int, float) or not math.isfinite(value) or value < 0


def incident(action, target, code, precondition=None):
    from ops_protocol import protocol_hash
    identity = {'action': action, 'target': target, 'code': code}
    return {'id': action+':'+target, 'fingerprint': protocol_hash(identity), 'target': target,
            'code': code, 'allowed_actions': [action], 'precondition': precondition or {}}


def collect_incidents(report, now):
    """No raw errors or file contents leave the server. Repair evidence is hash-only."""
    from ops_review import OPS_UNITS
    from production_cases import case_incidents
    incidents = case_incidents(now)
    try:
        marker = canary_marker(now)
        if marker and not (CANARY/'newapi-logrotate.conf').exists():
            incidents.append(incident('restore_logrotate_config','ops_canary','missing_logrotate_config',
                {'state_sha256':marker[1]}))
    except (OSError, ValueError, TypeError):
        pass
    if not LOG_CONFIG.exists() and not LOG_CONFIG.is_symlink():
        incidents.append(incident('restore_logrotate_config', 'newapi', 'missing_logrotate_config'))
    try:
        for key in sorted(vendor_keys()):
            path = vendor_path(key)
            raw = state_bytes(path)
            state = json.loads(raw)
            schedule = state.get('next_run_at', 0)
            if invalid_schedule(schedule):
                incidents.append(incident('repair_vendor_schedule', key, 'invalid_schedule', {'state_sha256': digest_bytes(raw)}))
                continue
            success = state.get('last_success_at', 0)
            stale = invalid_schedule(success) or now-success > 12*3600
            if (stale or state.get('last_error')) and schedule <= now:
                incidents.append(incident('refresh_vendor_source', key, 'source_stale_or_error'))
    except (OSError, ValueError, KeyError, TypeError):
        pass  # Unknown/corrupt business content is never replaced with an empty state.
    for unit in OPS_UNITS:
        if report.get('services', {}).get(unit) in {'confirming', 'restart_failed', 'restart_limited', 'would_restart'}:
            incidents.append(incident('restart_service', unit, 'service_unhealthy'))
    if report.get('feeds', {}).get('stalled', 0):
        incidents.append(incident('retry_feeds', 'stalled_feeds', 'feeds_stalled'))
    logs = report.get('newapi_logs', {})
    if logs.get('remaining_bytes', 0) > 64*1024*1024:
        incidents.append(incident('rotate_logs', 'newapi', 'log_retention_pressure'))
    return incidents[:32]


def load_vendor_module():
    spec = importlib.util.spec_from_file_location('qwen_trusted_vendor', VENDOR_SOURCE)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def backup_state(key, raw, ledger, checkpoint):
    # Two bounded slots per configured source, <=4 MiB/source. Originals aren't erased.
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    if BACKUPS.is_symlink(): raise ValueError('backup_path')
    sequence = ledger.get('backup_sequence', 0)+1
    ledger['backup_sequence'] = sequence
    checkpoint()
    destination = BACKUPS/(key+'.'+str(sequence % 2)+'.json')
    temporary = destination.with_suffix('.pending')
    with temporary.open('wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    temporary.chmod(0o600); temporary.replace(destination)
    return str(destination)


def repair_vendor_schedule(key, expected, ledger, checkpoint, now):
    path = vendor_path(key)
    lock_path = path.with_suffix('.lock')
    if lock_path.is_symlink(): raise ValueError('lock_path')
    module = load_vendor_module()
    with lock_path.open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {'state': 'busy_no_action'}
        raw = state_bytes(path)
        if digest_bytes(raw) != expected:
            return {'state': 'source_changed_no_action'}
        before = json.loads(raw)
        if not invalid_schedule(before.get('next_run_at', 0)):
            return {'state': 'already_valid_no_action'}
        # Validate all existing entries, URLs and dates using the native loader.
        module.load(key)
        original_fields = json.dumps({k:v for k,v in before.items() if k != 'next_run_at'}, sort_keys=True, allow_nan=False)
        backup = backup_state(key, raw, ledger, checkpoint)
        after = {**before, 'next_run_at': int(now)}
        # No other field, counter, claim, entry or URL may change.
        if original_fields != json.dumps({k:v for k,v in after.items() if k != 'next_run_at'}, sort_keys=True, allow_nan=False):
            raise ValueError('field_scope')
        if state_bytes(path) != raw: return {'state': 'source_changed_no_action'}
        from repair_guard import transaction, sync_dir
        with transaction('vendor_schedule', key, raw, after, native_locked=True) as tx:
            module.save(key, after)
            sync_dir(path.parent)
            written = state_bytes(path)
            tx.observe_written(written)
            if json.loads(written) != after:
                raise ValueError('post_write_verification')
            module.load(key)
            tx.commit()
        return {'state': 'repaired', 'verified': True, 'backup': backup,
                'before_sha256': digest_bytes(raw), 'after_sha256': digest_bytes(state_bytes(path))}


def canary_marker(now):
    if CANARY.is_symlink(): raise ValueError('canary_path')
    path = CANARY/'operator-fault.json'
    if path.is_symlink() or not path.exists(): return None
    with path.open('rb') as stream: raw=stream.read(4097)
    if len(raw)>4096:raise ValueError('canary_marker_size')
    data=json.loads(raw)
    if not isinstance(data,dict):raise ValueError('canary_marker_shape')
    if data.get('armed') is not True:return None
    complete=CANARY/'probe-complete.json'
    if complete.exists() and not complete.is_symlink():
        with complete.open('rb') as stream:completed=json.loads(stream.read(4096))
        if completed.get('run_id')==data.get('run_id') and completed.get('verified') is True:return None
    created,deadline=data.get('created_at'),data.get('deadline')
    if type(created) is not int or type(deadline) is not int or not created<=now<=deadline or not 0<deadline-created<=7200:
        return None
    return data,digest_bytes(raw)


def restore_log_config(target='newapi', expected_marker=None):
    destination,approved=LOG_CONFIG,APPROVED_LOG_CONFIG
    if target=='ops_canary':
        marker=canary_marker(time.time())
        if not marker or marker[1]!=expected_marker: return {'state':'canary_changed_or_expired_no_action'}
        destination,approved=CANARY/'newapi-logrotate.conf',CANARY/'approved-newapi-logrotate.conf'
    elif target!='newapi':raise ValueError('unapproved_log_target')
    if destination.exists() or destination.is_symlink():
        return {'state': 'existing_config_preserved'}
    if MAINTENANCE.is_symlink() or approved.is_symlink():
        raise ValueError('config_path')
    raw = approved.read_bytes()
    if digest_bytes(raw) != TEMPLATE_SHA256:
        raise ValueError('approved_template_changed')
    # Publish complete bytes atomically. A killed writer cannot leave half a config.
    pending = destination.with_name(destination.name+'.restore-pending')
    from repair_guard import sync_dir, write
    write(pending, raw)  # Replace the old staging inode; it may share a live hard link.
    try:
        os.link(pending, destination, follow_symlinks=False)
    except FileExistsError:
        return {'state': 'existing_config_preserved'}
    sync_dir(destination.parent)
    return {'state': 'repaired', 'verified': destination.read_bytes() == LOG_TEMPLATE,
            'scope':target,'after_sha256':digest_bytes(destination.read_bytes())}


def refresh_vendor(key, now):
    path = vendor_path(key)
    raw = state_bytes(path)
    state = json.loads(raw); due = state.get('next_run_at', 0)
    if invalid_schedule(due) or due > now: return {'state': 'native_backoff_preserved'}
    success = state.get('last_success_at', 0)
    if not invalid_schedule(success) and now-success <= 12*3600 and not state.get('last_error'):
        return {'state': 'already_healthy_no_action'}
    result = subprocess.run(['/home/ubuntu/ai-news/runtime/venv/bin/python', str(VENDOR_SOURCE),
                             '--refresh', '--source', key], timeout=60, capture_output=True, text=True,
                            env={**os.environ, 'VENDOR_OUTBOUND_PROXY': 'http://127.0.0.1:17890'})
    current = json.loads(state_bytes(path))
    current_success = current.get('last_success_at', 0)
    previous_success = 0 if invalid_schedule(success) else success
    verified = not invalid_schedule(current_success) and current_success > previous_success and not current.get('last_error')
    return {'state': 'refreshed' if verified else 'not_recovered', 'verified': verified,
            'returncode': result.returncode}


def execute_runbook(decision, state, checkpoint, now, agent):
    action, target = decision['action'], decision['target']
    record = state.setdefault('qwen_runbooks', {}).setdefault(action+':'+target, {})
    previous = record.get('last_attempt', 0)
    if previous and now-previous < 3600:
        return {'state': 'runbook_hourly_limit_no_action'}
    record['last_attempt'] = now
    checkpoint()  # Failed commands and process kills consume the same budget.
    if action in {'repair_production_case', 'inspect_production_case'}:
        from production_cases import execute_case
        return execute_case(decision, now)
    if action == 'repair_vendor_schedule':
        return repair_vendor_schedule(target, decision['incident']['precondition']['state_sha256'], record, checkpoint, now)
    if action == 'restore_logrotate_config' and target == 'newapi':
        return restore_log_config()
    if action == 'restore_logrotate_config' and target == 'ops_canary':
        return restore_log_config(target,decision['incident']['precondition']['state_sha256'])
    if action == 'refresh_vendor_source':
        return refresh_vendor(target, now)
    if action == 'rotate_logs' and target == 'newapi':
        return {'state': 'native_rotation_checked', 'result': agent.log_step(now, True)}
    raise ValueError('unknown_runbook')
