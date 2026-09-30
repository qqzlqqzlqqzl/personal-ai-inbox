"""Drain pending work in finite cloud cycles, retaining progress between runs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from recovery_policy import ProviderError,effective_retry_at,exception_code,record_failure


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
        raise ProviderError('local_state')
    data=json.loads(result.stdout)
    if data.get('recovery_error'):
        raise ProviderError(data['recovery_error'])
    return data


def drain_once(config_path, config, control, call=bridge, sleep=time.sleep, clock=time.monotonic):
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
        if config.get('reconcile_only'):
            existing=control.outstanding()
            if not existing:
                return report('nothing_to_reconcile',gpu_started=False)
            if existing['state']=='prepared':
                return report('prepared_requires_worker',batch_id=existing['id'],gpu_started=False)
            prepared={'existing_batch':existing['id']}
        else:
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
        # Submitted/running jobs are intentionally polled slowly. Local crash
        # states and already-terminal jobs are reconciled immediately: waiting
        # another 11 minutes here used to make submit_unknown recovery needlessly
        # sluggish and contributed to claims looking permanently stuck.
        resume_state=control.row(batch)['state']
        if resume_state in {'submitted','running'}:
            report('resuming',batch_id=batch)
            sleep(min(660,max(0,deadline-clock())))
        elif resume_state!='prepared':
            report('reconciling',batch_id=batch,previous_state=resume_state)
        while clock()<deadline:
            outcome=call(config_path,'advance','--batch',batch,timeout=max(1,min(600,int(deadline-clock()))))
            if outcome.get('submission_blocked'):
                return report(outcome['quota_gate']['state'],batch_id=batch,
                              quota_gate=outcome['quota_gate'],gpu_started=False)
            report('processing',batch_id=batch,outcome=outcome)
            if outcome.get('state')=='local_retry_scheduled':
                if not config.get('drain_queue'):
                    return report('local_retry_scheduled',batch_id=batch,retry_at=outcome['retry_at'])
                report('batch_deferred',batch_id=batch,retry_at=outcome['retry_at'])
                break
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


def drain(config_path,config,control,call=bridge,sleep=time.sleep,clock=time.monotonic):
    if not config.get('exception_audit_root'):
        return drain_once(config_path,config,control,call,sleep,clock)
    from exception_audit import Audit
    from batch_control import atomic_json
    audit=Audit(config['exception_audit_root']);audit.prune()
    recovery=control.root/'recovery.json'
    deadline=clock()+int(config.get('cycle_timeout_seconds',19800))
    while deadline-clock()>=60:
        previous=json.loads(recovery.read_text()) if recovery.exists() else {}
        effective_retry=effective_retry_at(previous)
        remaining=effective_retry-time.time()
        if remaining>0:
            report={'state':'cooldown','at':time.time(),**previous,'effective_retry_at':effective_retry}
            atomic_json(control.root/'cycle-status.json',report)
            # No hours-long sleeping process occupying an active lane slot.
            # The persistent watchdog returns after the recorded cooldown.
            return report
        try:
            report=drain_once(config_path,{**config,'cycle_timeout_seconds':int(deadline-clock())},control,call,sleep,clock)
            atomic_json(recovery,{'failures':0,'retry_at':0,'at':time.time()})
            return report
        except Exception as exc:
            record=record_failure(control.root,exception_code(exc),audit,config['owner'])
            report={'state':'cooldown',**record}
            atomic_json(control.root/'cycle-status.json',report)
            return report
    return {'state':'cycle_window_complete'}


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
