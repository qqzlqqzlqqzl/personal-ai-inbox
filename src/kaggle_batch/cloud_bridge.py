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

from batch_control import Controller, TERMINAL, atomic_json, digest
from build_manifest import build
from import_results import import_validated
from validate_business import validate
from queue_dispatch import claimed_entries, defer_unresolved


def validate_model_config(config,versions):
    if config.get('context_size',65536)!=65536:
        raise ValueError('Cloud business requests require a full 65536-token context')
    if config.get('parallel_requests',1) not in (1,2):
        raise ValueError('Unsupported parallel request count')
    if versions.get('dataset_source')!=config['model_dataset']:
        raise ValueError('Pinned model and attached dataset do not match')


def upstream_hash(entry):
    return digest({key:entry.get(key) for key in ('id','user_id','title','url','content')})


def resolve_recovery(control,database,batch,replacements):
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
    atomic_json(control.root/batch/'recovery-resolution.json',{'batch_id':batch,'coverage':coverage})
    control._set(batch,'resolved')
    return {'batch_id':batch,'state':'resolved','coverage':coverage}


def backup_before_import(database,folder):
    target=folder/'before-import.sqlite3'
    receipt=folder/'backup-complete.json'
    if target.exists():
        if not receipt.exists():
            raise RuntimeError('Incomplete backup exists; inspect before importing')
        return
    # Exclusive creation avoids overwriting a previous artifact.
    with target.open('xb'):
        pass
    source=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
    backup=sqlite3.connect(target,timeout=15)
    deadline=time.monotonic()+30
    def progress(status,remaining,total):
        if time.monotonic()>deadline:
            raise TimeoutError('Database backup exceeded 30 seconds')
    try:
        source.backup(backup,pages=256,progress=progress,sleep=0.1)
        if backup.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise RuntimeError('Database backup integrity check failed')
    finally:
        backup.close()
        source.close()
    atomic_json(receipt,{'completed':time.time(),'bytes':target.stat().st_size})


def load_inbox(source):
    sys.path.insert(0,str(Path(source).resolve()))
    import core
    import worker
    import card_translation
    from initialize_secrets import read_env
    cfg=core.settings()
    if cfg['enabled'] or cfg.get('translation_enabled',True):
        raise RuntimeError('Disable the existing paid workers before running the Kaggle bridge')
    # Only this dedicated CLI process changes its environment. Never source ai.env.
    os.environ.pop('ARK_API_KEY',None)
    values=read_env('ai.env')
    os.environ['MINIFLUX_API_KEY']=values['MINIFLUX_API_KEY']
    if values.get('AI_NEWS_OUTBOUND_PROXY'):
        os.environ['AI_NEWS_OUTBOUND_PROXY']=values['AI_NEWS_OUTBOUND_PROXY']
    return core,worker,card_translation


