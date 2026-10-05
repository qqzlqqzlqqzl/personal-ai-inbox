"""Health-aware Kaggle scheduler for a live queue or explicit historical scope.

It never submits a Kaggle job itself. It only starts the existing bounded lane
services. Lane services keep immutable manifests and perform remote reconciliation.
"""
# Reject help/unknown arguments before importing application code or touching
# scheduler state. This command accepts no operational arguments.
if __name__ == '__main__':
    import argparse
    argparse.ArgumentParser(description='Run one bounded scheduler tick.').parse_args()

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

try:
    from .dispatch_policy import automatic_allowed,require_automatic,DispatchStopped,config_fingerprint
except ImportError:
    from dispatch_policy import automatic_allowed,require_automatic,DispatchStopped,config_fingerprint

ROOT=Path('/home/ubuntu/ai-news')
STAGE=ROOT/'runtime/qwen-month-20260925'
KEYS=('primary','secondary','third','fourth','fifth')
FINISHED={'imported','retired','resolved'}
ANALYSIS_STATES=('pending','waiting_model','budget_paused','fetch_error','ai_error')
CARD_STATES=('pending','error','budget_paused','waiting_model')
MAX_ACTIVE=len(KEYS)
BATCH_LIMIT=20


from work_admission import check,options

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


def snapshot_lanes(now,states,configs=None):
    lanes={}
    quota_configs={}
    for key in KEYS:
        lane={'active':states.get(key) in ('active','activating','deactivating','reloading'),
              'service_state':states.get(key,'unknown'),'outstanding':None,'recovery':{},'cycle':{},
              'retry_at':0,'ready':False,'pending_local':0,'schedule_enabled':False}
        try:
            cfg=lane_config(key) if configs is None else configs[key]
            lane['quota_gate']={'allowed':False,'state':'schedule_disabled'}
            if not automatic_allowed(cfg):
                if isinstance(cfg,dict) and cfg.get('reconcile_only') is True:
                    lane['quota_gate']['state']='reconcile_only'
                lanes[key]=lane
                continue
            require_automatic(lane_config(key),STAGE/'paused.json')
            lane['schedule_enabled']=True
            root=Path(cfg['state_root'])
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
            if not lane['active'] and lane['ready']:
                if lane['outstanding'] and lane['outstanding']['state']!='prepared':
                    lane['quota_gate']={'allowed':False,'state':'existing_id_recovery'}
                else:
                    quota_configs[key]=cfg
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,DispatchStopped) as exc:
            lane['state_error']=type(exc).__name__;lane['ready']=False
        lanes[key]=lane
    def quota(key,cfg):
        def authorize():
            current=lane_config(key)
            require_automatic(current,STAGE/'paused.json')
            if config_fingerprint(current)!=config_fingerprint(cfg):raise DispatchStopped('configuration_changed')
        try:
            authorize()
            return query_config(cfg,authorize=authorize)
        except DispatchStopped as exc:
            return {'allowed':False,'state':exc.state}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(KEYS)) as pool:
        futures={key:pool.submit(quota,key,cfg) for key,cfg in quota_configs.items()}
        for key,future in futures.items():
            lanes[key]['quota_gate']=future.result()
    return lanes


