"""Explicit bounded Inbox CLI: extract -> immutable Kaggle job -> validated import.

Run with the existing Inbox Python; the Kaggle CLI uses its own interpreter.
No website restart, paid provider call, or scheduler activation occurs here.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import sqlite3
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from work_admission import check,options,async_call,http_client

from batch_control import Controller, RetiredManifest, TERMINAL, atomic_json, digest
from build_manifest import build
from live_scope import resolve_entry_ids
from import_results import import_validated
from validate_business import validate
from queue_dispatch import claimed_entries, required_roots, DispatchBlocked, defer_unresolved, recovery_generation


def validate_model_config(config,versions):
    if config.get('split_mode','layer') not in ('layer','tensor'):
        raise ValueError('Unsupported GPU split mode')
    if config.get('ubatch_size',128) not in (128,512):
        raise ValueError('Unsupported microbatch size')
    if config.get('context_size',65536)!=65536:
        raise ValueError('Cloud business requests require a full 65536-token context')
    if config.get('parallel_requests',1) not in (1,2):
        raise ValueError('Unsupported parallel request count')
    if versions.get('dataset_source')!=config['model_dataset']:
        raise ValueError('Pinned model and attached dataset do not match')


def upstream_hash(entry):
    return digest({key:entry.get(key) for key in ('id','user_id','title','url','content')})


def resolve_recovery(control,database,batch,replacements, *,admission=None):
    original=control.manifest(batch)
    candidates=[original]
    for child_id in replacements:
        child=control.manifest(child_id)
        if child.get('resume_of')!=batch or child['model']!=original['model']:
            raise ValueError('Replacement is not a same-model direct recovery')
        candidates.append(child)
    coverage={}
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        for item in original['items']:
            for candidate in candidates:
                for done in candidate['items']:
                    if done['id']!=item['id'] or done['source_refs']!=item['source_refs']:
                        continue
                    receipt=db.execute('SELECT input_hash FROM kaggle_imports WHERE batch_id=? AND item_id=?',
                        (candidate['batch_id'],done['id'])).fetchone()
                    if receipt and receipt[0]==done['input_hash']:
                        coverage[item['id']]=candidate['batch_id']
            if item['id'] not in coverage:
                raise ValueError('Original item has no verified import receipt: '+item['id'])
    check(admission)
    atomic_json(control.root/batch/'recovery-resolution.json',{'batch_id':batch,'coverage':coverage})
    control._set(batch,'resolved')
    return {'batch_id':batch,'state':'resolved','coverage':coverage}


def backup_before_import(database,folder):
    target=folder/'before-import.sqlite3';receipt=folder/'backup-complete.json'
    if target.exists() and receipt.exists():return
    # A crash must not leave a zero-byte target that blocks this batch forever.
    # Preserve any old unreceipted artifact; publish only a verified SQLite copy.
    if target.exists():target.rename(folder/('incomplete-backup-'+str(time.time_ns())+'.sqlite3'))
    temporary=folder/('backup-'+str(os.getpid())+'.partial')
    source=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
    backup=sqlite3.connect(temporary,timeout=15);deadline=time.monotonic()+60
    def progress(status,remaining,total):
        if time.monotonic()>deadline:raise TimeoutError('Database backup exceeded deadline')
    try:
        source.backup(backup,pages=256,progress=progress,sleep=.01)
        if backup.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Backup integrity failure')
    finally:backup.close();source.close()
    os.replace(temporary,target)
    atomic_json(receipt,{'completed':time.time(),'bytes':target.stat().st_size})


def load_inbox(source, *,admission=None):
    check(admission)
    sys.path.insert(0,str(Path(source).resolve()))
    import core
    import worker
    import card_translation
    from initialize_secrets import read_env
    check(admission)
    cfg=core.settings()
    if cfg['enabled'] or cfg.get('translation_enabled',True):
        raise RuntimeError('Disable the existing paid workers before running the Kaggle bridge')
    # Only this dedicated CLI process changes its environment. Never source ai.env.
    os.environ.pop('ARK_API_KEY',None)
    check(admission)
    values=read_env('ai.env')
    os.environ['MINIFLUX_API_KEY']=values['MINIFLUX_API_KEY']
    if values.get('AI_NEWS_OUTBOUND_PROXY'):
        os.environ['AI_NEWS_OUTBOUND_PROXY']=values['AI_NEWS_OUTBOUND_PROXY']
    return core,worker,card_translation


async def prepare_sample(source, limit, excluded_entry_ids=(), allowed_entry_ids=None, analysis_only=False, independent_cards=False,lease_owner=None,on_claimed=None,admission=None):
    check(admission)
    import httpx
    core,worker,cards=load_inbox(source,**options(admission))
    from content_input import content_text,is_our_social_feed
    from product_source import is_product_entry
    from prepared_content import apply as apply_prepared
    check(admission)
    cfg=core.settings()
    # model_payload's slice now preserves the complete input, without saving settings.
    extraction_cfg={**cfg,'max_chars':sys.maxsize}
    excluded=sorted(set(int(value) for value in excluded_entry_ids))
    exclusion=(' AND a.entry_id NOT IN ('+','.join('?' for _ in excluded)+')') if excluded else ''
    allowed=sorted(set(int(value) for value in allowed_entry_ids)) if allowed_entry_ids is not None else None
    inclusion=(' AND a.entry_id IN ('+','.join('?' for _ in allowed)+')') if allowed else (' AND 0' if allowed is not None else '')
    if analysis_only:
        inclusion+=" AND a.state!='done'"
    translation_state="a.state NOT IN ('removed','requires_source_review')" if independent_cards and not analysis_only else "a.state='done'"
    check(admission)
    with core.connect() as db:
        if lease_owner:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS kaggle_prepare_leases (entry_id INTEGER PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL)')
            db.execute('DELETE FROM kaggle_prepare_leases WHERE expires<=?',(time.time(),))
            inclusion+=" AND NOT EXISTS (SELECT 1 FROM kaggle_prepare_leases l WHERE l.entry_id=a.entry_id)"
        rows=[dict(row) for row in db.execute('''SELECT a.* FROM analyses a
            LEFT JOIN card_translations c ON c.entry_id=a.entry_id AND c.user_id=a.user_id
            WHERE ((a.state IN ('pending','waiting_model','budget_paused','fetch_error','ai_error')
                   AND a.next_try<=? AND a.attempts<3)
               OR ('''+translation_state+''' AND c.status IN ('pending','error','budget_paused','waiting_model')
                   AND c.next_try<=? AND c.attempts<3))'''+exclusion+inclusion+'''
            ORDER BY CASE WHEN c.status IN ('pending','error','budget_paused','waiting_model')
                          THEN 0 ELSE 1 END,a.published_at DESC,a.entry_id DESC LIMIT ?''',
            (time.time(),time.time(),*excluded,*(allowed or []),limit))]
        retry_at=db.execute("""SELECT MIN(a.next_try) FROM analyses a WHERE
            a.state IN ('pending','waiting_model','budget_paused','fetch_error','ai_error')
            AND a.attempts<3 AND a.next_try>?"""+exclusion+inclusion,
            (time.time(),*excluded,*(allowed or []))).fetchone()[0]
        card_retry=db.execute('''SELECT MIN(c.next_try) FROM analyses a JOIN card_translations c ON c.entry_id=a.entry_id AND c.user_id=a.user_id WHERE c.status IN ('pending','error','budget_paused','waiting_model') AND c.attempts<3 AND c.next_try>?'''+exclusion+inclusion,(time.time(),*excluded,*(allowed or []))).fetchone()[0]
        retry_at=min([v for v in (retry_at,card_retry) if v],default=None)
        if lease_owner:db.executemany('INSERT INTO kaggle_prepare_leases VALUES (?,?,?)',[(row['entry_id'],lease_owner,time.time()+660) for row in rows])
    if on_claimed:on_claimed()  # Release global lock only AFTER claim transaction commits.
    samples=[]
    skipped=[]
    def append_card_only(entry,row):
        if not independent_cards or analysis_only:
            return
        check(admission)
        cards.enqueue([entry],**options(admission))
        check(admission)
        with core.connect() as db:
            card=db.execute('SELECT * FROM card_translations WHERE entry_id=?',(row['entry_id'],)).fetchone()
        if card and card['status'] not in ('done','native') and card['attempts']<3 and card['next_try']<=time.time():
            samples.append({**row,'card':dict(card),'skip_analysis':True,'upstream_hash':upstream_hash(entry)})
    async def process_row(client,row):
        analysis_needed=row['state']!='done'
        if independent_cards:
            analysis_needed=(row['state'] in ('pending','waiting_model','budget_paused','fetch_error','ai_error')
                             and row['attempts']<3 and row['next_try']<=time.time())
        refreshed=row
        entry=apply_prepared(await async_call(admission,worker.mf_get,client,f"/v1/entries/{row['entry_id']}"),**options(admission))
        # Publishers edit headlines after RSS discovery. Refresh an unclaimed,
        # unfinished row only when the article ID, owner and URL still match.
        # The caller backs up the database before extraction; the new body is
        # fetched below and immutable manifests retain their strict checks.
        if (analysis_needed and entry['id']==row['entry_id'] and
            entry['user_id']==row['user_id'] and entry['url']==row['url'] and
            entry['title']!=row['title']):
            check(admission)
            with core.connect() as db:
                changed=db.execute('''UPDATE analyses SET title=?,updated_at=?
                    WHERE entry_id=? AND user_id=? AND url=? AND title=? AND state=?
                    AND content_hash IS ? AND source_text IS ?''',
                    (entry['title'],time.time(),row['entry_id'],row['user_id'],row['url'],
                     row['title'],row['state'],row['content_hash'],row['source_text'])).rowcount
            if changed!=1:
                skipped.append({'entry_id':row['entry_id'],'state':'source_changed_during_title_refresh'})
                return
            row['title']=entry['title']
        if (entry['user_id']!=row['user_id'] or (analysis_needed and
            (entry['title']!=row['title'] or entry['url']!=row['url']))):
            skipped.append({'entry_id':row['entry_id'],'state':'upstream_identity_changed'})
            check(admission)
            with core.connect() as db:
                db.execute('''UPDATE analyses SET state='requires_source_review',error='upstream_identity_changed',updated_at=?
                    WHERE entry_id=? AND user_id=? AND state=? AND state!='done' AND title=? AND url=? AND content_hash IS ?''',
                    (time.time(),row['entry_id'],row['user_id'],row['state'],row['title'],row['url'],row['content_hash']))
                if entry['user_id']!=row['user_id']:
                    db.execute("UPDATE card_translations SET status='requires_source_review' WHERE entry_id=? AND user_id=? AND status NOT IN ('done','native')",
                        (row['entry_id'],row['user_id']))
            return
        specialized=is_our_social_feed(entry.get('feed',{}).get('feed_url','')) or is_product_entry(entry)
        if analysis_needed and specialized:
            # Existing extraction returns before reserve_budget/post when no API key exists.
            await asyncio.wait_for(worker.process_one(client,row,extraction_cfg,**options(admission)),timeout=180)
            check(admission)
            with core.connect() as db:
                refreshed=dict(db.execute('SELECT * FROM analyses WHERE entry_id=?',(row['entry_id'],)).fetchone())
            if refreshed['state']!='waiting_model' or refreshed['truncated']:
                skipped.append({'entry_id':row['entry_id'],'state':refreshed['state']})
                append_card_only(entry,refreshed)
                return
            entry=apply_prepared(await async_call(admission,worker.mf_get,client,f"/v1/entries/{row['entry_id']}"),**options(admission))
            if (entry['user_id']!=refreshed['user_id'] or entry['title']!=refreshed['title']
                or entry['url']!=refreshed['url']
                or content_text(entry.get('content') or '')[0]!=refreshed['source_text']):
                skipped.append({'entry_id':row['entry_id'],'state':'upstream_changed_during_extract'})
                return
        if analysis_needed and not specialized:
            # Miniflux's nonempty result is not proof of a complete article.
            # Fetch the publisher body by its checked site rule before scoring.
            from fulltext_source import fetch,FulltextUnavailable
            from adafruit_source import is_adafruit,resolve,OriginalUnavailable
            try:
                if is_adafruit(entry['url']):
                    if entry.get('prepared_source')=='adafruit_linked_original':
                        text,images=content_text(entry['content'])
                        fulltext={'source_text':text,'image_count':images,'html':entry['content'],'receipt':entry['fulltext_receipt']}
                    else:
                        fulltext=await async_call(admission,resolve,entry,**options(admission))
                else:
                    fulltext=await async_call(admission,fetch,entry['url'],**options(admission))
            except (FulltextUnavailable,OriginalUnavailable) as exc:
                reason=str(exc)
                retryable=reason.startswith(('original_fetch_','original_http_','reader_http_','reader_returned_challenge'))
                failure_state='fetch_error' if retryable else 'requires_fulltext_adapter'
                skipped.append({'entry_id':row['entry_id'],'state':failure_state,'reason':reason})
                check(admission)
                with core.connect() as db:
                    db.execute("""UPDATE analyses SET state=?,error=?,updated_at=?,attempts=attempts+?,next_try=?
                        WHERE entry_id=? AND state=? AND content_hash IS ? AND source_text IS ?""",
                        (failure_state,reason,time.time(),int(retryable),time.time()+660 if retryable else 0,
                         row['entry_id'],row['state'],row['content_hash'],row['source_text']))
                append_card_only(entry,row)
                return
            current=apply_prepared(await async_call(admission,worker.mf_get,client,f"/v1/entries/{row['entry_id']}"),**options(admission))
            if upstream_hash(current)!=upstream_hash(entry):
                skipped.append({'entry_id':row['entry_id'],'state':'upstream_changed_during_fulltext'})
                return
            content_source=fulltext['receipt'].get('source','original_url_site_rule')
            source_content_hash=worker.hash_text(entry.get('content') or '')
            check(admission)
            with core.connect() as db:
                changed=db.execute("""UPDATE analyses SET source_text=?,source_chars=?,input_chars=?,
                    image_count=?,content_source=?,truncated=0,extracted_at=?,updated_at=?,
                    content_hash=?,state='waiting_model',error=NULL
                    WHERE entry_id=? AND user_id=? AND state=? AND content_hash IS ? AND source_text IS ?""",
                    (fulltext['source_text'],len(fulltext['source_text']),len(fulltext['source_text']),
                     fulltext['image_count'],content_source,time.time(),time.time(),source_content_hash,
                     row['entry_id'],row['user_id'],row['state'],row['content_hash'],row['source_text'])).rowcount
            if changed!=1:
                skipped.append({'entry_id':row['entry_id'],'state':'source_changed_during_fulltext'})
                return
            if content_source=='adafruit_linked_original' and not entry.get('prepared_source'):
                from prepared_content import remember
                remember(entry,fulltext['html'],content_source,fulltext['receipt'],**options(admission))
                entry=apply_prepared(entry,**options(admission))
            refreshed.update(source_text=fulltext['source_text'],source_chars=len(fulltext['source_text']),
                             input_chars=len(fulltext['source_text']),truncated=False,
                             content_hash=source_content_hash,state='waiting_model',
                             content_source=content_source,fulltext_receipt=fulltext['receipt'])
        check(admission)
        cards.enqueue([entry],**options(admission))
        check(admission)
        with core.connect() as db:
            card=db.execute('SELECT * FROM card_translations WHERE entry_id=?',(row['entry_id'],)).fetchone()
        refreshed['card']=dict(card) if card and card['status'] not in ('done','native') else None
        refreshed['skip_analysis']=not analysis_needed
        refreshed['upstream_hash']=upstream_hash(entry)
        samples.append(refreshed)
    async with http_client(admission,follow_redirects=False,trust_env=False,timeout=70) as client:
        for row in rows:
            check(admission)
            if lease_owner:
                check(admission)
                with core.connect() as db:
                    db.execute('UPDATE kaggle_prepare_leases SET expires=? WHERE owner=? AND expires>?',(time.time()+660,lease_owner,time.time()))
                    owned=db.execute('SELECT owner FROM kaggle_prepare_leases WHERE entry_id=? AND expires>?',(row['entry_id'],time.time())).fetchone()
                if not owned or owned[0]!=lease_owner:
                    skipped.append({'entry_id':row['entry_id'],'state':'preparation_lease_lost'});continue
            try:
                await asyncio.wait_for(process_row(client,row),timeout=210)
            except (httpx.HTTPError,TimeoutError,KeyError,TypeError,ValueError) as exc:
                # Source/one-record failures do not abandon other prepared inputs.
                transient=isinstance(exc,(httpx.HTTPError,TimeoutError))
                code='entry_fetch_'+type(exc).__name__ if transient else 'entry_validation_'+type(exc).__name__
                skipped.append({'entry_id':row['entry_id'],'state':'fetch_error' if transient else 'requires_source_review','reason':code})
                check(admission)
                with core.connect() as db:
                    db.execute("""UPDATE analyses SET state=?,error=?,attempts=attempts+1,next_try=?,updated_at=?
                        WHERE entry_id=? AND user_id=? AND state=? AND state!='done' AND content_hash IS ?""",
                        ('fetch_error' if transient else 'requires_source_review',code,time.time()+660,time.time(),row['entry_id'],row['user_id'],row['state'],row['content_hash']))
                    db.execute("""UPDATE card_translations SET status='error',error=?,attempts=attempts+1,next_try=?,updated_at=?
                        WHERE entry_id=? AND user_id=? AND status NOT IN ('done','native','requires_source_review')""",
                        (code,time.time()+660,time.time(),row['entry_id'],row['user_id']))
    if lease_owner:
        # A changing/invalid source must not remain forever at the head of the queue.
        snapshots={r['entry_id']:r for r in rows}
        check(admission)
        with core.connect() as db:
            for skipped_row in skipped:
                original=snapshots[skipped_row['entry_id']]
                code=skipped_row.get('reason') or skipped_row['state']
                db.execute('''UPDATE analyses SET state=CASE WHEN attempts>=2 THEN 'requires_source_review' ELSE 'fetch_error' END,
                    attempts=attempts+1,next_try=?,updated_at=?,error=?
                    WHERE entry_id=? AND user_id=? AND state IN ('pending','waiting_model','budget_paused','fetch_error','ai_error')
                    AND next_try<=? AND content_hash IS ? AND source_text IS ?
                    AND EXISTS (SELECT 1 FROM kaggle_prepare_leases l WHERE l.entry_id=analyses.entry_id AND l.owner=? AND l.expires>?)''',
                    (time.time()+660,time.time(),code,original['entry_id'],original['user_id'],time.time(),
                     original['content_hash'],original['source_text'],lease_owner,time.time()))
    return {'settings':{key:cfg[key] for key in ('prompt','max_output_tokens')},
            'translation_prompt':cards.PROMPT,'samples':samples,'skipped':skipped,'considered':len(rows),
            'next_retry_at':retry_at}


class UpstreamVerification(set):
    def __init__(self):
        super().__init__();self.unavailable=set()


async def verify_upstream(source,manifest, *,admission=None):
    check(admission)
    import httpx
    core,worker,cards=load_inbox(source,**options(admission))
    from prepared_content import apply as apply_prepared
    refs={ref['entry_id']:ref for item in manifest['items'] if item.get('kind')!='exception' for ref in item['source_refs']}
    unchanged=UpstreamVerification()
    async with http_client(admission,follow_redirects=False,trust_env=False,timeout=70) as client:
        for entry_id,ref in refs.items():
            check(admission)
            try:
                entry=apply_prepared(await async_call(admission,worker.mf_get,client,f'/v1/entries/{entry_id}'),**options(admission))
            except (httpx.HTTPError,TimeoutError,KeyError,TypeError,ValueError) as exc:
                if not isinstance(exc,httpx.HTTPStatusError) or exc.response.status_code not in (404,410):
                    unchanged.unavailable.add(entry_id)
                continue
            if ref.get('upstream_hash') and upstream_hash(entry)==ref['upstream_hash']:
                unchanged.add(entry_id)
    return unchanged


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('action',choices=['init','prepare','advance','recover','resolve'])
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--batch')
    parser.add_argument('--expected-config-sha256',help='Parent config identity; not an authorization token')
    parser.add_argument('--replacement',action='append',default=[])
    args=parser.parse_args()
    if not 1<=args.limit<=200:
        raise ValueError('A batch must contain 1..200 articles')
    from dispatch_policy import load_config,ConfigGuard,manual_target,DispatchStopped
    config=load_config(args.config)
    global BRIDGE_CONTEXT
    BRIDGE_CONTEXT={'config':config,'config_path':args.config,'action':args.action,'batch':args.batch}
    if args.action=='init':
        Controller(Path(config['state_root']),config['owner'],initialize=True)
        print(json.dumps({'state':'initialized','gpu_started':False}))
        return
    recovery_only=args.action=='recover'
    guard=ConfigGuard(args.config,config,manual_recovery=recovery_only,expected_fingerprint=args.expected_config_sha256)
    BRIDGE_CONTEXT['guard']=guard
    guard()
    root=Path(config['state_root'])
    if recovery_only:
        manual_target(root,args.batch,time.time())
        config={**config,'drain_queue':False}
    roots=required_roots(config)
    control=Controller(root,config['owner'],kaggle_python=config['kaggle_python'],required_roots=roots,admission=guard,recovery_batch=args.batch if recovery_only else None)
    if args.action=='prepare':
        pending=control.next_pending()
        if not pending or control.row(pending)['state']=='prepared':
            claimed_entries(roots)
    elif args.action=='advance' and args.batch and control.row(args.batch)['state']=='prepared':
        claimed_entries(roots)
    guard()
    os.environ['KAGGLE_API_TOKEN']=config['token_file']
    sys.path.insert(0,str(Path(config['source']).resolve()))
    from initialize_secrets import read_env
    proxy=read_env('ai.env').get('AI_NEWS_OUTBOUND_PROXY')
    if proxy:
        for name in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):
            os.environ[name]=proxy
    os.umask(0o077)
    import fcntl
    coordination=Path(config.get('coordination_root',root)) if args.action=='prepare' else root
    coordination.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (coordination/'bridge.lock').open('a') as lock:
        # Both accounts claim articles under the same lock. The subprocess timeout
        # bounds waiting; a second worker must not fail merely because extraction is busy.
        fcntl.flock(lock,fcntl.LOCK_EX)
        guard()
        if recovery_only:manual_target(root,args.batch,time.time())
        if args.action=='resolve':
            if not args.batch:
                raise ValueError('resolve requires --batch')
            print(json.dumps(resolve_recovery(control,config['database'],args.batch,args.replacement,admission=guard)))
            return
        if args.action=='prepare':
            outstanding=control.next_pending()
            if outstanding:
                print(json.dumps({'existing_batch':outstanding}))
                return
            guard()
            claimed_entries(roots)
            from quota_guard import query_client
            quota_gate=query_client(control.client)
            if not quota_gate['allowed']:
                print(json.dumps({'state':quota_gate['state'],'quota_gate':quota_gate,'gpu_started':False}))
                return
            guard()
            versions=json.loads(Path(config['versions']).read_text(encoding='utf-8'))
            validate_model_config(config,versions)
            extraction_backup=root/('before-extraction-'+str(time.time_ns()))
            guard()
            extraction_backup.mkdir(mode=0o700)
            guard()
            backup_before_import(config['database'],extraction_backup)
            guard()
            claimed=claimed_entries(roots)
            guard()
            allowed=resolve_entry_ids(config,admission=guard)
            import uuid,atexit
            lease_owner=config['owner']+'-'+uuid.uuid4().hex
            def release_leases():
                # Cleanup must not recreate a missing source DB or leak a raw
                # filesystem traceback from an atexit callback.
                try:
                    guard()
                    with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=rw',uri=True,timeout=15) as db:
                        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kaggle_prepare_leases'").fetchone():
                            db.execute('DELETE FROM kaggle_prepare_leases WHERE owner=?',(lease_owner,))
                except (sqlite3.Error,OSError,DispatchStopped):
                    pass
            atexit.register(release_leases)
            # Lease insertion and candidate selection use one SQLite transaction.
            # Retain the per-account cycle lock; release only cross-account lock.
            sample=asyncio.run(prepare_sample(config['source'],args.limit,claimed,allowed,config.get('analysis_only',False),config.get('independent_cards',False),lease_owner=lease_owner,on_claimed=lambda:fcntl.flock(lock,fcntl.LOCK_UN),admission=guard))
            fcntl.flock(lock,fcntl.LOCK_EX)
            guard()
            claimed=claimed_entries(roots)
            with sqlite3.connect(config['database'],timeout=15) as db:
                owned={row[0] for row in db.execute('SELECT entry_id FROM kaggle_prepare_leases WHERE owner=? AND expires>?',(lease_owner,time.time()))}
            sample['samples']=[row for row in sample['samples'] if row['entry_id'] in owned and row['entry_id'] not in claimed]

            atomic_json(root/'latest-extraction-report.json',{'at':time.time(),
                'considered':sample['considered'],'selected':len(sample['samples']),'skipped':sample['skipped']})
            if config.get('analysis_only',False):
                for row in sample['samples']:
                    row['card']=None
            attempt=1+max([max(row.get('attempts') or 0,(row.get('card') or {}).get('attempts') or 0)
                           for row in sample['samples']],default=0)
            manifest=build(sample,versions,config['runtime_sha256'],config['runtime_source'],
                           attempt=attempt,
                           context_size=config.get('context_size',65536),
                           model_dataset=config['model_dataset'],runtime_dataset=config.get('runtime_dataset'))
            manifest['parallel_requests']=config.get('parallel_requests',1)
            manifest['split_mode']=config.get('split_mode','layer')
            manifest['ubatch_size']=config.get('ubatch_size',128)
            if config.get('qwen_exception_review'):
                claimed=claimed_entries(roots)
                with sqlite3.connect(config['database'],timeout=15) as db:
                    claimed.update(row[0] for row in db.execute('SELECT entry_id FROM kaggle_prepare_leases WHERE owner<>? AND expires>?',(lease_owner,time.time())))
                from qwen_exceptions import prepare as prepare_exceptions
                selected={ref['entry_id'] for item in manifest['items'] for ref in item['source_refs']}
                manifest['items']+=prepare_exceptions(config['database'],allowed,claimed|selected,
                    limit=2 if manifest['items'] else 8,admission=guard)
            with control.db() as db:
                retired=db.execute("SELECT COUNT(*) FROM batches WHERE state='retired'").fetchone()[0]
            if retired:
                manifest['dispatch_generation']=retired
            generation=recovery_generation(config['database'],manifest)
            if generation:manifest['infrastructure_retry_generation']=generation
            guard()
            claimed_entries(roots)
            try:
                batch=control.prepare(manifest,Path(__file__).with_name('batch_runner.py').read_text(encoding='utf-8'))
            except RetiredManifest as exc:
                print(json.dumps({'state':'retired_manifest_requires_new_attempt','batch_id':exc.batch_id,'gpu_started':False,'recovery_required':True}))
                return
            if batch:
                atomic_json(root/batch/'extraction-report.json',{'selected':len(sample['samples']),'skipped':sample['skipped']})
            release_leases()
            retry_times=[t for t in (sample['next_retry_at'],control.next_retry()) if t]
            sample['next_retry_at']=min(retry_times,default=None)
            print(json.dumps({'batch_id':batch,'selected':len(sample['samples']),'skipped':len(sample['skipped']),
                              'considered':sample['considered'],'skipped_fingerprint':digest(sample['skipped']),
                              'next_retry_at':sample['next_retry_at']}))
            return
        if not args.batch:
            raise ValueError('advance requires --batch')
        row=control.row(args.batch)
        if row['state'] in {'imported','resolved','retired'}:
            print(json.dumps(row))
            return
        if row['state']=='prepared':
            if recovery_only:raise DispatchStopped('manual_recovery_cannot_submit')
            guard()
            print(json.dumps(control.submit(args.batch)))
            return
        guard()
        row=(control.status(args.batch,allow_retirement=False) if recovery_only
             else control.status(args.batch))
        if row['state']=='retired':
            print(json.dumps(row));return
        if row['remote_status'] not in TERMINAL:
            print(json.dumps(row))
            return
        try:
            guard()
            evidence=control.download(args.batch,salvage=True)
        except DispatchStopped:
            raise
        except Exception as exc:
            from recovery_policy import exception_code
            print(json.dumps(control.defer_local(args.batch,exception_code(exc))))
            return
        infrastructure_ids=set(evidence['missing_ids']) | {row['id'] for row in evidence['results'] if row.get('status')=='error' and row.get('error') in {'TimeoutError','ReadTimeout','ConnectTimeout','ConnectionError','RuntimeError','Batch deadline reached'}}
        manifest=control.manifest(args.batch)
        if not evidence['results'] and manifest['items'] and not config.get('exception_audit_root'):
            raise RuntimeError('Batch produced no results; inspect runtime before starting more GPU jobs')
        business={**manifest,'items':[item for item in manifest['items'] if item.get('kind')!='exception']}
        business_ids={item['id'] for item in business['items']}
        report=validate(business,[row for row in evidence['results'] if row['id'] in business_ids],config['source'])
        folder=root/args.batch
        guard()
        atomic_json(folder/'business-validation.json',report)
        guard()
        unchanged=asyncio.run(verify_upstream(config['source'],manifest,admission=guard))
        guard()
        backup_before_import(config['database'],folder)
        guard()
        outcome=import_validated(config['database'],business,report,unchanged,admission=guard)
        if any(item['kind']=='exception' for item in manifest['items']):
            from exception_audit import Audit
            from qwen_exceptions import apply as apply_exceptions
            guard()
            outcome['items']+=apply_exceptions(config['database'],manifest,evidence['results'],Audit(config['exception_audit_root']),admission=guard)
        guard()
        atomic_json(folder/'import-report.json',outcome)
        unresolved=[item for item in outcome['items'] if item['state'] not in
                    ('imported','already_imported','existing_result_preserved')]
        unavailable=getattr(unchanged,'unavailable',set())
        waiting={item['id'] for item in business['items']
                 if any(ref['entry_id'] in unavailable for ref in item['source_refs'])
                 and any(x['id']==item['id'] and x['state']=='upstream_changed_or_unavailable' for x in outcome['items'])}
        if waiting:
            # Keep valid generated output and retry only local validation/import.
            # Never spend another GPU run merely because the source API timed out.
            other={**business,'items':[item for item in business['items'] if item['id'] not in waiting]}
            guard()
            defer_unresolved(config['database'],other,outcome,delay=int(config.get('retry_delay_seconds',660)),infrastructure_ids=infrastructure_ids,admission=guard)
            guard()
            atomic_json(folder/'import-wait.json',{'waiting_ids':sorted(waiting),'at':time.time()})
            print(json.dumps(control.defer_local(args.batch,'upstream_validation')))
            return
        if not unresolved and not evidence['missing_ids']:
            control._set(args.batch,'imported')
        elif config.get('drain_queue',False):
            retry_delay=int(config.get('retry_delay_seconds',21600))
            if retry_delay<660:
                raise ValueError('Retry interval must be at least 660 seconds')
            guard()
            deferred=defer_unresolved(config['database'],business,outcome,delay=retry_delay,infrastructure_ids=infrastructure_ids,admission=guard)
            guard()
            atomic_json(folder/'retry-resolution.json',{'batch_id':args.batch,'actions':deferred,
                                                       'disposition':'valid_imports_kept_failed_inputs_deferred'})
            control._set(args.batch,'resolved')
        if not evidence['results'] and manifest['items'] and config.get('exception_audit_root'):
            from exception_audit import Audit
            from recovery_policy import ProviderError
            guard()
            Audit(config['exception_audit_root']).append('empty_remote_batch',batch_id=args.batch,
                owner=config['owner'],remote_status=row['remote_status'],action='released_with_bounded_retries')
            raise ProviderError('empty_output')
        print(json.dumps({'batch_id':args.batch,'valid':report['valid'],'invalid':report['invalid'],
                          'import_states':{state:sum(item['state']==state for item in outcome['items'])
                                           for state in {item['state'] for item in outcome['items']}},
                          'missing_ids':evidence['missing_ids']}))


def handle_failure(exc):
    from dispatch_policy import DispatchStopped,ConfigGuard
    if isinstance(exc,DispatchStopped):return exc.report()
    from recovery_policy import exception_code
    code=exception_code(exc)
    context=globals().get('BRIDGE_CONTEXT',{})
    cfg=context.get('config',{})
    guard=context.get('guard')
    if guard is None and context.get('config_path'):
        guard=ConfigGuard(context['config_path'],cfg,manual_recovery=context.get('action')=='recover')
    try:check(guard)
    except DispatchStopped as stopped:return stopped.report()
    if cfg.get('exception_audit_root'):
        from exception_audit import Audit,clean
        import traceback
        frames=traceback.extract_tb(exc.__traceback__)
        try:
            check(guard)
            Audit(cfg['exception_audit_root']).append('bridge_failure',owner=cfg.get('owner'),
                action=context.get('action'),batch_id=context.get('batch'),code=code,
                exception_type=type(exc).__name__,detail=getattr(exc,'detail',None),
                frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in frames[-5:]])
        except DispatchStopped as stopped:return stopped.report()
        except Exception:pass
    if isinstance(exc,DispatchBlocked):
        from queue_dispatch import block_with_backoff
        try:check(guard)
        except DispatchStopped as stopped:return stopped.report()
        return block_with_backoff(cfg.get('state_root'),exc)
    local_recovery=None
    if context.get('action')=='advance' and context.get('batch') and cfg.get('state_root'):
        try:
            guard=ConfigGuard(context['config_path'],cfg)
            guard()
            control=Controller(cfg['state_root'],cfg['owner'],kaggle_python=cfg['kaggle_python'],admission=guard)
            row=control.row(context['batch'])
            if row['remote_status'] in TERMINAL and row['state'] not in {'imported','resolved','retired'}:
                local_recovery=control.defer_local(context['batch'],code)
        except Exception:pass
    return local_recovery or {'recovery_error':code,'error_type':type(exc).__name__}


if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(handle_failure(exc)))
