"""Bounded retention for Kaggle SQLite rollback snapshots.

Normal batch manifests/results remain untouched. Only complete copies of the
analysis SQLite database are candidates for pruning.
"""
import json
import os
from pathlib import Path
import sqlite3
import time

ROOT=Path('/home/ubuntu/ai-news')
CAP_BYTES=5*1024**3
MIN_HISTORY_PER_LANE=1
YOUNG_PROTECTION_SECONDS=1800
FINISHED={'imported','retired','resolved'}

try:
    from .batch_control import atomic_json
except ImportError:
    from batch_control import atomic_json


def allocated_bytes(path):
    stat=Path(path).stat()
    blocks=getattr(stat,'st_blocks',0)
    return blocks*512 if blocks else stat.st_size


def _batch_states(root):
    path=root/'batches.sqlite3'
    if not path.exists():
        return {}
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        return {row[0]:row[1] for row in db.execute('SELECT id,state FROM batches')}


def _snapshot(file_path,lane,kind,state=None):
    file_path=Path(file_path)
    return {
        'lane':lane,
        'kind':kind,
        'state':state,
        'file':file_path,
        'receipt':file_path.parent/'backup-complete.json',
        'mtime':file_path.stat().st_mtime,
        'bytes':allocated_bytes(file_path),
        'protected':False,
        'reason':None,
    }


def discover(root=ROOT):
    root=Path(root)
    snapshots=[]
    for lane_root in sorted((root/'state').glob('kaggle-month-*')):
        if not lane_root.is_dir() or lane_root.is_symlink():
            continue
        lane=lane_root.name.removeprefix('kaggle-month-')
        states=_batch_states(lane_root)
        nonterminal=any(state not in FINISHED for state in states.values())
        extraction=[]
        for folder in lane_root.glob('before-extraction-*'):
            file_path=folder/'before-import.sqlite3'
            receipt=folder/'backup-complete.json'
            if file_path.is_file() and receipt.is_file() and not folder.is_symlink():
                item=_snapshot(file_path,lane,'before_extraction')
                extraction.append(item);snapshots.append(item)
        if nonterminal and extraction:
            newest=max(extraction,key=lambda x:x['mtime'])
            newest['protected']=True;newest['reason']='lane_has_nonterminal_batch'
        for folder in lane_root.glob('qwen-inbox-*'):
            file_path=folder/'before-import.sqlite3'
            receipt=folder/'backup-complete.json'
            if not (file_path.is_file() and receipt.is_file()) or folder.is_symlink():
                continue
            state=states.get(folder.name)
            item=_snapshot(file_path,lane,'before_import',state)
            if state not in FINISHED:
                item['protected']=True;item['reason']='nonterminal_or_unknown_batch'
            snapshots.append(item)
    return snapshots


def _delete(item):
    file_path=item['file'];receipt=item['receipt']
    if file_path.is_symlink() or receipt.is_symlink():
        raise ValueError('Refusing to prune symlinked snapshot artifact')
    if file_path.exists():
        file_path.unlink()
    if receipt.exists():
        receipt.unlink()
    if item['kind']=='before_extraction':
        try:file_path.parent.rmdir()
        except OSError:pass


def prune(root=ROOT,cap_bytes=CAP_BYTES,min_history_per_lane=MIN_HISTORY_PER_LANE,
          young_seconds=YOUNG_PROTECTION_SECONDS,now=None,dry_run=False):
    root=Path(root);now=time.time() if now is None else float(now)
    snapshots=discover(root)
    before=sum(item['bytes'] for item in snapshots)
    lanes=sorted({item['lane'] for item in snapshots})
    for lane in lanes:
        historical=[x for x in snapshots if x['lane']==lane and not x['protected']]
        historical.sort(key=lambda x:x['mtime'],reverse=True)
        for item in historical[:max(0,int(min_history_per_lane))]:
            item['protected']=True;item['reason']='latest_lane_history'
    for item in snapshots:
        if not item['protected'] and now-item['mtime']<young_seconds:
            item['protected']=True;item['reason']='young_snapshot'
    deleted=[];remaining=before
    # "One recent point per lane" is the normal policy: all older complete
    # historical copies are disposable even while below the global cap.
    eligible=sorted((x for x in snapshots if not x['protected']),key=lambda x:x['mtime'])
    for item in eligible:
        if not dry_run:_delete(item)
        remaining-=item['bytes'];deleted.append(item)
    # The 5 GiB budget is a hard ceiling for historical snapshots. If lane
    # minima alone exceed it, sacrifice the oldest historical minima; active
    # or very recent snapshots stay protected to avoid racing a live batch.
    if remaining>cap_bytes:
        soft=sorted((x for x in snapshots if x['protected'] and
                     x['reason']=='latest_lane_history'),key=lambda x:x['mtime'])
        for item in soft:
            if remaining<=cap_bytes:break
            if not dry_run:_delete(item)
            remaining-=item['bytes'];deleted.append(item)
            item['protected']=False;item['reason']='pruned_for_global_cap'
    report={
        'state':'ok' if remaining<=cap_bytes else 'over_budget_protected',
        'cap_bytes':int(cap_bytes),'before_bytes':before,'after_bytes':max(0,remaining),
        'deleted_bytes':sum(x['bytes'] for x in deleted),'deleted_count':len(deleted),
        'snapshot_count_before':len(snapshots),
        'protected_count':sum(1 for x in snapshots if x['protected']),
        'lanes':lanes,'dry_run':bool(dry_run),'at':now,
    }
    out=root/'state/kaggle-month-dispatch/snapshot-retention.json'
    if not dry_run:
        out.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(out,report)
    return report


def locked_prune(root=ROOT,**kwargs):
    import fcntl
    root=Path(root);folder=root/'state/kaggle-month-dispatch'
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'snapshot-retention.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {'state':'busy','deleted_count':0,'deleted_bytes':0}
        return prune(root=root,**kwargs)


if __name__=='__main__':
    print(json.dumps(locked_prune(),ensure_ascii=False))