def due_entries(now,config, *,admission=None):
    from feed_consumption import RESTRICTED_ERRORS
    check(admission)
    claimed=claimed_entries(required_roots(config))
    allow=set(resolve_entry_ids(config,**options(admission)) or [])
    if not allow:return set(),set()
    check(admission)
    with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        rows=db.execute("""SELECT DISTINCT a.entry_id FROM analyses a
            LEFT JOIN card_translations c ON c.entry_id=a.entry_id AND c.user_id=a.user_id
            WHERE ((a.state IN ('pending','waiting_model','budget_paused','fetch_error','ai_error')
                    AND a.next_try<=? AND a.attempts<3)
                OR (a.state NOT IN ('removed','requires_source_review')
                    AND c.status IN ('pending','error','budget_paused','waiting_model')
                    AND c.next_try<=? AND c.attempts<3))
                AND COALESCE(a.error,'') NOT IN (?,?)""",(now,now,*RESTRICTED_ERRORS)).fetchall()
    due={int(row[0]) for row in rows}&allow
    if config.get('qwen_exception_review'):
        check(admission)
        with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
            has_reviews=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qwen_exception_reviews'").fetchone()
            if has_reviews:
                rows=db.execute("""SELECT a.entry_id FROM analyses a
                    LEFT JOIN qwen_exception_reviews q ON q.entry_id=a.entry_id
                    WHERE (a.state IN ('requires_fulltext_adapter','insufficient_content')
                           OR (a.state='fetch_error' AND a.attempts>=3))
                    AND (q.entry_id IS NULL OR q.reviewed_at<?)
                    AND COALESCE(a.error,'') NOT IN (?,?)""",(now-30*86400,*RESTRICTED_ERRORS)).fetchall()
            else:
                rows=db.execute("""SELECT entry_id FROM analyses
                    WHERE (state IN ('requires_fulltext_adapter','insufficient_content')
                       OR (state='fetch_error' AND attempts>=3))
                       AND COALESCE(error,'') NOT IN (?,?)""",RESTRICTED_ERRORS).fetchall()
        due|={int(row[0]) for row in rows}&allow
    check(admission)
    with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kaggle_prepare_leases'").fetchone()
        leased={r[0] for r in db.execute('SELECT entry_id FROM kaggle_prepare_leases WHERE expires>?',(now,))} if exists else set()
    due-=leased
    return due-claimed,due&claimed


def queue_summary(now,config, *,admission=None):
    check(admission)
    ids=resolve_entry_ids(config,**options(admission)) or []
    if not ids:return {'analyses':{},'cards':{},'next_item_retry':0,'total':0}
    marks=','.join('?' for _ in ids)
    check(admission)
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


def dispatch_topology(configs=None):
    try:
        configs = {key: lane_config(key) for key in KEYS} if configs is None else configs
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


def schedule_snapshot():
    try:
        return {key:lane_config(key) for key in KEYS}
    except (OSError,ValueError,TypeError,RecursionError):
        raise DispatchStopped('configuration_unavailable') from None


def schedule_admission(configs):
    # Missing or malformed lane configuration is never authority for a launch.
    if any(not isinstance(configs.get(key),dict) for key in KEYS):
        raise DispatchBlocked('invalid_topology')
    from_dispatch=[cfg for cfg in configs.values() if automatic_allowed(cfg)]
    if not from_dispatch:
        raise DispatchStopped('schedule_disabled')
    require_automatic(from_dispatch[0],STAGE/'paused.json')


def revalidate_schedule(configs):
    current=schedule_snapshot()
    schedule_admission(current)
    if config_fingerprint(current)!=config_fingerprint(configs):raise DispatchStopped('configuration_changed')


