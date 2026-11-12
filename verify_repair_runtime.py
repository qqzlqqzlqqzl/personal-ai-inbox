"""Operator-run Linux integration: actual systemd timeout/SIGKILL and native writer.

All mutations go into a new private fixture directory. Production source hashes
must remain unchanged. No credentials, real articles or real schedules are edited.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

PYTHON = '/home/ubuntu/ai-news/runtime/venv/bin/python'
MAINT = Path('/home/ubuntu/cloud-maintenance')


def command(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=15, check=True)


def wait_until(predicate, seconds=12):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        if predicate(): return
        time.sleep(.1)
    raise TimeoutError('runtime_acceptance_deadline')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module-dir', type=Path, default=MAINT)
    args = parser.parse_args()
    os.environ['XDG_RUNTIME_DIR'] = '/run/user/1000'
    os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1000/bus'
    os.umask(0o077)
    sys.path.insert(0, str(args.module_dir))
    import production_cases as c
    originals = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (c.RSS,c.SOURCE/'content_input.py')}
    base = MAINT/'repair-runtime-evidence'/('run-'+str(int(time.time()))+'-'+uuid.uuid4().hex[:6])
    base.mkdir(parents=True, mode=0o700)
    outcomes = []
    for scenario in ('failure', 'sigkill', 'timeout', 'success'):
        root = base/scenario; root.mkdir(mode=0o700)
        wrapper = root/'invoke.py'
        wrapper.write_text('''import os, sys, json, time
from pathlib import Path
sys.path.insert(0, MODULE_DIR)
import repair_guard as g, ops_runbooks as r
sys.path.insert(0, str(r.VENDOR_SOURCE.parent))
root=Path(ROOT)
g.HOME=root/'guard'
module=r.load_vendor_module()
module.ROOT=root
r.VENDOR_STATES=root/'state/vendor-adapters'
r.BACKUPS=root/'backups'
r.load_vendor_module=lambda:module
r.VENDOR_STATES.mkdir(parents=True,exist_ok=True)
path=r.VENDOR_STATES/'nidec.json'
action=sys.argv[1]
if action=='recover':
    print(json.dumps(g.recover()));sys.exit(0)
before=json.dumps({'entries':[],'seen':{'real_native_writer':'preserved'},'seeded':True,'consecutive_failures':7,'next_run_at':'invalid'}).encode()
path.write_bytes(before)
(root/'original.bin').write_bytes(before)
save=module.save
def interrupted_save(key,value):
    save(key,value)
    (root/'written').write_text('native_write_complete')
    if action=='failure':raise RuntimeError('deliberate_post_write_failure')
    time.sleep(120)
if action!='success':module.save=interrupted_save
try:
    result=r.repair_vendor_schedule('nidec',r.digest_bytes(before),{},lambda:None,int(time.time()))
    (root/'outcome.json').write_text(json.dumps(result))
except RuntimeError:
    (root/'outcome.json').write_text(json.dumps({'state':'caught_failure','recovery':g.recover()}))
'''.replace('MODULE_DIR', repr(str(args.module_dir))).replace('ROOT', repr(str(root)), 1))
        # Replace only the literal assignment placeholder, never module.ROOT.
        # (The replacement above targets the first ROOT, in root=Path(ROOT).)
        prefix = 'qwen-repair-proof-'+uuid.uuid4().hex[:12]
        writer, guardian = prefix+'-writer', prefix+'-guardian'
        try:
            command('systemd-run','--user','--quiet','--unit='+writer,
                    '--property=RuntimeMaxSec='+('2s' if scenario=='timeout' else '20s'),
                    '--property=TimeoutStopSec=1s','--property=KillSignal=SIGKILL',
                    PYTHON,'-B',str(wrapper),scenario)
            if scenario in {'failure','success'}:
                wait_until(lambda:(root/'outcome.json').exists())
            else:
                wait_until(lambda:(root/'written').exists())
                if scenario=='sigkill':
                    command('systemctl','--user','kill','--signal=SIGKILL',writer+'.service')
                command('systemd-run','--user','--quiet','--unit='+guardian,
                        '--on-active=3s','--timer-property=AccuracySec=100ms',
                        '--property=TimeoutStartSec=20s',PYTHON,'-B',str(wrapper),'recover')
                def restored():
                    journal=root/'guard/current.json'
                    return journal.exists() and json.loads(journal.read_text()).get('state')=='rolled_back'
                wait_until(restored)
            journal=json.loads((root/'guard/current.json').read_text())
            actual=(root/'state/vendor-adapters/nidec.json').read_bytes()
            before=(root/'original.bin').read_bytes()
            if scenario=='success':
                verified=journal['state']=='committed' and json.loads(actual)['next_run_at']!='invalid'
            else:
                verified=journal['state']=='rolled_back' and actual==before
            props=command('systemctl','--user','show',writer+'.service','-p','Result','-p','ExecMainCode','-p','ExecMainStatus').stdout.splitlines()
            outcomes.append({'scenario':scenario,'verified':verified,'journal_state':journal['state'],'systemd':props})
            if not verified:raise ValueError('runtime_recovery_failed')
        finally:
            subprocess.run(['systemctl','--user','stop',writer+'.service',guardian+'.timer',guardian+'.service'],capture_output=True,timeout=15)
            subprocess.run(['systemctl','--user','reset-failed',writer+'.service',guardian+'.service'],capture_output=True,timeout=15)
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in originals.items())
    report={'at':int(time.time()),'actual_systemd':True,'native_atomic_writer':True,
            'production_sources_unchanged':unchanged,'outcomes':outcomes,'fixture_directory':str(base)}
    (base/'acceptance.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))
    if not unchanged:raise ValueError('production_source_changed')


if __name__=='__main__':
    main()