async def prepare_sample(source, limit, excluded_entry_ids=(), allowed_entry_ids=None, analysis_only=False):
    import httpx
    core,worker,cards=load_inbox(source)
    from content_input import content_text,is_our_social_feed
    from product_source import is_product_entry
    from prepared_content import apply as apply_prepared
    cfg=core.settings()
    # model_payload's slice now preserves the complete input, without saving settings.
    extraction_cfg={**cfg,'max_chars':sys.maxsize}
    excluded=sorted(set(int(value) for value in excluded_entry_ids))
    exclusion=(' AND a.entry_id NOT IN ('+','.join('?' for _ in excluded)+')') if excluded else ''
    allowed=sorted(set(int(value) for value in allowed_entry_ids)) if allowed_entry_ids is not None else None
    inclusion=(' AND a.entry_id IN ('+','.join('?' for _ in allowed)+')') if allowed else (' AND 0' if allowed is not None else '')
    if analysis_only:
        inclusion+=" AND a.state!='done'"
    with core.connect() as db:
        rows=[dict(row) for row in db.execute('''SELECT a.* FROM analyses a
            LEFT JOIN card_translations c ON c.entry_id=a.entry_id AND c.user_id=a.user_id
            WHERE ((a.state IN ('pending','waiting_model','budget_paused','fetch_error','ai_error')
                   AND a.next_try<=? AND a.attempts<3)
               OR (a.state='done' AND c.status IN ('pending','error','budget_paused','waiting_model')
                   AND c.next_try<=? AND c.attempts<3))'''+exclusion+inclusion+'''
            ORDER BY CASE WHEN c.status IN ('pending','error','budget_paused','waiting_model')
                          THEN 0 ELSE 1 END,a.published_at DESC,a.entry_id DESC LIMIT ?''',
            (time.time(),time.time(),*excluded,*(allowed or []),limit))]
    samples=[]
    skipped=[]
    async with httpx.AsyncClient(follow_redirects=False,trust_env=False,timeout=70) as client:
        for row in rows:
            analysis_needed=row['state']!='done'
            refreshed=row
            entry=apply_prepared(await worker.mf_get(client,f"/v1/entries/{row['entry_id']}"))
            if (entry['user_id']!=row['user_id'] or (analysis_needed and
                (entry['title']!=row['title'] or entry['url']!=row['url']))):
                skipped.append({'entry_id':row['entry_id'],'state':'upstream_identity_changed'})
                continue
            specialized=is_our_social_feed(entry.get('feed',{}).get('feed_url','')) or is_product_entry(entry)
            if analysis_needed and specialized:
                # Existing extraction returns before reserve_budget/post when no API key exists.
                await asyncio.wait_for(worker.process_one(client,row,extraction_cfg),timeout=180)
                with core.connect() as db:
                    refreshed=dict(db.execute('SELECT * FROM analyses WHERE entry_id=?',(row['entry_id'],)).fetchone())
                if refreshed['state']!='waiting_model' or refreshed['truncated']:
                    skipped.append({'entry_id':row['entry_id'],'state':refreshed['state']})
                    continue
                entry=apply_prepared(await worker.mf_get(client,f"/v1/entries/{row['entry_id']}"))
                if (entry['user_id']!=refreshed['user_id'] or entry['title']!=refreshed['title']
                    or entry['url']!=refreshed['url']
                    or content_text(entry.get('content') or '')[0]!=refreshed['source_text']):
                    skipped.append({'entry_id':row['entry_id'],'state':'upstream_changed_during_extract'})
                    continue
            if analysis_needed and not specialized:
                # Miniflux's nonempty result is not proof of a complete article.
                # Fetch the publisher body by its checked site rule before scoring.
                from fulltext_source import fetch,FulltextUnavailable
                try:
                    fulltext=await fetch(entry['url'])
                except FulltextUnavailable as exc:
                    skipped.append({'entry_id':row['entry_id'],'state':'requires_fulltext_adapter','reason':str(exc)})
                    with core.connect() as db:
                        db.execute("""UPDATE analyses SET state='requires_fulltext_adapter',error=?,updated_at=?
                            WHERE entry_id=? AND state=? AND content_hash IS ? AND source_text IS ?""",
                            (str(exc),time.time(),row['entry_id'],row['state'],row['content_hash'],row['source_text']))
                    continue
                current=apply_prepared(await worker.mf_get(client,f"/v1/entries/{row['entry_id']}"))
                if upstream_hash(current)!=upstream_hash(entry):
                    skipped.append({'entry_id':row['entry_id'],'state':'upstream_changed_during_fulltext'})
                    continue
                with core.connect() as db:
                    changed=db.execute("""UPDATE analyses SET source_text=?,source_chars=?,input_chars=?,
                        image_count=?,content_source='original_url_site_rule',truncated=0,extracted_at=?,updated_at=?,
                        content_hash=?,state='waiting_model',error=NULL
                        WHERE entry_id=? AND user_id=? AND state=? AND content_hash IS ? AND source_text IS ?""",
                        (fulltext['source_text'],len(fulltext['source_text']),len(fulltext['source_text']),
                         fulltext['image_count'],time.time(),time.time(),worker.hash_text(entry.get('content') or ''),
                         row['entry_id'],row['user_id'],row['state'],row['content_hash'],row['source_text'])).rowcount
                if changed!=1:
                    skipped.append({'entry_id':row['entry_id'],'state':'source_changed_during_fulltext'})
                    continue
                refreshed.update(source_text=fulltext['source_text'],source_chars=len(fulltext['source_text']),
                                 input_chars=len(fulltext['source_text']),truncated=False,
                                 content_hash=worker.hash_text(entry.get('content') or ''),state='waiting_model',
                                 content_source='original_url_site_rule',fulltext_receipt=fulltext['receipt'])
            cards.enqueue([entry])
            with core.connect() as db:
                card=db.execute('SELECT * FROM card_translations WHERE entry_id=?',(row['entry_id'],)).fetchone()
            refreshed['card']=dict(card) if card and card['status'] not in ('done','native') else None
            refreshed['skip_analysis']=not analysis_needed
            refreshed['upstream_hash']=upstream_hash(entry)
            samples.append(refreshed)
    return {'settings':{key:cfg[key] for key in ('prompt','max_output_tokens')},
            'translation_prompt':cards.PROMPT,'samples':samples,'skipped':skipped,'considered':len(rows)}


