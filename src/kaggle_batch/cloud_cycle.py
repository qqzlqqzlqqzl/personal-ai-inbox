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


def bridge(config_path,*args,timeout,expected_config_sha256=None):
    if expected_config_sha256 is not None:
        args=(*args,'--expected-config-sha256',expected_config_sha256)
    result=subprocess.run([sys.executable,str(Path(__file__).with_name('cloud_bridge.py')),
                           '--config',str(config_path),*args],timeout=timeout,
                          capture_output=True,text=True,encoding='utf-8')
    if result.returncode:
        # Network errors may contain signed URLs; do not echo them to scheduler logs.
        raise ProviderError('local_state')
    data=json.loads(result.stdout)
    if data.get('recovery_error'):
        if data.get('state')=='dispatch_blocked':
            from queue_dispatch import DispatchBlocked
            error=DispatchBlocked(data.get('reason'))
            # Only a fixed ordinal is accepted, never an external peer/path.
            peer=data.get('peer','')
            if peer in {'peer_'+str(i) for i in range(100)}:error.peer=peer
            # Preserve the subprocess's one failure record; don't count the
            # same dispatch block twice in the supervising cycle.
            import math
            failures=data.get('failures');retry=data.get('retry_at');at=data.get('at')
            if (type(failures) is int and failures>0 and
                all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in (retry,at))):
                error.retry_record={'code':'local_state','failures':failures,'retry_at':retry,'at':at}
            raise error
        raise ProviderError(data['recovery_error'])
    return data


def drain_once(config_path, config, control, call=bridge, sleep=time.sleep, clock=time.monotonic, *, authorize=None, recovery_batch=None):
    from batch_control import atomic_json
    from dispatch_policy import require_automatic,DispatchStopped
    authorize=authorize or (lambda:require_automatic(config))
    def invoke(action,*args,timeout):
        kwargs={'timeout':timeout}
        if hasattr(authorize,'fingerprint'):kwargs['expected_config_sha256']=authorize.fingerprint
        return call(config_path,action,*args,**kwargs)
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
        authorize()
        report('preparing')
        if recovery_batch:
            existing=control.row(recovery_batch)
            if existing['state']=='prepared':raise DispatchStopped('manual_recovery_cannot_submit')
            prepared={'existing_batch':recovery_batch}
        elif config.get('reconcile_only') is True:
            from dispatch_policy import readonly_reconcile
            return readonly_reconcile(config)
        else:
            prepared=invoke('prepare','--limit',str(config['batch_limit']),
                          timeout=max(1,min(7200,int(deadline-clock()))))
        if prepared.get('state') in DispatchStopped.STATES:
            raise DispatchStopped(prepared['state'])
        authorize()
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
        if resume_state=='quarantined':
            return report('quarantined',batch_id=batch)
        if resume_state in {'submitted','running'}:
            report('resuming',batch_id=batch)
            sleep(min(660,max(0,deadline-clock())))
        elif resume_state!='prepared':
            report('reconciling',batch_id=batch,previous_state=resume_state)
        while clock()<deadline:
            authorize()
            if control.row(batch)['state']=='quarantined':
                return report('quarantined',batch_id=batch)
            outcome=invoke('recover' if recovery_batch else 'advance','--batch',batch,timeout=max(1,min(600,int(deadline-clock()))))
            if outcome.get('state') in DispatchStopped.STATES:
                raise DispatchStopped(outcome['state'])
            authorize()
            if control.row(batch)['state']=='quarantined':
                return report('quarantined',batch_id=batch)
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
        if recovery_batch or not config.get('drain_queue'):
            return report('completed',batch_id=batch)
    return report('cycle_window_complete')


