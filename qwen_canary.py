"""Operator-authorized fault injection; real Qwen and executor, isolated config only."""
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import sys
import time
import uuid

import ops_runbooks as books
from ops_review import ops_read, validate_operations_result

PRIVATE = Path('/home/ubuntu/ai-news/.private/recovery-agent')
SOURCE = Path('/home/ubuntu/website-release-20261004/reader-3349cf2-source/src/kaggle_batch')


def atomic(path, value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    if len(raw)>65536:raise ValueError('evidence_size')
    temporary=path.with_suffix('.pending')
    if path.is_symlink() or temporary.is_symlink():raise ValueError('symlink')
    with temporary.open('wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    temporary.chmod(0o600);temporary.replace(path)


def arm():
    os.umask(0o077)
    now=int(time.time());home=books.CANARY
    if home.is_symlink():raise ValueError('canary_path')
    home.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (PRIVATE/'tick.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if books.canary_marker(now):raise ValueError('probe_already_armed')
        # The live configuration is read for proof only; it is never renamed or edited.
        live_before=books.digest_bytes(books.LOG_CONFIG.read_bytes())
        target=home/'newapi-logrotate.conf';approved=home/'approved-newapi-logrotate.conf'
        for path in (target,approved):
            if path.is_symlink():raise ValueError('probe_symlink')
            if path.exists() and path.read_bytes()!=books.LOG_TEMPLATE:raise ValueError('unknown_probe_file')
            if not path.exists():
                with path.open('xb') as stream:stream.write(books.LOG_TEMPLATE)
                path.chmod(0o600)
        run_id=uuid.uuid4().hex
        history=home/'history';history.mkdir(mode=0o700,exist_ok=True)
        if history.is_symlink():raise ValueError('history_symlink')
        before=history/(run_id+'.before.conf')
        target.replace(before)  # Preserve the original in place of deletion.
        marker={'armed':True,'run_id':run_id,'created_at':now,'deadline':now+7200,
                'scope':'ops_canary','expected_sha256':books.TEMPLATE_SHA256,
                'live_config_sha256_before':live_before,'original_file':before.name}
        atomic(home/'operator-fault.json',marker)
        result={'state':'armed','run_id':run_id,'injected_at':now,'fault':'isolated_logrotate_config_missing',
                'missing':not target.exists(),'before_sha256':books.digest_bytes(before.read_bytes()),
                'live_config_unchanged':books.digest_bytes(books.LOG_CONFIG.read_bytes())==live_before}
        atomic(home/'injection-proof.json',result)
        return result


def real_two_call_evidence(attempt):
    """Require measured inference and thinking on both requests, not approval text alone."""
    for name in ('planner','reviewer'):
        trace=attempt.get(name,{})
        usage=trace.get('usage')
        if not isinstance(usage,dict):return False
        if any(type(usage.get(k)) is not int or usage[k]<=0 for k in ('prompt_tokens','completion_tokens')):return False
        if trace.get('thinking_enabled') is not True or trace.get('reasoning_budget')!=-1:return False
        if type(trace.get('reasoning_chars')) is not int or trace['reasoning_chars']<=0:return False
    return attempt['planner'].get('messages_sha256')!=attempt['reviewer'].get('messages_sha256')


def status():
    marker_path=books.CANARY/'operator-fault.json'
    if not marker_path.exists():return {'state':'not_armed'}
    marker=ops_read(marker_path,4096);marker_hash=books.digest_bytes(marker_path.read_bytes())
    if not re.fullmatch(r'[0-9a-f]{32}',marker.get('run_id','')):raise ValueError('run_id')
    complete=books.CANARY/'probe-complete.json'
    if complete.exists():
        closed=ops_read(complete,4096)
        if closed.get('verified') is True and closed.get('run_id')==marker['run_id']:
            archived=ops_read(books.CANARY/'history'/(marker['run_id']+'.verification.json'))
            target=books.CANARY/'newapi-logrotate.conf'
            return {**archived,'historical':True,'last_verified_at':archived['checked_at'],
                    'checked_at':int(time.time()),'current_file_matches':target.exists() and target.read_bytes()==books.LOG_TEMPLATE}
    evidence={'checked_at':int(time.time()),'run_id':marker['run_id'],'state':'awaiting_batch',
              'injected_at':marker['created_at'],'deadline':marker['deadline'],'scope':'ops_canary'}
    target=books.CANARY/'newapi-logrotate.conf'
    evidence['file_exists']=target.exists()
    evidence['file_verified']=target.exists() and target.read_bytes()==books.LOG_TEMPLATE
    evidence['live_config_unchanged']=books.digest_bytes(books.LOG_CONFIG.read_bytes())==marker['live_config_sha256_before']
    config=ops_read('/home/ubuntu/ai-news/src/kaggle_batch/cloud-config-month-primary.json')
    root=Path(config['state_root']);sys.path.insert(0,str(SOURCE))
    from batch_control import digest
    with sqlite3.connect((root/'batches.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=3) as db:
        rows=db.execute('SELECT id,state,remote_status,manifest_hash FROM batches ORDER BY updated DESC LIMIT 32').fetchall()
    for batch_id,state,remote,expected_hash in rows:
        folder=root/batch_id;m=ops_read(folder/'manifest.json',32*1024*1024)
        snapshot=m.get('operations_review',{})
        incidents=[i for i in snapshot.get('incidents',[]) if i['target']=='ops_canary' and i['precondition'].get('state_sha256')==marker_hash]
        if not incidents:continue
        canonical={k:v for k,v in m.items() if k not in {'batch_id','manifest_hash'}}
        if digest(canonical)!=expected_hash:raise ValueError('manifest_integrity')
        evidence.update(state='awaiting_model_result',batch_id=batch_id,batch_state=state,remote_status=remote,
                        manifest_hash=expected_hash,evidence_hash=snapshot['evidence_hash'],model=m['model']['filename'])
        output=folder/'output/operations-review.json'
        if not output.exists():break
        result=ops_read(output)
        approved,skipped=validate_operations_result(m,result,time.time())
        selected=[d for d in approved if d['target']=='ops_canary']
        attempts=[a for a in result['attempts'] if a['incident_id']==incidents[0]['id']]
        two_calls=bool(attempts) and real_two_call_evidence(attempts[0])
        summary=ops_read(folder/'output/summary.json',1024*1024)
        runtime_ok=(summary.get('stage')=='finished' and summary.get('process_cleanup_complete') is True
                    and summary.get('model',{}).get('sha256')==m['model']['sha256'])
        evidence.update(state='awaiting_executor',approved=bool(selected),skipped=skipped,
                        two_real_thinking_calls=two_calls,runtime_finished_and_model_bound=runtime_ok,
                        model_attempts=attempts,model_result_sha256=books.digest_bytes(output.read_bytes()))
        receipt=folder/'operations-execution.json'
        if not receipt.exists():break
        executed=ops_read(receipt)
        outcomes=[o for o in executed.get('outcomes',[]) if o.get('target')=='ops_canary']
        evidence.update(execution_state=executed['state'],outcomes=outcomes,
                        execution_receipt_sha256=books.digest_bytes(receipt.read_bytes()))
        success=(bool(selected) and executed['state']=='processed' and any(o.get('state')=='repaired' and o.get('verified') is True and o.get('scope')=='ops_canary' for o in outcomes)
                 and two_calls and runtime_ok and evidence['file_verified'] and evidence['live_config_unchanged']
                 and target.stat().st_mtime>=marker['created_at'])
        evidence['state']='verified_real_repair' if success else 'not_repaired'
        if success:
            evidence['restored_sha256']=books.digest_bytes(target.read_bytes())
            evidence['restored_mtime']=int(target.stat().st_mtime)
        break
    atomic(books.CANARY/'verification.json',evidence)
    return evidence


def finish():
    evidence=status()
    if evidence.get('state')!='verified_real_repair':raise ValueError('real_repair_not_verified')
    marker=ops_read(books.CANARY/'operator-fault.json',4096)
    marker.update(armed=False,verified_at=int(time.time()))
    atomic(books.CANARY/'history'/(marker['run_id']+'.verification.json'),evidence)
    atomic(books.CANARY/'completed-marker.json',marker)
    # Keep the original marker bytes for hash-bound evidence; disarm separately.
    atomic(books.CANARY/'probe-complete.json',{'run_id':marker['run_id'],'at':int(time.time()),'verified':True})
    return evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('arm','status','finish'))
    args=parser.parse_args()
    print(json.dumps({'arm':arm,'status':status,'finish':finish}[args.action](),ensure_ascii=True,allow_nan=False))
