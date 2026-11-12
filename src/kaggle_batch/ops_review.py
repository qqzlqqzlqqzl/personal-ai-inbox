"""Hash-bound operations sidecar. Safe to embed in a private Kaggle runner."""
import hashlib
import json
import math
from pathlib import Path
import time
from ops_protocol import check_incident, run_operations_v2, validate_operations_v2

OPS_HOME = Path('/home/ubuntu/ai-news/.private/recovery-agent')
OPS_UNITS = ('ai-news-miniflux.service', 'ai-news-rsshub.service',
             'ai-news-web.service', 'public-ai-newapi.service')
OPS_STATES = {'healthy', 'confirming', 'restart_limited', 'restart_requested',
              'restart_failed', 'stopped_no_action', 'would_restart'}
OPS_CODES = {'auth', 'quota', 'capacity', 'rate_limit', 'network', 'protocol',
             'not_found', 'inaccessible', 'unknown', 'empty_output'}


def ops_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def ops_read(path, limit=65536):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('symlink_rejected')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('size_limit')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))


def ops_projection(report):
    """Explicit projection: no raw error strings, URLs, headers, IDs or request bodies."""
    services = {unit: (report.get('services', {}).get(unit)
                      if report.get('services', {}).get(unit) in OPS_STATES else 'unknown')
                for unit in OPS_UNITS}
    queue = {}
    for lane in ('primary', 'secondary', 'third', 'fourth', 'fifth'):
        row = report.get('queue', {}).get(lane, {})
        state = row.get('state')
        queue[lane] = {'state': state if state in {'idle', 'prepared', 'submitted', 'running',
                         'downloaded', 'submit_unknown', 'guarded'} else 'other',
                       'code': row.get('code') if row.get('code') in OPS_CODES else None}
    stalled = report.get('feeds', {}).get('stalled', 0)
    stalled = min(stalled, 10000) if type(stalled) is int and stalled >= 0 else 0
    metrics = {k: report.get('metrics', {}).get(k) is True for k in ('fresh', 'finite')}
    return {'services': services, 'queue': queue, 'metrics': metrics,
            'disk_low': report.get('disk', {}).get('low') is not False,
            'stalled_feeds': stalled,
            'degraded_source_count': sum(isinstance(v, dict) and bool(v.get('stale') or v.get('error'))
                                         for v in report.get('sources', {}).values())}


def ops_snapshot(report, history, now, incidents=None):
    evidence = {'version': 2, 'created_at': int(now), 'current': ops_projection(report),
                'protocol_revision': 4,
                'incidents': incidents or [],
                'recent_log_checks': [ops_projection(row) for row in history[-12:]]}
    return {**evidence, 'evidence_hash': ops_hash(evidence)}


def ops_valid_snapshot(snapshot, now):
    required = {'version', 'created_at', 'current', 'recent_log_checks', 'evidence_hash', 'incidents'}
    if not isinstance(snapshot, dict) or set(snapshot) not in (required, required | {'protocol_revision'}):
        raise ValueError('snapshot_shape')
    if snapshot.get('protocol_revision', 2) not in (2, 3, 4):
        raise ValueError('protocol_revision')
    created = snapshot['created_at']
    if type(created) is not int or not 0 <= now-created <= 7200 or snapshot['version'] != 2:
        raise ValueError('stale_snapshot')
    if ops_hash({k: v for k, v in snapshot.items() if k != 'evidence_hash'}) != snapshot['evidence_hash']:
        raise ValueError('snapshot_hash')
    # Re-projecting must produce exactly the same fixed, safe schema.
    def valid_projection(row):
        if not isinstance(row, dict) or set(row) != {'services', 'queue', 'metrics', 'disk_low', 'stalled_feeds', 'degraded_source_count'}:
            return False
        if set(row['services']) != set(OPS_UNITS) or any(v not in OPS_STATES | {'unknown'} for v in row['services'].values()):
            return False
        if type(row['disk_low']) is not bool or set(row['metrics']) != {'fresh', 'finite'} or any(type(v) is not bool for v in row['metrics'].values()):
            return False
        if any(type(row[k]) is not int or not 0 <= row[k] <= 10000 for k in ('stalled_feeds', 'degraded_source_count')):
            return False
        if set(row['queue']) != {'primary', 'secondary', 'third', 'fourth', 'fifth'}:
            return False
        return all(set(v) == {'state', 'code'} and v['state'] in {'idle', 'prepared', 'submitted', 'running', 'downloaded', 'submit_unknown', 'guarded', 'other'} and v['code'] in OPS_CODES | {None} for v in row['queue'].values())
    if not isinstance(snapshot['recent_log_checks'], list) or len(snapshot['recent_log_checks']) > 12:
        raise ValueError('snapshot_history')
    if not all(valid_projection(row) for row in [snapshot['current'], *snapshot['recent_log_checks']]):
        raise ValueError('unsafe_snapshot')
    if not isinstance(snapshot['incidents'], list) or len(snapshot['incidents']) > 32:
        raise ValueError('incident_limit')
    for incident in snapshot['incidents']:
        check_incident(incident)
        if snapshot.get('protocol_revision', 2) < 4 and any(
                a in {'repair_production_case', 'inspect_production_case'} for a in incident['allowed_actions']):
            raise ValueError('production_revision')
    if len({i['id'] for i in snapshot['incidents']}) != len(snapshot['incidents']):
        raise ValueError('duplicate_incident')
    return snapshot


