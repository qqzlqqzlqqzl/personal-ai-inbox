"""Loopback authenticated control for the current bounded Kaggle article scope."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import hmac,json,math,os,sqlite3,subprocess,threading,time
from fastapi import APIRouter,Header,HTTPException

ROOT=Path('/home/ubuntu/ai-news')
STAGE=ROOT/'runtime/qwen-month-20260925'
KEYS=('primary','secondary','third','fourth','fifth')
router=APIRouter(prefix='/internal/kaggle-month')
_QUOTA_TTL=300
_quota_lock=threading.Lock()
_quota_refresh_lock=threading.Lock()


def _hours(value):
    if value is None or isinstance(value,bool):return None
    text=str(value).strip().lower()
    if text.endswith('h'):text=text[:-1]
    try:
        value=float(text)
        return value if math.isfinite(value) and value>=0 else None
    except ValueError:return None


def _parse_quota_rows(rows):
    if not isinstance(rows,list):raise ValueError('invalid quota payload')
    parsed={}
    for row in rows:
        if not isinstance(row,dict):continue
        resource=str(row.get('resource','')).strip().lower()
        if resource not in ('gpu','tpu'):continue
        parsed[resource]={
            'used_hours':_hours(row.get('used')),
            'remaining_hours':_hours(row.get('remaining')),
            'total_hours':_hours(row.get('total')),
            'refresh_at':str(row.get('refreshAt') or '')[:40] or None,
        }
    if 'gpu' not in parsed:raise ValueError('GPU quota missing')
    return parsed


def _quota_for_lane(key):
    from kaggle_batch.dispatch_policy import (load_config,config_fingerprint,
        require_automatic,DispatchStopped)
    path=ROOT/f'src/kaggle_batch/cloud-config-month-{key}.json'
    try:
        config=load_config(path)
        if key not in effective_enabled_lanes():return {'state':'error','error':'schedule_disabled'}
        current=load_config(path)
        # Python dict equality aliases True/1 and False/0; typed JSON does not.
        if config_fingerprint(current)!=config_fingerprint(config):
            return {'state':'error','error':'configuration_changed'}
        require_automatic(current,STAGE/'paused.json')
        env={**os.environ,'KAGGLE_API_TOKEN':current['token_file']}
        result=subprocess.run(
            [current['kaggle_python'],'-m','kaggle','quota','--format','json'],
            capture_output=True,text=True,encoding='utf-8',timeout=20,env=env,
        )
        if result.returncode:raise RuntimeError('quota command failed')
        return {'state':'ok',**_parse_quota_rows(json.loads(result.stdout or '[]'))}
    except DispatchStopped as exc:
        return {'state':'error','error':exc.state}
    except (OSError,subprocess.SubprocessError,RuntimeError,ValueError,KeyError,json.JSONDecodeError):
        return {'state':'error','error':'quota_unavailable'}


def effective_enabled_lanes():
    from kaggle_batch.dispatch_policy import automatic_allowed,paused,DispatchStopped
    try:
        if paused(STAGE/'paused.json'):return []
        configs={key:json.loads((ROOT/f'src/kaggle_batch/cloud-config-month-{key}.json').read_text()) for key in KEYS}
        if any(not isinstance(cfg,dict) for cfg in configs.values()):return []
        return [key for key,cfg in configs.items() if automatic_allowed(cfg)]
    except (OSError,ValueError,TypeError,RecursionError,DispatchStopped):
        return []


def quota_status(now=None,ttl=_QUOTA_TTL,*,background=False):
    now=float(time.time() if now is None else now)
    folder=ROOT/'state/kaggle-month-dispatch'
    cache=folder/'quota-status.json'
    def load():
        try:
            value=json.loads(cache.read_text())
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError,TypeError):return {}
    cached=load()
    enabled=effective_enabled_lanes()
    if not enabled:
        # Disabled UI polling is a cache-only observation, including expired data.
        lanes=cached.get('lanes',{})
        return {**cached,'lanes':lanes if isinstance(lanes,dict) else {},'refresh_disabled':True}
    folder.mkdir(parents=True,exist_ok=True)
    if now-float(cached.get('checked_at',0) or 0)<ttl and all(k in cached.get('lanes',{}) for k in KEYS):
        return cached
    if background:
        # UI observes the last quota without waiting for five remote CLI calls.
        # The regular refresher still enforces schedule/config guards and bounds.
        if _quota_refresh_lock.acquire(blocking=False):
            def refresh():
                try:quota_status(ttl=ttl)
                finally:_quota_refresh_lock.release()
            try:threading.Thread(target=refresh,daemon=True,name='kaggle-quota-refresh').start()
            except BaseException:
                _quota_refresh_lock.release()
                raise
        previous=cached.get('lanes',{})
        lanes={key:{**(previous.get(key,{}) if isinstance(previous,dict) else {}),
                    'state':'stale','stale':True,'error':'quota_refreshing'}
               for key in KEYS}
        return {**cached,'lanes':lanes,'refreshing':True}
    with _quota_lock:
        cached=load()
        if now-float(cached.get('checked_at',0) or 0)<ttl and all(k in cached.get('lanes',{}) for k in KEYS):
            return cached
        previous=cached.get('lanes',{}) if isinstance(cached.get('lanes'),dict) else {}
        lanes={key:{**previous.get(key,{}),'state':'stale','stale':True,'error':'schedule_disabled'}
               for key in KEYS if key not in enabled}
        with ThreadPoolExecutor(max_workers=len(KEYS)) as pool:
            futures={pool.submit(_quota_for_lane,key):key for key in enabled}
            for future in as_completed(futures):
                key=futures[future]
                try:value=future.result()
                except Exception:value={'state':'error','error':'quota_unavailable'}
                if value.get('state')=='error' and previous.get(key,{}).get('gpu'):
                    value={**previous[key],'state':'stale','error':value.get('error'),'stale':True}
                lanes[key]=value
        value={'checked_at':now,'ttl_seconds':ttl,'lanes':lanes}
        temp=cache.with_suffix('.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False))
        os.chmod(temp,0o600);os.replace(temp,cache)
        return value


def authenticate(value):
    expected=json.loads((ROOT/'.private/vendor-refresh.json').read_text())['token']
    if not value or not hmac.compare_digest(value,expected):raise HTTPException(401,'Authentication required')

def status():
    scope=json.loads((STAGE/'scope.json').read_text())
    historical_scope=dict(scope)
    historical_ids=json.loads((STAGE/'allowlist.json').read_text())['entry_ids']
    config=json.loads((ROOT/'src/kaggle_batch/cloud-config-month-primary.json').read_text())
    from kaggle_batch.live_scope import resolve_entry_ids
    live=config.get('queue_scope')=='all_enabled_feeds'
    ids=(resolve_entry_ids(config) or []) if live else historical_ids
    if live:
        # A continuous queue has no fixed publication-date cutoff.
        scope={**scope,'from':None,'to':None,'articles':len(ids),'unknown_date_excluded':0,
               'scope_type':'all_enabled_feeds','scope_label':'持续增量 · 当前启用的订阅源'}
    marks=','.join('?' for _ in ids) or 'NULL'
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
    enabled_lanes=effective_enabled_lanes()
    quotas=quota_status(background=True)
    for key in KEYS:
        folder=ROOT/'state'/('kaggle-month-'+key)
        report=folder/'cycle-status.json'
        lanes[key]=json.loads(report.read_text()) if report.exists() else {'state':'not_started'}
        lanes[key]['service']=services.get(f'ai-news-kaggle-month@{key}.service',{})
        recovery=folder/'recovery.json'
        if recovery.exists():lanes[key]['recovery']=json.loads(recovery.read_text())
        batch_db=folder/'batches.sqlite3'
        # Current selectable work and old retained claims are separate facts.
        # Never inherit an outstanding row from the cached cycle report.
        lanes[key].pop('outstanding',None)
        lanes[key]['ledger_state']='unknown'
        lanes[key]['quarantine']={'batches':None,'claims':None}
        if batch_db.exists():
            try:
                with sqlite3.connect(batch_db.resolve().as_uri()+'?mode=ro',uri=True,timeout=5) as db:
                    db.execute('PRAGMA query_only=ON')
                    db.execute('BEGIN')
                    db.row_factory=sqlite3.Row
                    row=db.execute("""SELECT id,state,remote_status,error,updated FROM batches
                        WHERE state NOT IN ('imported','retired','resolved','quarantined') ORDER BY updated LIMIT 1""").fetchone()
                    isolated=db.execute("SELECT COUNT(*) FROM batches WHERE state='quarantined'").fetchone()[0]
                    held=db.execute("SELECT COUNT(*) FROM batch_claims c JOIN batches b ON b.id=c.batch_id WHERE b.state='quarantined'").fetchone()[0]
                    if row:lanes[key]['outstanding']=dict(row)
                    lanes[key]['quarantine']={'batches':isolated,'claims':held}
                    lanes[key]['ledger_state']='ok'
            except (OSError,sqlite3.Error):pass
        lanes[key]['quota']=quotas.get('lanes',{}).get(key,{'state':'error','error':'quota_unavailable'})
        from kaggle_batch.quota_guard import admission
        lanes[key]['quota_gate']=(admission(lanes[key]['quota'],checked_at=quotas.get('checked_at'))
            if key in enabled_lanes else {'allowed':False,'state':'schedule_disabled'})
    enabled=bool(effective_enabled_lanes())
    scheduler_file=ROOT/'state/kaggle-month-dispatch/scheduler.json'
    scheduler=json.loads(scheduler_file.read_text()) if scheduler_file.exists() else {'state':'not_started'}
    return {'scheduler':scheduler,'scope':{k:scope[k] for k in ['from','to','articles','unknown_date_excluded']} | {'scope_type':scope.get('scope_type','recent_month'),'label':scope.get('scope_label','最近一个月')},'enabled':enabled,
        'analyses':analyses,'cards':cards,'lanes':lanes,'quota_checked_at':quotas.get('checked_at'),
        'historical_scope':{'articles':len(historical_ids),'from':historical_scope.get('from'),'to':historical_scope.get('to'),'label':'历史存量快照，仅用于审计与恢复'},'checked_at':time.time()}

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
        return {'action':action,'accepted':True,'scope':'configured_queue_scope',
                'gpu_cancellation':False,'scheduler':scheduler}
    return {'action':action,'accepted':True,'scope':'configured_queue_scope','gpu_cancellation':False}
