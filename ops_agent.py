"""Cloud half of the Qwen operations sidecar; called under recovery-agent tick.lock."""
import json
from pathlib import Path
import sqlite3
import sys
import time

from ops_review import (OPS_HOME, OPS_UNITS, ops_read, ops_snapshot,
                        validate_operations_result)


def execute_decisions(decisions, report, state, agent, now, checkpoint):
    """Fixed native actions only. Model confidence never overrides local safeguards."""
    outcomes = []
    actions_considered = 0
    for decision in decisions:
        action, target = decision['action'], decision['target']
        outcome = {'action': action, 'target': target, 'state': 'recorded'}
        outcomes.append(outcome)
        if action == 'report':
            continue
        if action != 'inspect_production_case':
            from repair_guard import recover
            recovery = recover()
            if recovery['state'] not in {'clean', 'rolled_back'}:
                outcome.update(state='unfinished_repair_no_action', recovery=recovery)
                continue
        if actions_considered >= 1:
            outcome['state'] = 'per_tick_action_limit_no_action'
            continue
        actions_considered += 1
        if decision['confidence'] < .9:
            outcome['state'] = 'low_confidence_no_action'
            continue
        if report.get('disk', {}).get('low') is not False:
            outcome['state'] = 'low_disk_no_action'
            continue
        if action == 'restart_service':
            record = state.get('services', {}).get(target, {})
            if record.get('failures', 0) < 3:
                outcome['state'] = 'not_persistent_no_action'
                continue
            if target == 'ai-news-web.service' and report['services'].get('ai-news-miniflux.service') != 'healthy':
                outcome['state'] = 'dependency_failed_no_action'
                continue
            url = dict(agent.HEALTH)[target]
            try:
                body = agent.request(url)
                http_good = not isinstance(body, dict) or (body.get('ready', True) is not False and body.get('success', True) is not False)
            except Exception:
                http_good = False
            metrics = agent.metric_health(now) if target == 'public-ai-newapi.service' else {}
            good = agent.service_health(target, http_good, metrics)
            active = agent.command('systemctl', '--user', 'is-active', target).stdout.strip()
            def restart():
                checkpoint()  # The native function reserves its budget before this callback.
                return agent.command('systemctl', '--user', 'restart', target).returncode == 0
            outcome['state'] = agent.service_step(record, good, active, now, True, restart)
            if outcome['state'] == 'restart_requested':
                try:
                    body = agent.request(url)
                    healthy = not isinstance(body, dict) or (body.get('ready', True) is not False and body.get('success', True) is not False)
                    metrics = agent.metric_health(time.time()) if target == 'public-ai-newapi.service' else {}
                    outcome['verified_healthy'] = agent.service_health(target, healthy, metrics)
                except Exception:
                    outcome['verified_healthy'] = False
        elif action == 'retry_feeds':
            sys.path.insert(0, str(agent.SOURCE))
            from initialize_secrets import read_env
            headers = {'X-Auth-Token': read_env('ai.env')['MINIFLUX_API_KEY']}
            feeds = agent.request(agent.FEEDS, headers=headers)
            outcome['result'] = agent.feeds_step(feeds, state.setdefault('feeds', {}), now, True,
                lambda fid: agent.request(agent.FEEDS+f'/{fid}/refresh', 'PUT', headers),
                lambda fid: agent.request(agent.FEEDS+f'/{fid}', headers=headers), checkpoint)
            outcome['state'] = 'native_feed_retry_checked'
        else:
            from ops_runbooks import execute_runbook
            outcome.update(execute_runbook(decision, state, checkpoint, now, agent))
    return outcomes