def _tick(run=subprocess.run,starter=start_lane,now=None):
    now=float(time.time() if now is None else now)
    if (STAGE/'paused.json').exists():
        return {'state':'paused','started':[],'at':now}
    state_path=ROOT/'state/kaggle-month-dispatch/scheduler.json'
    configs=None
    def blocked(exc,started=None):
        # A concurrent stop outranks the earlier ledger failure. Preserve its
        # existing recovery record instead of opening a new local-state retry.
        current=None
        try:
            current=schedule_snapshot()
            schedule_admission(current)
            if configs is not None and config_fingerprint(current)!=config_fingerprint(configs):
                raise DispatchStopped('configuration_changed')
        except DispatchStopped as stop:return stopped(stop,started)
        except DispatchBlocked:
            # An unchanged malformed topology is still a typed local error.
            # A changed configuration cannot authorize error-path mutation.
            if current is None:return stopped(DispatchStopped('configuration_unavailable'),started)
            if configs is not None and config_fingerprint(current)!=config_fingerprint(configs):
                return stopped(DispatchStopped('configuration_changed'),started)
        report={**block_with_backoff(state_path.parent,exc,now=now),'started':started or [],'at':now}
        atomic_json(state_path,report)
        return report
    def stopped(exc,started=None):
        report={**exc.report(),'started':started or [],'at':now}
        atomic_json(state_path,report)
        return report
    try:
        configs=schedule_snapshot()
        schedule_admission(configs)
    except DispatchStopped as exc:
        return stopped(exc)
    except DispatchBlocked as exc:
        return blocked(exc)
    recovery_path=state_path.parent/'recovery.json'
    try:
        recovery=read_json(recovery_path,{})
        if not isinstance(recovery,dict):raise DispatchBlocked('invalid_recovery')
        retry_at=effective_retry_at(recovery)
        failures=recovery.get('failures',0)
        if type(failures) is not int or failures<0:raise DispatchBlocked('invalid_recovery')
        if retry_at>now:
            # Admission is closed throughout cooldown: no service/quota calls,
            # no increased failure count, and no mutation of batch claims.
            report={**DispatchBlocked('local_state_cooldown').report(),'started':[],
                    'code':'local_state','failures':failures,'retry_at':retry_at,'at':now}
            atomic_json(state_path,report)
            return report
    except DispatchBlocked as exc:
        return blocked(exc)
    except (OSError,ValueError,TypeError,RecursionError):
        return blocked(DispatchBlocked('invalid_recovery'))
    try:
        configs,roots=dispatch_topology(configs)
        claimed_entries(roots)
    except DispatchBlocked as exc:
        return blocked(exc)
    try:revalidate_schedule(configs)
    except DispatchStopped as exc:return stopped(exc)
    except DispatchBlocked as exc:return blocked(exc)
    states=service_states(run);lanes=snapshot_lanes(now,states,configs)
    cfg=configs['primary']
    try:
        due,claimed=due_entries(now,cfg,admission=lambda:revalidate_schedule(configs))
    except DispatchStopped as exc:return stopped(exc)
    except DispatchBlocked as exc:
        return blocked(exc)
    try:summary=queue_summary(now,cfg,admission=lambda:revalidate_schedule(configs))
    except DispatchStopped as exc:return stopped(exc)
    except DispatchBlocked as exc:return blocked(exc)
    previous=read_json(state_path,{}) or {};cursor=int(previous.get('cursor',0))%len(KEYS)
    starts,new_cursor=plan(lanes,len(due),cursor)
    started=[];failures=[]
    try:
        claimed_entries(roots)
    except DispatchBlocked as exc:
        return blocked(exc)
    for key in starts:
        try:
            revalidate_schedule(configs)
            require_automatic(configs[key],STAGE/'paused.json')
            starter(key,run=run);started.append(key)
        except DispatchStopped as exc:
            return stopped(exc,started)
        except DispatchBlocked as exc:
            return blocked(exc,started)
        except (OSError,subprocess.SubprocessError) as exc:
            failures.append({'lane':key,'error':type(exc).__name__})
    try:revalidate_schedule(configs)
    except DispatchStopped as exc:return stopped(exc,started)
    except DispatchBlocked as exc:return blocked(exc,started)
    # A stopped tick is not a successful reset of persisted recovery.
    if recovery:
        try:revalidate_schedule(configs)
        except DispatchStopped as exc:return stopped(exc,started)
        except DispatchBlocked as exc:return blocked(exc,started)
        atomic_json(recovery_path,{'failures':0,'retry_at':0,'at':now})
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
        try:revalidate_schedule(configs)
        except DispatchStopped as exc:return stopped(exc,started)
        except DispatchBlocked as exc:return blocked(exc,started)
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
