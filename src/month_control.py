"""Loopback authenticated control for the current bounded Kaggle article scope."""
from pathlib import Path
import hmac,json,sqlite3,subprocess,time
from fastapi import APIRouter,Header,HTTPException

ROOT=Path('/home/ubuntu/ai-news')
STAGE=ROOT/'runtime/qwen-month-20260925'
KEYS=('primary','secondary','third','fourth','fifth')
router=APIRouter(prefix='/internal/kaggle-month')

def authenticate(value):
    expected=json.loads((ROOT/'.private/vendor-refresh.json').read_text())['token']
    if not value or not hmac.compare_digest(value,expected):raise HTTPException(401,'Authentication required')

def status():
    scope=json.loads((STAGE/'scope.json').read_text())
    ids=json.loads((STAGE/'allowlist.json').read_text())['entry_ids']
    marks=','.join('?' for _ in ids)
    with sqlite3.connect('file:'+str(ROOT/'state/analysis.sqlite3')+'?mode=ro',uri=True,timeout=10) as db:
        analyses=dict(db.execute(f'SELECT state,count(*) FROM analyses WHERE entry_id IN ({marks}) GROUP BY state',ids))
        cards=dict(db.execute(f'SELECT status,count(*) FROM card_translations WHERE entry_id IN ({marks}) GROUP BY status',ids))
    lanes={}
    units=subprocess.run(['systemctl','--user','show',*[f'ai-news-kaggle-month@{k}.service' for k in KEYS],
        '--property=Id,ActiveState,SubState'],capture_output=True,text=True,timeout=10,check=True)
    services={}
    for block in units.stdout.strip().split('\n\n'):
        values=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        if values.get('Id'):services[values['Id']]=values
    for key in KEYS:
        folder=ROOT/'state'/('kaggle-month-'+key)
        report=folder/'cycle-status.json'
        lanes[key]=json.loads(report.read_text()) if report.exists() else {'state':'not_started'}
        lanes[key]['service']=services.get(f'ai-news-kaggle-month@{key}.service',{})
        recovery=folder/'recovery.json'
        if recovery.exists():lanes[key]['recovery']=json.loads(recovery.read_text())
    enabled=not (STAGE/'paused.json').exists()
    scheduler_file=ROOT/'state/kaggle-month-dispatch/scheduler.json'
    scheduler=json.loads(scheduler_file.read_text()) if scheduler_file.exists() else {'state':'not_started'}
    return {'scheduler':scheduler,'scope':{k:scope[k] for k in ['from','to','articles','unknown_date_excluded']} | {'scope_type':scope.get('scope_type','recent_month'),'label':scope.get('scope_label','最近一个月')},'enabled':enabled,
        'analyses':analyses,'cards':cards,'lanes':lanes,'checked_at':time.time()}

@router.get('/status')
def get_status(x_vendor_refresh:str=Header(default='')):
    authenticate(x_vendor_refresh);return status()

@router.post('/{action}')
def control(action:str,x_vendor_refresh:str=Header(default='')):
    authenticate(x_vendor_refresh)
    if action not in ('start','resume','pause'):raise HTTPException(400,'Unknown action')
    paused=STAGE/'paused.json'
    if action=='pause':
        import fcntl
        coordination=ROOT/'state/kaggle-month-dispatch';coordination.mkdir(parents=True,exist_ok=True)
        with (coordination/'scheduler.lock').open('a') as guard:
            fcntl.flock(guard,fcntl.LOCK_EX)
            paused.write_text(json.dumps({'paused_at':time.time()}))
            # No race with a scheduler starting a lane after the stop command.
            # Existing finite GPU jobs are reconciled on resume, not cancelled.
            subprocess.run(['systemctl','--user','stop',*[f'ai-news-kaggle-month@{k}.service' for k in KEYS]],timeout=45,check=True)
    else:
        if paused.exists():
            if action=='start':return {'state':'paused','started':False}
            paused.rename(STAGE/('pause-history-'+str(time.time_ns())+'.json'))
        # Do not blindly start all accounts. The shared scheduler starts only
        # healthy lanes that have reconciliation or due unclaimed work, in rotating
        # order, while preserving immutable outstanding claims.
        from kaggle_batch.lane_scheduler import tick
        scheduler=tick()
        return {'action':action,'accepted':True,'scope':'current_allowlist',
                'gpu_cancellation':False,'scheduler':scheduler}
    return {'action':action,'accepted':True,'scope':'current_allowlist','gpu_cancellation':False}