async def verify_upstream(source,manifest):
    import httpx
    core,worker,cards=load_inbox(source)
    from prepared_content import apply as apply_prepared
    refs={ref['entry_id']:ref for item in manifest['items'] for ref in item['source_refs']}
    unchanged=set()
    async with httpx.AsyncClient(follow_redirects=False,trust_env=False,timeout=70) as client:
        for entry_id,ref in refs.items():
            try:
                entry=apply_prepared(await worker.mf_get(client,f'/v1/entries/{entry_id}'))
            except httpx.HTTPError:
                continue
            if ref.get('upstream_hash') and upstream_hash(entry)==ref['upstream_hash']:
                unchanged.add(entry_id)
    return unchanged


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('action',choices=['prepare','advance','resolve'])
    parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--batch')
    parser.add_argument('--replacement',action='append',default=[])
    args=parser.parse_args()
    if not 1<=args.limit<=200:
        raise ValueError('A batch must contain 1..200 articles')
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    os.environ['KAGGLE_API_TOKEN']=config['token_file']
    sys.path.insert(0,str(Path(config['source']).resolve()))
    from initialize_secrets import read_env
    proxy=read_env('ai.env').get('AI_NEWS_OUTBOUND_PROXY')
    if proxy:
        for name in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'):
            os.environ[name]=proxy
    root=Path(config['state_root'])
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.umask(0o077)
    import fcntl
    coordination=Path(config.get('coordination_root',root)) if args.action=='prepare' else root
    coordination.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (coordination/'bridge.lock').open('a') as lock:
        # Both accounts claim articles under the same lock. The subprocess timeout
        # bounds waiting; a second worker must not fail merely because extraction is busy.
        fcntl.flock(lock,fcntl.LOCK_EX)
        control=Controller(root,config['owner'],kaggle_python=config['kaggle_python'])
        if args.action=='resolve':
            if not args.batch:
                raise ValueError('resolve requires --batch')
            print(json.dumps(resolve_recovery(control,config['database'],args.batch,args.replacement)))
            return
        if args.action=='prepare':
            with control.db() as db:
                outstanding=db.execute("SELECT id FROM batches WHERE state NOT IN ('imported','retired','resolved') LIMIT 1").fetchone()
            if outstanding:
                print(json.dumps({'existing_batch':outstanding['id']}))
                return
            versions=json.loads(Path(config['versions']).read_text(encoding='utf-8'))
            validate_model_config(config,versions)
            extraction_backup=root/('before-extraction-'+str(time.time_ns()))
            extraction_backup.mkdir(mode=0o700)
            backup_before_import(config['database'],extraction_backup)
            claimed=claimed_entries(config.get('peer_state_roots',[str(root)]))
            allowed=json.loads(Path(config['entry_allowlist']).read_text())['entry_ids'] if config.get('entry_allowlist') else None
            sample=asyncio.run(prepare_sample(config['source'],args.limit,claimed,allowed,config.get('analysis_only',False)))
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
            batch=control.prepare(manifest,Path(__file__).with_name('batch_runner.py').read_text(encoding='utf-8'))
            if batch:
                atomic_json(root/batch/'extraction-report.json',{'selected':len(sample['samples']),'skipped':sample['skipped']})
            print(json.dumps({'batch_id':batch,'selected':len(sample['samples']),'skipped':len(sample['skipped']),
                              'considered':sample['considered'],'skipped_fingerprint':digest(sample['skipped'])}))
            return
        if not args.batch:
            raise ValueError('advance requires --batch')
        row=control.row(args.batch)
        if row['state'] in {'imported','resolved'}:
            print(json.dumps(row))
            return
        if row['state']=='prepared':
            print(json.dumps(control.submit(args.batch)))
            return
        row=control.status(args.batch)
        if row['remote_status'] not in TERMINAL:
            print(json.dumps(row))
            return
        evidence=control.download(args.batch)
        manifest=control.manifest(args.batch)
        report=validate(manifest,evidence['results'],config['source'])
        folder=root/args.batch
        atomic_json(folder/'business-validation.json',report)
        unchanged=asyncio.run(verify_upstream(config['source'],manifest))
        backup_before_import(config['database'],folder)
        outcome=import_validated(config['database'],manifest,report,unchanged)
        atomic_json(folder/'import-report.json',outcome)
        unresolved=[item for item in outcome['items'] if item['state'] not in
                    ('imported','already_imported','existing_result_preserved')]
        if not unresolved and not evidence['missing_ids']:
            control._set(args.batch,'imported')
        elif config.get('drain_queue',False):
            deferred=defer_unresolved(config['database'],manifest,outcome)
            atomic_json(folder/'retry-resolution.json',{'batch_id':args.batch,'actions':deferred,
                                                       'disposition':'valid_imports_kept_failed_inputs_deferred'})
            control._set(args.batch,'resolved')
        print(json.dumps({'batch_id':args.batch,'valid':report['valid'],'invalid':report['invalid'],
                          'import_states':{state:sum(item['state']==state for item in outcome['items'])
                                           for state in {item['state'] for item in outcome['items']}},
                          'missing_ids':evidence['missing_ids']}))


if __name__=='__main__':
    main()