def qwen_tick(report, state, agent, checkpoint):
    """Bounded discovery, native config guard, immutable inputs, write-ahead receipt."""
    now = time.time()
    settings = ops_read(OPS_HOME/'qwen-settings.json', 4096)
    if settings.get('enabled') is not True:
        return {'state': 'disabled'}
    config_path = agent.ROOT/'src/kaggle_batch/cloud-config-month-primary.json'
    config = ops_read(config_path)
    root = Path(config['state_root'])
    if root.resolve() != Path(settings['state_root']).resolve():
        raise ValueError('designated_lane_changed')
    sys.path.insert(0, str(agent.SOURCE/'kaggle_batch'))
    from dispatch_policy import ConfigGuard
    from batch_control import digest
    guard = ConfigGuard(config_path, config)
    guard()
    qstate = state.setdefault('qwen_operations', {'history': [], 'receipts': {}})
    history = qstate.setdefault('history', [])
    # Store only sanitized projected reports; this is also the bounded log history.
    from ops_review import ops_projection
    if not history:
        try:
            with (OPS_HOME/'events.jsonl').open('rb') as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell()-128*1024))
                lines = stream.read(128*1024).splitlines()[-12:]
            for line in lines:
                try:
                    previous = json.loads(line)
                    if isinstance(previous, dict):
                        history.append(ops_projection(previous))
                except (ValueError, TypeError, AttributeError):
                    continue
        except OSError:
            pass
    history.append(ops_projection(report))
    del history[:-12]
    # ops_snapshot expects native-shaped reports, so build the current snapshot and
    # directly attach already-projected historical events before sealing it.
    from ops_runbooks import collect_incidents
    incidents = collect_incidents(report, now)
    attempted = qstate.setdefault('problem_attempts', {})
    present = {i['fingerprint'] for i in incidents}
    for key in list(attempted):
        if key not in present:
            attempted.pop(key)
    incidents = [i for i in incidents if now-attempted.get(i['fingerprint'], 0) >= 3600]
    snapshot = ops_snapshot(report, [], now, incidents=incidents[:10])
    snapshot['recent_log_checks'] = history[:-1]
    from ops_review import ops_hash
    snapshot['evidence_hash'] = ops_hash({k: v for k, v in snapshot.items() if k != 'evidence_hash'})
    agent.atomic(OPS_HOME/'qwen-evidence.json', snapshot)
    receipts = qstate.setdefault('receipts', {})
    # Reviewed campaign decisions survive the one-mutation-per-tick limit.
    # Reserve each item before effects: a crash cannot replay it.
    drained = []
    for batch_id, receipt in receipts.items():
        pending = receipt.get('pending_decisions', [])
        if not pending:
            continue
        decision = pending.pop(0)
        receipt.setdefault('execution_reservations', []).append({'at': int(now), 'target': decision['target']})
        checkpoint()
        guard()
        try:
            outcomes = execute_decisions([decision], report, state, agent, now, checkpoint)
        except Exception as exc:
            outcomes = [{'action': decision['action'], 'target': decision['target'],
                         'state': 'failed', 'verified': False, 'error_type': type(exc).__name__}]
        receipt['outcomes'].extend(outcomes)
        checkpoint()
        agent.atomic(root/batch_id/'operations-execution.json', receipt)
        drained.append({'batch_id': batch_id, 'outcomes': outcomes})
        break
    with sqlite3.connect((root/'batches.sqlite3').resolve().as_uri()+'?mode=ro', uri=True, timeout=3) as db:
        rows = db.execute("SELECT id,manifest_hash FROM batches WHERE remote_status='COMPLETE' AND state IN ('downloaded','imported','resolved') ORDER BY updated DESC LIMIT 24").fetchall()
    received = []
    for batch_id, expected_hash in reversed(rows):
        if batch_id in receipts:
            continue
        folder = root/batch_id
        if folder.is_symlink() or folder.resolve().parent != root.resolve():
            raise ValueError('batch_path')
        output = folder/'output'
        if output.is_symlink() or not (output/'operations-review.json').exists():
            continue
        manifest = ops_read(folder/'manifest.json', 32*1024*1024)
        if 'operations_review' not in manifest:
            continue
        canonical = {k: v for k, v in manifest.items() if k not in {'batch_id', 'manifest_hash'}}
        if manifest.get('batch_id') != batch_id or manifest.get('manifest_hash') != expected_hash or digest(canonical) != expected_hash:
            raise ValueError('immutable_manifest_changed')
        receipt = {'at': int(now), 'state': 'reserved', 'outcomes': []}
        receipts[batch_id] = receipt
        checkpoint()  # Persist at-most-once reservation BEFORE parsing or execution.
        agent.atomic(folder/'operations-execution.json', receipt)
        try:
            result = ops_read(output/'operations-review.json', 128*1024)
            # v1 output has no independent review and cannot execute after upgrade.
            decisions, skipped = validate_operations_result(manifest, result, now)
            guard()  # Pause/config changes apply to model actions too.
            receipt['model'] = manifest['model']['filename']
            receipt['decisions'] = decisions
            receipt['evidence_hash'] = manifest['operations_review']['evidence_hash']
            receipt['skipped'] = skipped
            for incident in manifest['operations_review']['incidents']:
                attempted[incident['fingerprint']] = now
            checkpoint()
            campaign = manifest['operations_review'].get('protocol_revision', 2) >= 4 and any(
                d['action'] in {'repair_production_case', 'inspect_production_case'} for d in decisions)
            if campaign or drained:
                receipt['pending_decisions'] = decisions if drained else decisions[1:]
                checkpoint()
                receipt['outcomes'] = [] if drained else execute_decisions(decisions[:1], report, state, agent, now, checkpoint)
            else:
                receipt['outcomes'] = execute_decisions(decisions, report, state, agent, now, checkpoint)
            receipt['state'] = 'processed'
        except Exception as exc:
            receipt.update(state='rejected_or_failed', error_type=type(exc).__name__)
        checkpoint()
        agent.atomic(folder/'operations-execution.json', receipt)
        received.append({'batch_id': batch_id, 'state': receipt['state'], 'outcomes': receipt['outcomes']})
    # Old results cannot execute again: their evidence expires within an hour.
    for key in sorted(receipts, key=lambda key: receipts[key]['at'])[:-64]:
        receipts.pop(key)
    latest = max(receipts.values(), key=lambda row: row['at'], default=None)
    return {'state': 'enabled', 'protocol_version': 2, 'lane': 'primary', 'snapshot_at': snapshot['created_at'],
            'eligible_incidents': len(snapshot['incidents']), 'receipts_retained': len(receipts),
            'processed_total_retained': sum(row['state']=='processed' for row in receipts.values()),
            'last_result': {k:latest[k] for k in ('at','state','error_type','outcomes','skipped') if k in latest} if latest else None,
            'received': received, 'drained': drained,
            'pending_decisions': sum(len(r.get('pending_decisions', [])) for r in receipts.values())}