def drain(config_path,config,control,call=bridge,sleep=time.sleep,clock=time.monotonic, *, authorize=None,recovery_batch=None):
    from dispatch_policy import require_automatic,DispatchStopped
    authorize=authorize or (lambda:require_automatic(config))
    try:authorize()
    except DispatchStopped as exc:return exc.report()
    if not config.get('exception_audit_root'):
        try:return drain_once(config_path,config,control,call,sleep,clock,authorize=authorize,recovery_batch=recovery_batch)
        except DispatchStopped as exc:return exc.report()
    from exception_audit import Audit
    from batch_control import atomic_json
    audit=Audit(config['exception_audit_root']);audit.prune()
    recovery=control.root/'recovery.json'
    deadline=clock()+int(config.get('cycle_timeout_seconds',19800))
    while deadline-clock()>=60:
        try:authorize()
        except DispatchStopped as exc:return exc.report()
        try:
            previous=json.loads(recovery.read_text()) if recovery.exists() else {}
            effective_retry=effective_retry_at(previous)
        except (OSError,ValueError,TypeError,RecursionError):
            from queue_dispatch import DispatchBlocked
            raise DispatchBlocked('invalid_recovery') from None
        remaining=effective_retry-time.time()
        if remaining>0:
            report={'state':'cooldown','at':time.time(),**previous,'effective_retry_at':effective_retry}
            atomic_json(control.root/'cycle-status.json',report)
            # No hours-long sleeping process occupying an active lane slot.
            # The persistent watchdog returns after the recorded cooldown.
            return report
        try:
            report=drain_once(config_path,{**config,'cycle_timeout_seconds':int(deadline-clock())},control,call,sleep,clock,authorize=authorize,recovery_batch=recovery_batch)
            authorize()
            if report.get('state')=='quarantined':
                return report  # Parking an unknown attempt is not successful recovery.
            atomic_json(recovery,{'failures':0,'retry_at':0,'at':time.time()})
            return report
        except DispatchStopped as exc:
            return exc.report()
        except Exception as exc:
            try:authorize()
            except DispatchStopped as stopped:return stopped.report()
            from queue_dispatch import DispatchBlocked,block_with_backoff
            if isinstance(exc,DispatchBlocked):
                report=block_with_backoff(control.root,exc)
                atomic_json(control.root/'cycle-status.json',report)
                return report
            record=record_failure(control.root,exception_code(exc),audit,config['owner'])
            report={'state':'cooldown',**record}
            atomic_json(control.root/'cycle-status.json',report)
            return report
    return {'state':'cycle_window_complete'}


def main():
    from dispatch_policy import (load_config,ConfigGuard,DispatchStopped,
        manual_target,readonly_reconcile,require_automatic)
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--render-timer',action='store_true')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--manual',action='store_true',help='Retired broad bypass; returns manual_authorization_required')
    modes.add_argument('--manual-recovery',action='store_true',help='Explicit mutating recovery of one existing --batch; never prepare or submit')
    modes.add_argument('--reconcile-readonly',action='store_true',help='Local self-ledger snapshot only; no Controller/provider/import/logical write; SQLite WAL coordination sidecars may change')
    parser.add_argument('--batch')
    args=parser.parse_args()
    root=None
    guard=None
    from queue_dispatch import required_roots,DispatchBlocked,block_with_backoff
    try:
        config=load_config(args.config)
        if args.render_timer:
            print(timer_text(config['interval_hours']),end='')
            return
        if args.manual:raise DispatchStopped('manual_authorization_required')
        if args.reconcile_readonly or (config.get('reconcile_only') is True and not args.manual_recovery):
            if not args.reconcile_readonly:
                # Config reconcile_only permits a local observer only when scheduled.
                require_automatic({**config,'reconcile_only':False})
            guard=ConfigGuard(args.config,config,manual_recovery=True)
            result=readonly_reconcile(config,args.batch)
            guard()
            print(json.dumps(result))
            return
        guard=ConfigGuard(args.config,config,manual_recovery=args.manual_recovery)
        guard()
        root=Path(config['state_root'])
        if args.batch and not args.manual_recovery:
            raise DispatchStopped('manual_authorization_required')
        if args.manual_recovery:manual_target(root,args.batch,time.time())
        import fcntl
        from batch_control import Controller
        os.umask(0o077)
        control=Controller(root,config['owner'],kaggle_python=config['kaggle_python'],
                           required_roots=required_roots(config),admission=guard,
                           recovery_batch=args.batch if args.manual_recovery else None)
        with (root/'cycle.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            guard()
            if args.manual_recovery:manual_target(root,args.batch,time.time())
            cycle_config={**config,'drain_queue':False} if args.manual_recovery else config
            result=drain(args.config,cycle_config,control,authorize=guard,
                         recovery_batch=args.batch if args.manual_recovery else None)
            if result.get('state') in DispatchStopped.STATES|{'dispatch_blocked'}:
                print(json.dumps(result))
    except DispatchStopped as exc:
        print(json.dumps(exc.report()))
    except DispatchBlocked as exc:
        try:
            if guard is not None:guard()
        except DispatchStopped as stopped:
            print(json.dumps(stopped.report()))
            return
        print(json.dumps(block_with_backoff(root,exc)))


if __name__=='__main__':
    main()
