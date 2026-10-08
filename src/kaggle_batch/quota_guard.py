"""Fail-closed admission for NEW Kaggle GPU work; recovery never needs quota."""
import json
import math
import os
import subprocess
import time
try:
    from .absence_proof import VERSION_WARNING
except ImportError:
    from absence_proof import VERSION_WARNING

RESERVE_HOURS = 1.0
MAX_AGE_SECONDS = 300


def admission(snapshot, *, now=None, checked_at=None):
    now = time.time() if now is None else now
    try:
        age = now - float(snapshot.get('checked_at') if checked_at is None else checked_at)
        remaining = float(snapshot['gpu']['remaining_hours'])
        valid = (snapshot.get('state') == 'ok' and not snapshot.get('stale')
                 and 0 <= age <= MAX_AGE_SECONDS and math.isfinite(remaining) and remaining >= 0)
    except (TypeError, ValueError, KeyError, AttributeError):
        valid, remaining = False, None
    if not valid:
        return {'allowed':False, 'state':'quota_unknown', 'remaining_hours':None,
                'reserve_hours':RESERVE_HOURS, 'reason':'fresh_quota_unavailable'}
    return {'allowed':remaining > RESERVE_HOURS,
            'state':'available' if remaining > RESERVE_HOURS else 'quota_reserved',
            'remaining_hours':remaining, 'reserve_hours':RESERVE_HOURS}


def query_client(client, *, now=None):
    """Each call invokes official CLI anew; stdout/errors never leave this function."""
    try:
        output = client(['quota','--format','json'], 20)
        first, separator, rest = output.partition('\n')
        # Accept only one complete official warning before the JSON payload.
        if separator and VERSION_WARNING.fullmatch(first.removesuffix('\r')):
            output = rest
        rows = json.loads(output)
        if not isinstance(rows,list):
            raise TypeError()
        gpu = [r for r in rows if isinstance(r,dict) and str(r.get('resource','')).strip().lower() == 'gpu']
        if len(gpu) != 1 or isinstance(gpu[0].get('remaining'),bool):
            raise ValueError()
        text = str(gpu[0]['remaining']).strip().lower()
        remaining = float(text.removesuffix('h'))
        checked = time.time() if now is None else now
        result = admission({'state':'ok', 'checked_at':checked,
                            'gpu':{'remaining_hours':remaining}}, now=checked)
        return {**result, 'checked_at':checked}
    except Exception:  # noqa: BLE001 -- all provider failures must block new GPU work without leaking errors
        # Provider error bodies may contain credentials/signed URLs. Never echo them.
        return {'allowed':False, 'state':'quota_unknown', 'remaining_hours':None,
                'reserve_hours':RESERVE_HOURS, 'reason':'fresh_quota_unavailable',
                'checked_at':time.time() if now is None else now}


def query_config(config, run=None, *, authorize=None):
    run = subprocess.run if run is None else run
    def client(args, timeout):
        if authorize is not None:authorize()
        result = run([config['kaggle_python'],'-m','kaggle',*args], capture_output=True,
                     text=True,encoding='utf-8',timeout=timeout,
                     env={**os.environ,'KAGGLE_API_TOKEN':config['token_file']})
        if result.returncode:
            raise ValueError('quota_command_failed')
        return result.stdout
    return query_client(client)


def may_start(lane):
    """A never-submitted prepared batch is new GPU work, not recovery."""
    if lane.get('schedule_enabled') is not True:return False
    outstanding = lane.get('outstanding')
    if outstanding and outstanding.get('state') != 'prepared':
        return True
    return lane.get('quota_gate',{}).get('allowed') is True


def plan_lanes(keys, lanes, due_count, cursor, max_active, batch_limit):
    active=sum(1 for lane in lanes.values() if lane['active'])
    slots=max(0,max_active-active)
    order=keys[cursor % len(keys):]+keys[:cursor % len(keys)]
    eligible=lambda key: not lanes[key]['active'] and lanes[key]['ready'] and may_start(lanes[key])
    reconcile=[key for key in order if eligible(key) and lanes[key]['outstanding']][:slots]
    slots-=len(reconcile)
    normal=[key for key in order if eligible(key) and not lanes[key]['outstanding']]
    normal=normal[:min(slots,(due_count+batch_limit-1)//batch_limit)]
    return reconcile+normal, (keys.index(normal[-1])+1)%len(keys) if normal else cursor
