"""Bounded private recovery tick, with optional hash-bound Qwen diagnostics."""
import argparse
from datetime import datetime
import fcntl
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path('/home/ubuntu/ai-news')
SOURCE = Path('/home/ubuntu/website-release-20261004/reader-3349cf2-source/src')
HOME = ROOT / '.private/recovery-agent'
HEALTH = (
    ('ai-news-miniflux.service', 'http://127.0.0.1:8091/mf/healthcheck'),
    ('ai-news-rsshub.service', 'http://127.0.0.1:1200/healthz'),
    ('ai-news-web.service', 'http://127.0.0.1:8092/readyz'),
    ('public-ai-newapi.service', 'http://127.0.0.1:18767/api/status'),
)
FEEDS = 'http://127.0.0.1:8091/mf/v1/feeds'


def atomic(path, value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > 1024 * 1024:
        raise ValueError('state_size_limit')
    temporary = path.with_suffix('.pending')
    with temporary.open('wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def command(*args):
    return subprocess.run(args, timeout=25, capture_output=True, text=True,
                          env=dict(os.environ, XDG_RUNTIME_DIR='/run/user/1000',
                                   DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1000/bus'))


def request(url, method='GET', headers=None):
    # Explicitly bypass proxy environment for these loopback-only URLs.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(urllib.request.Request(url, method=method, headers=headers or {}), timeout=15) as response:
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('response_size_limit')
        return json.loads(data) if data.lstrip().startswith((b'{', b'[')) else None


def backoff(failures, base=1800, cap=86400):
    return min(cap, base * 2 ** min(max(0, failures - 1), 8))


def service_step(record, good, active, now, apply, restart):
    """Three separate ticks, ten-minute cooldown, at most two restarts/hour."""
    record['failures'] = 0 if good else record.get('failures', 0) + 1
    record['restarts'] = [t for t in record.get('restarts', []) if now - t < 3600]
    if good:
        return 'healthy'
    if active not in ('active', 'failed'):
        return 'stopped_no_action'
    if record['failures'] < 3:
        return 'confirming'
    if len(record['restarts']) >= 2 or now - record.get('last_restart', 0) < 600:
        return 'restart_limited'
    if not apply:
        return 'would_restart'
    # Reserve before attempting: a failed/timeout restart also consumes budget.
    record['last_restart'] = now
    record['restarts'].append(now)
    return 'restart_requested' if restart() else 'restart_failed'


def feeds_step(feeds, records, now, apply, refresh, verify, checkpoint):
    """Retry only native-stalled feeds; never change URLs/proxy/auth/settings."""
    live_ids = {str(f['id']) for f in feeds}
    for key in list(records):
        if key not in live_ids:
            records.pop(key)
    pending = []
    for feed in feeds:
        key = str(feed['id'])
        if feed.get('disabled') or not feed.get('parsing_error_count'):
            records.pop(key, None)
            continue
        if feed['parsing_error_count'] < 3:
            continue  # Native polling still owns ordinary transient errors.
        row = records.setdefault(key, {})
        if row.get('next_try', 0) <= now:
            pending.append((row.get('next_try', 0), feed, row))
    results = []
    # Oldest due first, max four sequential requests per five-minute tick.
    for _, feed, row in sorted(pending, key=lambda item: (item[0], item[1]['id']))[:4]:
        if not apply:
            results.append({'id': feed['id'], 'state': 'would_refresh'})
            continue
        row['attempts'] = row.get('attempts', 0) + 1
        restricted = re.search(r'\b(?:401|403|404|410)\b', feed.get('parsing_error_message', ''))
        row['next_try'] = now + backoff(row['attempts'], base=21600 if restricted else 1800)
        checkpoint()  # A process kill must not reset the retry budget.
        try:
            refresh(feed['id'])
            current = verify(feed['id'])
            # An accepted refresh alone is not proof of successful parsing.
            good = (current.get('checked_at') != feed.get('checked_at')
                    and current.get('parsing_error_count') == 0)
            result = 'recovered' if good else 'not_yet_recovered'
            if good:
                records.pop(str(feed['id']), None)
        except Exception as exc:
            result = type(exc).__name__
        results.append({'id': feed['id'], 'state': result})
    return {'stalled': sum(not f.get('disabled') and f.get('parsing_error_count', 0) >= 3 for f in feeds),
            'attempts': results}


def prune_logs(folder, protected, now, apply, max_bytes=64*1024*1024, max_files=32):
    """Only automatic log retention, never database/task/business-file cleanup."""
    files = sorted((p for p in folder.iterdir() if not p.is_symlink() and p.is_file()
                    and re.fullmatch(r'oneapi-\d{14}\.log(?:\.\d+(?:\.gz)?)?', p.name)),
                   key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    remaining = len(files)
    removed = 0
    for path in files:
        stat = path.stat()
        if path in protected or now-stat.st_mtime < 3600:
            continue
        if now-stat.st_mtime <= 14*86400 and total <= max_bytes and remaining <= max_files:
            continue
        if apply:
            path.unlink()  # User-authorized expiration of rolling logs only.
        total -= stat.st_size
        remaining -= 1
        removed += 1
    return {'expired_files': removed, 'remaining_bytes': total, 'remaining_files': remaining}


def log_step(now, apply):
    folder = Path('/home/ubuntu/public-ai-pool/logs/newapi')
    pid = int(command('systemctl', '--user', 'show', 'public-ai-newapi.service', '--property=MainPID', '--value').stdout.strip())
    if pid <= 0:
        return {'state': 'no_process_no_cleanup'}
    protected = set()
    for fd in (Path('/proc') / str(pid) / 'fd').iterdir():
        try:
            protected.add(Path(os.readlink(fd)))
        except FileNotFoundError:
            pass
    if apply:
        result = command('/usr/sbin/logrotate', '--state', str(HOME/'newapi-logrotate.state'),
                         '/home/ubuntu/cloud-maintenance/newapi-logrotate.conf')
        if result.returncode:
            return {'state': 'rotation_failed_no_cleanup'}
    return prune_logs(folder, protected, now, apply)


def source_health(now):
    result = {}
    configs = json.loads((ROOT/'src/vendor_sources.json').read_text())
    for config in configs:
        key = config['key']
        try:
            saved = json.loads((ROOT/f'state/vendor-adapters/{key}.json').read_text())
            error = saved.get('last_error')
            result[key] = {'stale': now-saved.get('last_success_at', 0) > 12*3600,
                           'error': error.get('category', error.get('type')) if error else None}
        except (ValueError, OSError, TypeError):
            result[key] = {'stale': True, 'error': 'unreadable_state'}
    return result


def finite_tree(value):
    if isinstance(value, dict):
        return all(finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(finite_tree(item) for item in value)
    return not isinstance(value, (float, int)) or math.isfinite(value)


def queue_step(state, now, apply, checkpoint):
    """Observe native per-task recovery; never run a competing isolation policy."""
    sys.path.insert(0, str(SOURCE / 'kaggle_batch'))
    from dispatch_policy import ConfigGuard
    configs = {key: json.loads((ROOT / f'src/kaggle_batch/cloud-config-month-{key}.json').read_text())
               for key in ('primary', 'secondary', 'third', 'fourth', 'fifth')}
    results = {}
    for key, config in configs.items():
        try:
            root = Path(config['state_root'])
            ConfigGuard(ROOT/f'src/kaggle_batch/cloud-config-month-{key}.json', config,
                        pause_file=ROOT/'runtime/qwen-month-20260925/paused.json')()
            with sqlite3.connect((root / 'batches.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=3) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM batches WHERE state NOT IN ('imported','retired','resolved','quarantined') ORDER BY updated LIMIT 1").fetchone()
                data = {'state':row['state'] if row else 'idle','recovery_policy':'native_task_v2'}
                if row and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_failure_context'").fetchone():
                    failed = db.execute('SELECT failures,next_try,code FROM task_failure_context WHERE batch_id=?',(row['id'],)).fetchone()
                    if failed:
                        data.update(task_failures=failed['failures'],task_retry_at=failed['next_try'],code=failed['code'])
            results[key] = data
        except Exception as exc:
            results[key] = {'state': 'guarded', 'error_type': type(exc).__name__}
    return results


def metric_health(now):
    with sqlite3.connect('file:/home/ubuntu/public-ai-pool/state/newapi/new-api.db?mode=ro', uri=True, timeout=3) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM system_instances ORDER BY last_seen_at DESC LIMIT 1').fetchone()
    seen = row['last_seen_at'] if row else None
    resources = None
    for value in dict(row or {}).values():
        if isinstance(value, str) and value.startswith('{'):
            data = json.loads(value)
            if isinstance(data, dict) and 'resources' in data:
                resources = data['resources']
    return {'fresh':seen is not None and 0<=now-seen<300,
            'finite':bool(resources) and finite_tree(resources)}


def service_health(unit, http_good, metrics):
    if unit=='public-ai-newapi.service' and 'fresh' in metrics and 'finite' in metrics:
        return http_good and metrics['fresh'] and metrics['finite']
    # An unreadable telemetry database alone is insufficient reason to restart.
    return http_good


def run(state, apply, checkpoint):
    now = time.time()
    report = {'at': datetime.now().astimezone().isoformat(), 'apply': apply, 'services': {}}
    if apply:
        from repair_guard import recover
        report['repair_recovery'] = recover()
        if report['repair_recovery']['state'] not in {'clean', 'rolled_back'}:
            report['attention'] = ['unfinished_repair_blocks_mutation']
            return report
    try:
        report['metrics']=metric_health(now)
    except Exception as exc:
        report['metrics']={'error_type':type(exc).__name__}
    services = state.setdefault('services', {})
    for unit, url in HEALTH:
        try:
            body = request(url)
            good = not isinstance(body, dict) or (body.get('ready', True) is not False and body.get('success', True) is not False)
        except Exception:
            good = False
        good=service_health(unit,good,report['metrics'])
        try:
            active = command('systemctl', '--user', 'is-active', unit).stdout.strip()
            dependencies_good = unit != 'ai-news-web.service' or report['services'].get('ai-news-miniflux.service') == 'healthy'
            def restart():
                checkpoint()
                return command('systemctl', '--user', 'restart', unit).returncode == 0
            report['services'][unit] = service_step(services.setdefault(unit, {}), good,
                                                   active if dependencies_good else 'dependency_failed', now, apply, restart)
        except Exception as exc:
            report['services'][unit] = type(exc).__name__
    usage = shutil.disk_usage(ROOT)
    report['disk'] = {'free_mib': usage.free // (1024 * 1024), 'low': usage.free < 1024**3 or usage.free / usage.total < .1}
    if not report['disk']['low']:
        try:
            sys.path.insert(0, str(SOURCE))
            from initialize_secrets import read_env
            headers = {'X-Auth-Token': read_env('ai.env')['MINIFLUX_API_KEY']}
            feeds = request(FEEDS, headers=headers)
            report['feeds'] = feeds_step(feeds, state.setdefault('feeds', {}), now, apply,
                lambda fid: request(FEEDS + f'/{fid}/refresh', 'PUT', headers),
                lambda fid: request(FEEDS + f'/{fid}', headers=headers), checkpoint)
        except Exception as exc:
            report['feeds'] = {'error_type': type(exc).__name__}
        report['queue'] = queue_step(state, now, apply, checkpoint)
    else:
        report['deferred'] = 'low_disk_no_feed_or_queue_mutations'
    try:
        report['newapi_logs'] = log_step(now, apply)
    except Exception as exc:
        report['newapi_logs'] = {'error_type': type(exc).__name__}
    try:
        report['sources'] = source_health(now)
    except Exception as exc:
        report['sources'] = {'error_type': type(exc).__name__}
    report['attention'] = [unit for unit, status in report['services'].items() if status != 'healthy']
    if not report['metrics'].get('fresh') or not report['metrics'].get('finite'):
        report['attention'].append('newapi_metrics_stale_or_invalid')
    if report['disk']['low']:
        report['attention'].append('disk_low')
    for section in ('feeds', 'newapi_logs'):
        if report.get(section, {}).get('error_type') or report.get(section, {}).get('state', '').endswith('no_cleanup'):
            report['attention'].append(section + '_check_failed')
    for lane, data in report.get('queue', {}).items():
        if data.get('state') in ('guarded', 'submit_unknown'):
            report['attention'].append('queue_' + lane + '_' + data['state'])
    if report.get('feeds', {}).get('stalled'):
        report['attention'].append('stalled_feeds_retrying')
    if any(isinstance(data, dict) and (data.get('stale') or data.get('error')) for data in report['sources'].values()):
        report['attention'].append('vendor_source_degraded')
    if report['sources'].get('error_type'):
        report['attention'].append('vendor_check_failed')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (HOME / 'tick.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"state":"busy"}')
            return
        path = HOME / 'state.json'
        try:
            state = json.loads(path.read_text()) if path.exists() else {}
        except (ValueError, OSError):
            # Fail closed; corrupt counters must never reset restart budgets.
            print('{"state":"invalid_state_no_action"}')
            raise SystemExit(1)
        checkpoint = lambda: atomic(path, state) if args.apply else None
        report = run(state, args.apply, checkpoint)
        if args.apply and (HOME/'qwen-settings.json').exists():
            try:
                from ops_agent import qwen_tick
                report['qwen_operations'] = qwen_tick(report, state, sys.modules[__name__], checkpoint)
            except Exception as exc:
                report['qwen_operations'] = {'state': 'guarded', 'error_type': type(exc).__name__}
        checkpoint()
        if args.apply:
            atomic(HOME / 'status.json', report)
            log = logging.getLogger('recovery')
            handler = RotatingFileHandler(HOME / 'events.jsonl', maxBytes=2*1024*1024, backupCount=3)
            log.addHandler(handler)
            log.setLevel(logging.INFO)
            log.info(json.dumps(report, ensure_ascii=True, allow_nan=False))
            handler.close()
        print(json.dumps(report, ensure_ascii=True, allow_nan=False))


if __name__ == '__main__':
    main()
