"""Health-aware Kaggle scheduler for a live queue or explicit historical scope.

It never submits a Kaggle job itself. It only starts the existing bounded lane
services. Lane services keep immutable manifests and perform remote reconciliation.
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import time

try:
    from .live_scope import resolve_entry_ids
    from .batch_control import atomic_json
    from .exception_audit import Audit
    from .queue_dispatch import claimed_entries, required_roots, DispatchBlocked, block_with_backoff
    from .recovery_policy import effective_retry_at
    from .quota_guard import query_config, plan_lanes
except ImportError:  # direct script/test execution
    from live_scope import resolve_entry_ids
    from batch_control import atomic_json
    from exception_audit import Audit
    from queue_dispatch import claimed_entries, required_roots, DispatchBlocked, block_with_backoff
    from recovery_policy import effective_retry_at
    from quota_guard import query_config, plan_lanes

ROOT=Path('/home/ubuntu/ai-news')
STAGE=ROOT/'runtime/qwen-month-20260925'
KEYS=('primary','secondary','third','fourth','fifth')
FINISHED={'imported','retired','resolved'}
ANALYSIS_STATES=('pending','waiting_model','budget_paused','fetch_error','ai_error')
CARD_STATES=('pending','error','budget_paused','waiting_model')
MAX_ACTIVE=len(KEYS)
BATCH_LIMIT=20


def read_json(path,default=None):
    path=Path(path)
    return json.loads(path.read_text()) if path.exists() else default

def lane_config(key):
    return read_json(ROOT/f'src/kaggle_batch/cloud-config-month-{key}.json')


def service_states(run=subprocess.run):
    result={}
    for key in KEYS:
        name=f'ai-news-kaggle-month@{key}.service'
        try:
            p=run(['systemctl','--user','show',name,'--property=ActiveState','--value'],
                  capture_output=True,text=True,timeout=10,check=True)
            result[key]=p.stdout.strip()
        except (OSError,subprocess.SubprocessError):result[key]='unavailable'
    return result


def outstanding(root):
    db_path=Path(root)/'batches.sqlite3'
    if not db_path.exists():return None
    with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        db.row_factory=sqlite3.Row
        row=db.execute("SELECT id,state,remote_status,error,updated FROM batches "
                       "WHERE state NOT IN ('imported','retired','resolved') "
                       "ORDER BY updated LIMIT 1").fetchone()
    return dict(row) if row else None


def snapshot_lanes(now,states):
    lanes={}
    quota_configs={}
    for key in KEYS:
        lane={'active':states.get(key) in ('active','activating','deactivating','reloading'),
              'service_state':states.get(key,'unknown'),'outstanding':None,'recovery':{},'cycle':{},
              'retry_at':0,'ready':False,'pending_local':0}
        try:
            cfg=lane_config(key);root=Path(cfg['state_root'])
            # A manually launched observer holds the same lock as the service.
            # Detect it rather than trying to start a second observer for this lane.
            import fcntl
            cycle_lock=root/'cycle.lock'
            if cycle_lock.exists():
                with cycle_lock.open('a') as process_lock:
                    try:fcntl.flock(process_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:lane['active']=True
            lane['outstanding']=outstanding(root)
            lane['recovery']=read_json(root/'recovery.json',{}) or {}
            lane['cycle']=read_json(root/'cycle-status.json',{}) or {}
            lane['retry_at']=effective_retry_at(lane['recovery'])
            lane['persisted_retry_at']=float(lane['recovery'].get('retry_at',0) or 0)
            dbpath=root/'batches.sqlite3'
            if dbpath.exists():
                with sqlite3.connect(dbpath.resolve().as_uri()+'?mode=ro',uri=True,timeout=10) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_progress'").fetchone():
                        db.row_factory=sqlite3.Row
                        row=db.execute("""SELECT b.* FROM batches b LEFT JOIN batch_progress p ON p.batch_id=b.id
                          WHERE b.state NOT IN ('imported','retired','resolved') AND COALESCE(p.next_try,0)<=?
                          ORDER BY CASE WHEN b.state IN ('submitting','submitted','running','submit_unknown') THEN 0 ELSE 1 END,b.updated LIMIT 1""",(now,)).fetchone()
                        lane['outstanding']=dict(row) if row else None
                        lane['pending_local']=db.execute("""SELECT count(*) FROM batch_progress p JOIN batches b ON b.id=p.batch_id
                          WHERE b.state NOT IN ('imported','retired','resolved') AND p.next_try>?""",(now,)).fetchone()[0]
                        local_at=db.execute("SELECT MIN(p.next_try) FROM batch_progress p JOIN batches b ON b.id=p.batch_id WHERE b.state NOT IN ('imported','retired','resolved') AND p.next_try>?",(now,)).fetchone()[0]
                        lane['local_retry_at']=local_at or 0
            lane['ready']=lane['retry_at']<=now and lane['service_state']!='unavailable'
            if lane['cycle'].get('recovery_required'):
                lane['ready']=False
            # Only fresh read-only quota permits new work. Recovery stays eligible.
            lane['quota_gate']={'allowed':False,'state':'not_checked_active' if lane['active'] else 'quota_unknown'}
            if not lane['active']:
                quota_configs[key]=cfg
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error) as exc:
            lane['state_error']=type(exc).__name__;lane['ready']=False
        lanes[key]=lane
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(KEYS)) as pool:
        futures={key:pool.submit(query_config,cfg) for key,cfg in quota_configs.items()}
        for key,future in futures.items():
            lanes[key]['quota_gate']=future.result()
    return lanes


def due_entries(now,config):
    claimed=claimed_entries(required_roots(config))
    allow=set(resolve_entry_ids(config) or [])
    if not allow:return set(),set()
    with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        rows=db.execute("""SELECT DISTINCT a.entry_id FROM analyses a
            LEFT JOIN card_translations c ON c.entry_id=a.entry_id AND c.user_id=a.user_id
            WHERE ((a.state IN ('pending','waiting_model','budget_paused','fetch_error','ai_error')
                    AND a.next_try<=? AND a.attempts<3)
                OR (a.state NOT IN ('removed','requires_source_review')
                    AND c.status IN ('pending','error','budget_paused','waiting_model')
                    AND c.next_try<=? AND c.attempts<3))""",(now,now)).fetchall()
    due={int(row[0]) for row in rows}&allow
    if config.get('qwen_exception_review'):
        with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
            has_reviews=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qwen_exception_reviews'").fetchone()
            if has_reviews:
                rows=db.execute("""SELECT a.entry_id FROM analyses a
                    LEFT JOIN qwen_exception_reviews q ON q.entry_id=a.entry_id
                    WHERE (a.state IN ('requires_fulltext_adapter','insufficient_content')
                           OR (a.state='fetch_error' AND a.attempts>=3))
                    AND (q.entry_id IS NULL OR q.reviewed_at<?)""",(now-30*86400,)).fetchall()
            else:
                rows=db.execute("""SELECT entry_id FROM analyses
                    WHERE state IN ('requires_fulltext_adapter','insufficient_content')
                       OR (state='fetch_error' AND attempts>=3)""").fetchall()
        due|={int(row[0]) for row in rows}&allow
    with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kaggle_prepare_leases'").fetchone()
        leased={r[0] for r in db.execute('SELECT entry_id FROM kaggle_prepare_leases WHERE expires>?',(now,))} if exists else set()
    due-=leased
    return due-claimed,due&claimed


def queue_summary(now,config):
    ids=resolve_entry_ids(config) or []
    if not ids:return {'analyses':{},'cards':{},'next_item_retry':0,'total':0}
    marks=','.join('?' for _ in ids)
    with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        analyses=dict(db.execute(f'SELECT state,count(*) FROM analyses WHERE entry_id IN ({marks}) GROUP BY state',ids))
        cards=dict(db.execute(f'SELECT status,count(*) FROM card_translations WHERE entry_id IN ({marks}) GROUP BY status',ids))
        retries=[]
        for table,column,states in [('analyses','state',ANALYSIS_STATES),('card_translations','status',CARD_STATES)]:
            qmarks=','.join('?' for _ in states)
            row=db.execute(f'SELECT MIN(next_try) FROM {table} WHERE entry_id IN ({marks}) AND {column} IN ({qmarks}) AND attempts<3 AND next_try>?',(*ids,*states,now)).fetchone()
            if row[0]:retries.append(row[0])
    return {'analyses':analyses,'cards':cards,'next_item_retry':min(retries,default=0),'total':len(ids)}


def rotated(keys,cursor):
    keys=list(keys)
    if not keys:return []
    cursor=cursor%len(KEYS)
    order=KEYS[cursor:]+KEYS[:cursor]
    return [key for key in order if key in keys]


def plan(lanes,due_count,cursor,max_active=MAX_ACTIVE,batch_limit=BATCH_LIMIT):
    return plan_lanes(KEYS,lanes,due_count,cursor,max_active,batch_limit)


def start_lane(key,run=subprocess.run):
    run(['systemctl','--user','start','--no-block',f'ai-news-kaggle-month@{key}.service'],
        timeout=20,check=True)


def dispatch_topology():
    try:
        configs = {key: lane_config(key) for key in KEYS}
        roots = [Path(configs[key]['state_root']).resolve() for key in KEYS]
        if len(set(roots)) != len(KEYS):
            raise DispatchBlocked('invalid_topology')
        for key in KEYS:
            if set(required_roots(configs[key])) != set(roots):
                raise DispatchBlocked('invalid_topology')
        return configs, roots
    except DispatchBlocked:
        raise
    except (KeyError, TypeError, ValueError, OSError):
        raise DispatchBlocked('invalid_topology') from None


def _tick(run=subprocess.run,starter=start_lane,now=None):
    now=float(time.time() if now is None else now)
    if (STAGE/'paused.json').exists():
        return {'state':'paused','started':[],'at':now}
    state_path=ROOT/'state/kaggle-month-dispatch/scheduler.json'
    def blocked(exc):
        report={**block_with_backoff(state_path.parent,exc),'started':[],'at':now}
        atomic_json(state_path,report)
        return report
    try:
        configs,roots=dispatch_topology()
        claimed_entries(roots)
    except DispatchBlocked as exc:
        return blocked(exc)
    states=service_states(run);lanes=snapshot_lanes(now,states)
    cfg=configs['primary']
    try:
        due,claimed=due_entries(now,cfg)
    except DispatchBlocked as exc:
        return blocked(exc)
    summary=queue_summary(now,cfg)
    previous=read_json(state_path,{}) or {};cursor=int(previous.get('cursor',0))%len(KEYS)
    starts,new_cursor=plan(lanes,len(due),cursor)
    started=[];failures=[]
    try:
        claimed_entries(roots)
    except DispatchBlocked as exc:
        return blocked(exc)
    for key in starts:
        if (STAGE/'paused.json').exists():break
        try:
            starter(key,run=run);started.append(key)
        except (OSError,subprocess.SubprocessError) as exc:
            failures.append({'lane':key,'error':type(exc).__name__})
    times=[v for lane in lanes.values() for v in (lane['retry_at'],lane.get('local_retry_at',0)) if v>now]
    if summary['next_item_retry']:times.append(summary['next_item_retry'])
    next_retry=min(times,default=0)
    if started:state='started'
    elif any(lane['active'] for lane in lanes.values()):state='processing'
    elif due and next_retry:state='waiting_for_lane_recovery'
    elif any(lane['outstanding'] for lane in lanes.values()):state='waiting_for_reconciliation'
    elif any(lane.get('pending_local') for lane in lanes.values()):state='waiting_for_cached_output_import'
    elif due:state='no_healthy_lane'
    elif summary['next_item_retry']:state='waiting_for_item_retry'
    elif any(k!='done' and v for k,v in summary['analyses'].items()):state='blocked_source_or_review'
    else:state='idle'
    report={'state':state,'at':now,'started':started,'start_failures':failures,
            'due_unclaimed':len(due),'due_claimed':len(claimed),'next_retry_at':next_retry,
            'cursor':new_cursor,'queue_scope':cfg.get('queue_scope','allowlist'),'queue':summary,'lanes':{key:{'active':lane['active'],'ready':lane['ready'],
                'retry_at':lane['retry_at'],'outstanding_state':(lane['outstanding'] or {}).get('state'),
                'cycle_state':lane['cycle'].get('state'),'pending_local':lane.get('pending_local',0),
                'quota_gate':lane.get('quota_gate',{'allowed':False,'state':'quota_unknown'}),
                'state_error':lane.get('state_error')} for key,lane in lanes.items()}}
    atomic_json(state_path,report)
    if started or failures:
        Audit(ROOT/'state/qwen-exception-audit').append('scheduler_tick',
            started=started,start_failures=failures,due_unclaimed=len(due),due_claimed=len(claimed))
    return report


def tick(run=subprocess.run,starter=start_lane,now=None):
    import fcntl
    folder=ROOT/'state/kaggle-month-dispatch';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'scheduler.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'state':'scheduler_busy','started':[]}
        return _tick(run=run,starter=starter,now=now)


if __name__=='__main__':
    print(json.dumps(tick(),ensure_ascii=False))
