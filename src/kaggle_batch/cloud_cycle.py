"""One bounded cloud-only cycle and disabled-by-default 6h/12h timer rendering."""
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
        prepared=bridge(args.config,'prepare','--limit',str(config['batch_limit']),timeout=3600)
        batch=prepared.get('batch_id') or prepared.get('existing_batch')
        if not batch:
            print(json.dumps({'state':'empty','gpu_started':False}))
            return
        control=Controller(root,config['owner'],kaggle_python=config['kaggle_python'])
        deadline=time.monotonic()+7200
        if control.row(batch)['state']!='prepared':
            # Restarting the observer must not bypass the check interval.
            time.sleep(660)
        while time.monotonic()<deadline:
            outcome=bridge(args.config,'advance','--batch',batch,timeout=600)
            print(json.dumps(outcome),flush=True)
            if control.row(batch)['state'] in {'imported','resolved'}:
                return
            if outcome.get('invalid') or outcome.get('missing_ids') or any(
                state not in ('imported','already_imported','existing_result_preserved')
                for state in outcome.get('import_states',{})):
                raise RuntimeError('Batch requires selective recovery; no automatic full resubmission')
            # Do not issue a final early poll when the observation window ends.
            time.sleep(min(660,max(0,deadline-time.monotonic())))
        print(json.dumps({'batch_id':batch,'state':'observation_timeout',
                          'note':'Remote state remains authoritative; resume this same batch'}))


if __name__=='__main__':
    main()