def attach_operations(manifest, state_root):
    """Only the designated existing lane; no config change, claim or empty GPU job."""
    try:
        settings = ops_read(OPS_HOME/'qwen-settings.json', 4096)
        if settings.get('enabled') is not True or Path(settings['state_root']).resolve() != Path(state_root).resolve():
            return manifest
        snapshot = ops_valid_snapshot(ops_read(OPS_HOME/'qwen-evidence.json'), time.time())
        operations_only = not manifest.get('items') and valid_operations_campaign(snapshot, state_root, time.time())
        if (manifest.get('items') or operations_only) and snapshot['incidents']:
            import fcntl
            import os
            lock_path = OPS_HOME/'qwen-dispatch.lock'
            leases_path = OPS_HOME/'qwen-dispatched.json'
            if lock_path.is_symlink() or leases_path.is_symlink():
                return manifest
            with lock_path.open('a') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return manifest
                now = time.time()
                leases = ops_read(leases_path) if leases_path.exists() else {}
                if not isinstance(leases, dict) or any(not isinstance(k,str) or len(k)!=64 or type(v) not in (int,float) or not math.isfinite(v) for k,v in leases.items()):
                    return manifest
                leases = {k:v for k,v in leases.items() if 0 <= now-v < 3600}
                limit = 10 if snapshot.get('protocol_revision', 2) >= 4 else 2
                selected = [i for i in snapshot['incidents'] if i['fingerprint'] not in leases][:limit]
                if not selected:
                    return manifest
                for item in selected:
                    leases[item['fingerprint']] = now
                if len(leases)>64:
                    return manifest
                temporary = leases_path.with_suffix('.pending')
                with temporary.open('w') as stream:
                    json.dump(leases,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
                temporary.chmod(0o600);temporary.replace(leases_path)
                snapshot = {**snapshot, 'incidents': selected}
                snapshot['evidence_hash'] = ops_hash({k:v for k,v in snapshot.items() if k!='evidence_hash'})
                manifest = {**manifest, 'operations_review': snapshot}
                if operations_only:
                    manifest['operations_only'] = True
                    manifest['items'] = [{'id': 'operations-'+snapshot['evidence_hash'][:24],
                        'kind': 'operations_campaign', 'source_refs': [], 'input_hash': ops_hash(snapshot),
                        'messages': [{'role': 'system', 'content': 'Execute the hash-bound production operations campaign using its independent proposal/review protocol.'},
                                     {'role': 'user', 'content': json.dumps(snapshot, ensure_ascii=True)}]}]
    except (OSError, ValueError, KeyError, TypeError):
        pass  # Optional diagnostics must not block native reading batches.
    return manifest


def valid_operations_campaign(snapshot, state_root, now):
    """Only the explicit operator campaign can allocate a non-article GPU job."""
    try:
        data = ops_read(OPS_HOME/'production-campaign'/'active.json')
        created, deadline = data.get('created_at'), data.get('deadline')
        incidents = snapshot.get('incidents', [])
        if (data.get('armed') is not True or type(created) is not int or type(deadline) is not int
                or not created <= now <= deadline or not 0 < deadline-created <= 7200
                or Path(data.get('state_root', '')).resolve() != Path(state_root).resolve()
                or snapshot.get('protocol_revision') != 4 or len(incidents) != 10 or len(data.get('cases', {})) != 10):
            return False
        return all(i['target'].startswith('case_') and
                   data['cases'].get(i['target'][5:]) == i['precondition'].get('state_sha256') and
                   i['allowed_actions'] in (['repair_production_case'], ['inspect_production_case']) for i in incidents)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify_operations_campaign(manifest, evidence, output, now):
    """Native output verification, then zero article/database imports."""
    from ops_protocol import operations_campaign_receipt
    ops_valid_snapshot(manifest['operations_review'], now)
    result = ops_read(Path(output)/'operations-review.json', 128*1024)
    expected = operations_campaign_receipt(manifest, result)
    if evidence.get('missing_ids') or evidence.get('results') != [expected]:
        raise ValueError('operations_campaign_output')
    return {'scope': 'operations_campaign', 'attempts': len(result['attempts']), 'article_imports': 0}


def operator_probe_limit(requested, state_root):
    """One real article for an operator-armed probe; all native admission stays intact."""
    try:
        settings=ops_read(OPS_HOME/'qwen-settings.json',4096)
        if settings.get('enabled') is not True or Path(settings['state_root']).resolve()!=Path(state_root).resolve():
            return requested
        campaign = OPS_HOME/'production-campaign'/'active.json'
        if campaign.exists():
            data = ops_read(campaign)
            now = time.time()
            if (data.get('armed') is True and type(data.get('created_at')) is int
                    and type(data.get('deadline')) is int and data['created_at'] <= now <= data['deadline']
                    and 0 < data['deadline']-data['created_at'] <= 7200
                    and Path(data.get('state_root', '')).resolve() == Path(state_root).resolve()):
                evidence = ops_valid_snapshot(ops_read(OPS_HOME/'qwen-evidence.json'), now)
                leases = ops_read(OPS_HOME/'qwen-dispatched.json') if (OPS_HOME/'qwen-dispatched.json').exists() else {}
                if any(i['target'].startswith('case_') and not 0 <= now-leases.get(i['fingerprint'], 0) < 3600
                       for i in evidence['incidents']):
                    return min(requested, 1)
        home=OPS_HOME/'qwen-canary'
        if home.is_symlink() or (home/'newapi-logrotate.conf').exists():return requested
        marker=ops_read(home/'operator-fault.json',4096);now=time.time()
        created,deadline=marker.get('created_at'),marker.get('deadline')
        if marker.get('armed') is not True or type(created) is not int or type(deadline) is not int or not created<=now<=deadline or not 0<deadline-created<=7200:
            return requested
        complete=home/'probe-complete.json'
        if complete.exists() and ops_read(complete,4096).get('run_id')==marker.get('run_id'):return requested
        from ops_protocol import protocol_hash
        fingerprint=protocol_hash({'action':'restore_logrotate_config','target':'ops_canary','code':'missing_logrotate_config'})
        leases=ops_read(OPS_HOME/'qwen-dispatched.json') if (OPS_HOME/'qwen-dispatched.json').exists() else {}
        dispatched=leases.get(fingerprint)
        if type(dispatched) in (int,float) and 0<=now-dispatched<3600:return requested
        return min(requested,1)
    except (OSError,ValueError,KeyError,TypeError):
        return requested


def run_operations_review(manifest, post, output, available_seconds=None):
    snapshot = manifest.get('operations_review')
    if not snapshot:
        return
    ops_valid_snapshot(snapshot, time.time())
    seconds = available_seconds() if available_seconds else 1800
    return run_operations_v2(manifest, post, output, seconds)


def validate_operations_result(manifest, result, now):
    ops_valid_snapshot(manifest['operations_review'], now)
    return validate_operations_v2(manifest, result, now)
