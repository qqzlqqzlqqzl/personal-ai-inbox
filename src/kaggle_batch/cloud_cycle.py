"""Drain pending work in finite cloud cycles, retaining progress between runs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def timer_text(hours):
    if hours not in (6,12):
        raise ValueError('Only 6h or 12h scheduling is supported')
    slots=','.join(f'{hour:02d}' for hour in range(0,24,hours))
    return ('[Unit]\nDescription=Kaggle Inbox bounded batch\n\n[Timer]\n'
            f'OnCalendar=*-*-* {slots}:00:00\nPersistent=true\nRandomizedDelaySec=120\n'
            'Unit=ai-news-kaggle.service\n\n[Install]\nWantedBy=timers.target\n')


def bridge(config_path,*args,timeout):
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('cloud_bridge.py')),
                           '--config',str(config_path),*args],timeout=timeout,
                          capture_output=True,text=True,encoding='utf-8')
    if result.returncode:
        # Network errors may contain signed URLs; do not echo them to scheduler logs.
        raise RuntimeError('Cloud bridge command failed: '+args[0])
    return json.loads(result.stdout)


def drain(config_path, config, control, call=bridge, sleep=time.sleep, clock=time.monotonic):
    from batch_control import atomic_json
    duration=int(config.get('cycle_timeout_seconds',19800))
    if not 60<=duration<=19800:
        raise ValueError('Cycle must fit within the six-hour schedule')
    deadline=clock()+duration
    completed=0
    previous_skip=None
    def report(state, **extra):
        value={'state':state,'completed_batches':completed,'at':time.time(),**extra}
        atomic_json(control.root/'cycle-status.json',value)
        print(json.dumps(value),flush=True)
        return value
    while clock()<deadline:
        report('preparing')
        prepared=call(config_path,'prepare','--limit',str(config['batch_limit']),
                      timeout=max(1,min(7200,int(deadline-clock()))))
        if prepared.get('state')=='retired_manifest_requires_new_attempt':
            return report(prepared['state'],batch_id=prepared['batch_id'],gpu_started=False,recovery_required=True)
        batch=prepared.get('batch_id') or prepared.get('existing_batch')
        if not batch:
            if prepared.get('quota_gate',{}).get('allowed') is False:
                return report(prepared['quota_gate']['state'],quota_gate=prepared['quota_gate'],gpu_started=False)
            fingerprint=prepared.get('skipped_fingerprint')
            if config.get('drain_queue') and prepared.get('considered') and fingerprint!=previous_skip:
                previous_skip=fingerprint
                report('skipped_unavailable_sources',skipped=prepared.get('skipped',0))
                continue
            retry_at=prepared.get('next_retry_at')
            if config.get('drain_queue') and retry_at:
                report('waiting_for_retry',next_retry_at=retry_at)
                if retry_at-time.time()<deadline-clock():
                    sleep(min(660,max(0,deadline-clock())))
                    continue
                return report('retry_after_cycle_window',next_retry_at=retry_at)
            return report('no_progress' if prepared.get('considered') else 'empty',gpu_started=False)
        previous_skip=None
        # On resume, always wait a full interval before observing the remote job.
        if control.row(batch)['state']!='prepared':
            report('resuming',batch_id=batch)
            sleep(min(660,max(0,deadline-clock())))
        while clock()<deadline:
            outcome=call(config_path,'advance','--batch',batch,timeout=max(1,min(600,int(deadline-clock()))))
            if outcome.get('submission_blocked'):
                return report(outcome['quota_gate']['state'],batch_id=batch,
                              quota_gate=outcome['quota_gate'],gpu_started=False)
            report('processing',batch_id=batch,outcome=outcome)
            if control.row(batch)['state']=='retired':
                return report('retired_missing_remote',batch_id=batch,gpu_started=False,recovery_required=True)
            if control.row(batch)['state'] in {'imported','resolved'}:
                completed+=1
                break
            if outcome.get('invalid') or outcome.get('missing_ids') or any(
                state not in ('imported','already_imported','existing_result_preserved')
                for state in outcome.get('import_states',{})):
                raise RuntimeError('Batch requires selective recovery')
            sleep(min(660,max(0,deadline-clock())))
        else:
            return report('observation_timeout',batch_id=batch)
        if not config.get('drain_queue'):
            return report('completed',batch_id=batch)
    return report('cycle_window_complete')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--render-timer',action='store_true')
    parser.add_argument('--manual',action='store_true',help='Explicit single run while the schedule remains disabled')
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    timer=timer_text(config['interval_hours'])
    if args.render_timer:
        print(timer,end='')
        return
    if not config.get('schedule_enabled',False) and not args.manual:
        print(json.dumps({'state':'schedule_disabled','gpu_started':False}))
        return
    import fcntl
    from batch_control import Controller
    root=Path(config['state_root'])
    os.umask(0o077)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (root/'cycle.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        control=Controller(root,config['owner'],kaggle_python=config['kaggle_python'])
        drain(args.config,config,control)


if __name__=='__main__':
    main()
