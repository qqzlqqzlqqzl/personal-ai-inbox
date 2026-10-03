"""Explicit dot/Astra imports. No GPU, model API, settings, or budget changes."""
import argparse
import ast
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

from export_dot_articles_readonly import constants, digest, ro, ELIGIBLE, CARD_ELIGIBLE, STORED_SOURCES

from dot_import_coordination import exclusions

PRODUCER = {'provider': 'dot', 'model': 'gpt-6-astra', 'reasoning': 'xhigh'}
MODEL = 'dot/gpt-6-astra'
TYPES = {'新闻', '案例', '教程', '产品', '论文', '观点', '社交'}
SNAPSHOT_FIELDS = ('entry_id','user_id','feed_id','title','url','published_at','state','attempts',
                   'content_hash','source_text','source_chars','content_source','truncated','extracted_at')
CARD_FIELDS = ('entry_id','user_id','source_hash','original_title','excerpt','source_kind','status','attempts','next_try')


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def identity(packet, results):
    plain = {k: v for k, v in packet.items() if k != 'export_hash'}
    if packet.get('schema') != 'dot-article-export-v1' or packet.get('export_hash') != digest(plain):
        raise ValueError('export_integrity_failed')
    if packet.get('intended_executor') != PRODUCER or results.get('producer') != PRODUCER:
        raise ValueError('wrong_execution_provenance')
    if results.get('schema') != 'dot-article-results-v1' or results.get('export_hash') != packet['export_hash']:
        raise ValueError('results_not_bound_to_export')
    articles = packet.get('articles')
    if not isinstance(articles, list) or not 1 <= len(articles) <= 3:
        raise ValueError('first_batch_requires_one_to_three_articles')
    ids = [a['entry_id'] for a in articles]
    if any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('duplicate_or_invalid_export_id')
    for article in articles:
        if (article.get('state') not in ELIGIBLE or article.get('truncated')
                or article.get('content_source') not in STORED_SOURCES
                or not isinstance(article.get('source_text'),str) or not article['source_text'].strip()
                or article.get('source_chars') != len(article['source_text'])
                or not article.get('extracted_at') or not article.get('content_hash')):
            raise ValueError('export_source_not_ready')
        original = {k:v for k,v in article.items() if k not in {'snapshot_hash','card'}}
        if digest(original) != article.get('snapshot_hash') or sha(article['source_text']) != article.get('source_text_sha256'):
            raise ValueError('article_snapshot_integrity_failed')
        card = article.get('card')
        if card and digest({k:v for k,v in card.items() if k != 'snapshot_hash'}) != card.get('snapshot_hash'):
            raise ValueError('card_snapshot_integrity_failed')
    return 'dot-articles-' + packet['export_hash'][:24], digest(results)


def read_context(config, db):
    source = Path(config['source'])
    defaults = constants(source/'core.py', {'DEFAULT_PROMPT'})
    row = db.execute("SELECT value FROM settings WHERE name='preferences'").fetchone()
    cfg = json.loads(row[0]) if row else {}
    card = constants(source/'card_translation.py', {'PROMPT','VERSION'})
    fidelity = constants(source/'kaggle_batch/build_manifest.py', {'ANALYSIS_FIDELITY','TRANSLATION_FIDELITY'})
    return {'enabled':cfg.get('enabled',True),'translation_enabled':cfg.get('translation_enabled',True),
        'prompt':cfg.get('prompt',defaults['DEFAULT_PROMPT']), 'max_output_tokens':cfg.get('max_output_tokens',1500),
        'card_prompt':card['PROMPT'],'card_version':card['VERSION'], **fidelity}


def validate_results(config, packet, results):
    sys.path.insert(0, str(Path(config['source']).resolve()))
    from worker import validate_result
    from card_translation import validate_items
    from kaggle_batch.scoring_policy import add_priority
    returned = results.get('articles')
    if not isinstance(returned, list) or len(returned) != len(packet['articles']):
        raise ValueError('incomplete_result_batch')
    by_id = {}
    expected = {a['entry_id']:a for a in packet['articles']}
    for result in returned:
        eid = result.get('entry_id') if isinstance(result,dict) else None
        if type(eid) is not int or eid not in expected or eid in by_id:
            raise ValueError('unexpected_result_entry')
        original = expected[eid]
        if result.get('snapshot_hash') != original['snapshot_hash']:
            raise ValueError('result_input_version_mismatch')
        data = result.get('analysis')
        required = {'score','technical_score','business_score','summary','reason','evidence','content_type','worth_reading','tags'}
        if not isinstance(data,dict) or set(data) != required:
            raise ValueError('analysis_schema_mismatch')
        for field in ('score','technical_score','business_score'):
            value=data[field]
            if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=10:
                raise ValueError('invalid_score')
        for field,low,high in (('summary',1,120),('reason',1,100),('evidence',8,120)):
            value=data[field]
            if not isinstance(value,str) or value != value.strip() or not low<=len(value)<=high:
                raise ValueError('invalid_'+field)
        if data['evidence'] not in original['source_text']:
            raise ValueError('evidence_not_exact_contiguous_original')
        if data['content_type'] not in TYPES or type(data['worth_reading']) is not bool:
            raise ValueError('invalid_type_or_boolean')
        if not isinstance(data['tags'],list) or len(data['tags'])>6 or any(not isinstance(t,str) or not t.strip() or len(t)>40 for t in data['tags']):
            raise ValueError('invalid_tags')
        checked = add_priority(validate_result(data), packet['analysis_prompt'])
        if 'reading-priority-v2' in packet['analysis_prompt']:
            if data['worth_reading'] != checked['worth_reading'] or not data['reason'].startswith(checked['reading_priority']):
                raise ValueError('reading_priority_mismatch')
        card_output = result.get('card')
        if original.get('card') is None:
            if card_output is not None:
                raise ValueError('unrequested_card_result')
            translated = None
        else:
            if not isinstance(card_output,dict) or set(card_output)!={'title','summary'}:
                raise ValueError('card_schema_mismatch')
            for field,limit in (('title',180),('summary',100)):
                value=card_output[field]
                if not isinstance(value,str) or not value.strip() or value!=value.strip() or len(value)>limit:
                    raise ValueError('invalid_card_'+field)
            raw = json.dumps({'items':[{'id':eid,**card_output}]},ensure_ascii=False)
            translated = validate_items(raw,[original['card']])[eid]
            if tuple(translated) != (card_output['title'],card_output['summary']):
                raise ValueError('card_requires_silent_normalization')
        by_id[eid] = {'analysis':checked,'card':translated}
    return by_id


def existing_receipt(db, batch_id, export_hash, result_hash):
    table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dot_imports'").fetchone()
    row=db.execute('SELECT * FROM dot_imports WHERE batch_id=?',(batch_id,)).fetchone() if table else None
    if row:
        if row['export_hash']!=export_hash or row['result_hash']!=result_hash or row['model']!=MODEL or row['reasoning']!='xhigh':
            raise ValueError('import_identity_collision')
        return {'state':'already_imported','batch_id':batch_id,'analysis_count':row['analysis_count'],'card_count':row['card_count'],'model':MODEL}
    return None


def preflight(db, config, packet, upstream):
    ctx=read_context(config,db)
    if ctx['enabled'] is not False or ctx['translation_enabled'] is not False:
        raise ValueError('existing_paid_workers_enabled')
    comparisons={'analysis_prompt':'prompt','max_output_tokens':'max_output_tokens','translation_prompt':'card_prompt',
        'translation_prompt_version':'card_version','analysis_fidelity':'ANALYSIS_FIDELITY','translation_fidelity':'TRANSLATION_FIDELITY'}
    if any(packet[k]!=ctx[v] for k,v in comparisons.items()):
        raise ValueError('prompt_or_policy_changed')
    claimed,leased=exclusions(config)
    rows=[]
    for article in packet['articles']:
        eid=article['entry_id']
        if eid in claimed or eid in leased:
            raise ValueError('entry_claimed_or_leased_after_export')
        row=db.execute('SELECT * FROM analyses WHERE entry_id=?',(eid,)).fetchone()
        if row is None or row['state']=='done':
            raise ValueError('existing_result_preserved_or_missing')
        if row['user_id'] != int(config.get('scope_user_id',1)) or (row['attempts'] or 0)>=3 or (row['next_try'] or 0)>time.time():
            raise ValueError('analysis_no_longer_eligible')
        if any(row[key] != article.get(key) for key in SNAPSHOT_FIELDS):
            raise ValueError('analysis_source_snapshot_changed')
        if sha(row['source_text']) != article['source_text_sha256']:
            raise ValueError('analysis_body_changed')
        current=upstream.get(eid)
        required=('entry_id','user_id','feed_id','title','url','content_hash')
        if not current or any(current.get(key)!=article.get(key) for key in required) or current.get('feed_enabled') is not True:
            raise ValueError('upstream_changed_or_unavailable')
        card=None
        if article.get('card'):
            card=db.execute('SELECT * FROM card_translations WHERE entry_id=?',(eid,)).fetchone()
            if card is None or card['status'] in {'done','native'} or any(card[key]!=article['card'].get(key) for key in CARD_FIELDS):
                raise ValueError('card_source_snapshot_changed')
            if card['status'] not in CARD_ELIGIBLE or (card['attempts'] or 0)>=3 or (card['next_try'] or 0)>time.time():
                raise ValueError('card_no_longer_eligible')
            live_card=current.get('card_source')
            if not isinstance(live_card,dict) or any(live_card.get(key)!=article['card'][key] for key in ('source_hash','original_title','excerpt','source_kind')):
                raise ValueError('live_card_source_changed')
            prior_version=db.execute('SELECT 1 FROM card_translation_versions WHERE entry_id=? AND user_id=? AND source_hash=?',
                (eid,card['user_id'],card['source_hash'])).fetchone()
            if prior_version:
                raise ValueError('existing_translation_version_preserved')
        rows.append((dict(row),dict(card) if card else None))
    return rows


def backup_database(config, packet, results, batch_id):
    # A verified online snapshot; never restore the whole database automatically.
    folder=Path(config['database']).parent/'dot-article-imports'/batch_id
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=folder/'before-import.sqlite3'
    if target.exists():
        try:
            receipt=json.loads((folder/'backup-receipt.json').read_text())
            prior_packet=json.loads((folder/'export.json').read_text())
            prior_results=json.loads((folder/'results.json').read_text())
            checksum=hashlib.sha256(target.read_bytes()).hexdigest()
            if (receipt['sha256']!=checksum or prior_packet!=packet or prior_results!=results):
                raise ValueError('backup_identity_mismatch')
            with ro(target) as check:
                if check.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('backup_integrity_failed')
            return checksum
        except (OSError,ValueError,KeyError):
            raise ValueError('unreceipted_backup_requires_review') from None
    partial=folder/('backup-'+str(os.getpid())+'.partial')
    source=ro(config['database']); dest=sqlite3.connect(partial); deadline=time.monotonic()+60
    def progress(*_):
        if time.monotonic()>deadline:raise TimeoutError('backup_deadline')
    try:
        source.backup(dest,pages=256,progress=progress,sleep=.01)
        if dest.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('backup_integrity_failed')
    finally:
        dest.close();source.close()
    os.chmod(partial,0o600);os.replace(partial,target)
    checksum=hashlib.sha256(target.read_bytes()).hexdigest()
    for name,value in [('export.json',packet),('results.json',results),('backup-receipt.json',{'sha256':checksum,'bytes':target.stat().st_size,'at':time.time()})]:
        path=folder/name
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    return checksum


def import_batch(config, packet, results, upstream_reader, *, apply=False, backup=backup_database):
    batch_id,result_hash=identity(packet,results)
    checked=validate_results(config,packet,results)
    if not callable(upstream_reader):raise ValueError('fresh_upstream_reader_required')
    # Existing global preparation lock prevents new Kaggle claims while checking
    # every lane and committing. Existing observers do not get cancelled.
    with (Path(config['coordination_root'])/'bridge.lock').open('rb') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with ro(config['database']) as db:
            prior=existing_receipt(db,batch_id,packet['export_hash'],result_hash)
            if prior:return prior
            preflight(db,config,packet,upstream_reader())
        if not apply:
            return {'state':'validated_no_write','batch_id':batch_id,'analysis_count':len(checked),
                    'card_count':sum(v['card'] is not None for v in checked.values()),'model':MODEL}
        backup_hash=backup(config,packet,results,batch_id)
        # Re-fetch after the potentially long backup while retaining the global
        # coordination lock. Never commit against a pre-backup source snapshot.
        fresh_upstream=upstream_reader()
        db=sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=rw',uri=True,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                prior=existing_receipt(db,batch_id,packet['export_hash'],result_hash)
                if prior:return prior
                before=preflight(db,config,packet,fresh_upstream)
                now=time.time()
                prompt_hash=sha(json.dumps({'provider':'dot','model':'gpt-6-astra','reasoning':'xhigh',
                    'prompt_sha256':sha(packet['analysis_prompt']),'fidelity_sha256':sha(packet['analysis_fidelity'])},sort_keys=True))
                card_count=0
                for article in packet['articles']:
                    eid=article['entry_id']; value=checked[eid]; data=value['analysis']
                    changed=db.execute("""UPDATE analyses SET state='done',result=?,score=?,technical_score=?,business_score=?,
                        model=?,prompt_hash=?,tokens=NULL,analyzed_at=?,updated_at=?,attempts=0,next_try=0,error=NULL
                        WHERE entry_id=? AND user_id=? AND state=? AND content_hash IS ? AND source_text IS ?""",
                        (json.dumps(data,ensure_ascii=False),data['score'],data['technical_score'],data['business_score'],MODEL,prompt_hash,
                         now,now,eid,article['user_id'],article['state'],article['content_hash'],article['source_text'])).rowcount
                    if changed!=1:raise ValueError('analysis_compare_and_set_failed')
                    if value['card'] is not None:
                        card=article['card']; title,summary=value['card']
                        changed=db.execute("""UPDATE card_translations SET title_zh=?,summary_zh=?,status='done',model=?,
                            attempts=0,next_try=0,error=NULL,updated_at=?,translated_at=?
                            WHERE entry_id=? AND user_id=? AND status=? AND source_hash=?""",
                            (title,summary,MODEL,now,now,eid,card['user_id'],card['status'],card['source_hash'])).rowcount
                        if changed!=1:raise ValueError('card_compare_and_set_failed')
                        db.execute('''INSERT INTO card_translation_versions
                            (entry_id,user_id,source_hash,original_title,title_zh,summary_zh,source_kind,model,translated_at)
                            VALUES (?,?,?,?,?,?,?,?,?)''',(eid,card['user_id'],card['source_hash'],card['original_title'],title,summary,card['source_kind'],MODEL,now))
                        card_count+=1
                db.execute('''CREATE TABLE IF NOT EXISTS dot_imports(batch_id TEXT PRIMARY KEY,export_hash TEXT NOT NULL,
                    result_hash TEXT NOT NULL,model TEXT NOT NULL,reasoning TEXT NOT NULL,imported_at REAL NOT NULL,
                    analysis_count INTEGER NOT NULL,card_count INTEGER NOT NULL,entry_ids TEXT NOT NULL,
                    backup_sha256 TEXT NOT NULL,before_rows TEXT NOT NULL)''')
                db.execute('INSERT INTO dot_imports VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (batch_id,packet['export_hash'],result_hash,MODEL,'xhigh',now,len(checked),card_count,
                     json.dumps([a['entry_id'] for a in packet['articles']]),backup_hash,json.dumps(before,ensure_ascii=False)))
        finally:db.close()
    return {'state':'imported','batch_id':batch_id,'analysis_count':len(checked),'card_count':card_count,'model':MODEL,'analyzed_at':now}


def read_upstream(config, packet):
    sys.path.insert(0,config['source'])
    import httpx
    from initialize_secrets import read_env
    from worker import MF
    from prepared_content import apply as apply_prepared
    from card_translation import source_card
    from kaggle_batch.live_scope import enabled_feeds
    uid=int(config.get('scope_user_id',1))
    enabled={f['id'] for f in enabled_feeds(config) if type(f.get('id')) is int and f.get('user_id')==uid and not f.get('disabled',False)}
    token=read_env('ai.env')['MINIFLUX_API_KEY']
    with ro(config['database']) as db:
        stored=db.execute("SELECT value FROM settings WHERE name='preferences'").fetchone()
        cfg=json.loads(stored[0]) if stored else {}
    configured_model=cfg.get('model')
    if configured_model is None:
        for node in ast.parse((Path(config['source'])/'core.py').read_text()).body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='DEFAULT_SETTINGS' for t in node.targets) and isinstance(node.value,ast.Dict):
                for key,value in zip(node.value.keys,node.value.values):
                    if isinstance(key,ast.Constant) and key.value=='model':configured_model=ast.literal_eval(value)
    if not isinstance(configured_model,str) or not configured_model:raise ValueError('current_card_model_setting_unavailable')
    current={}
    with httpx.Client(timeout=30,trust_env=False,follow_redirects=False) as client:
        for original in packet['articles']:
            response=client.get(MF+'/v1/entries/'+str(original['entry_id']),headers={'X-Auth-Token':token})
            response.raise_for_status(); entry=apply_prepared(response.json())
            feed=entry.get('feed') or {}; feed_id=feed.get('id',entry.get('feed_id'))
            current[original['entry_id']]={'entry_id':entry.get('id'),'user_id':entry.get('user_id'),'feed_id':feed_id,
                'title':entry.get('title'),'url':entry.get('url'),'content_hash':sha(entry.get('content') or ''),
                'feed_enabled':feed_id in enabled}
            if original.get('card'):
                title,excerpt,kind,fingerprint=source_card(entry,configured_model)
                current[original['entry_id']]['card_source']={'original_title':title,'excerpt':excerpt,'source_kind':kind,'source_hash':fingerprint}
    return current


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True);parser.add_argument('--export',required=True)
    parser.add_argument('--results',required=True);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        config=json.loads(Path(args.config).read_text());packet=json.loads(Path(args.export).read_text());results=json.loads(Path(args.results).read_text())
        batch_id,result_hash=identity(packet,results)
        with ro(config['database']) as db:prior=existing_receipt(db,batch_id,packet['export_hash'],result_hash)
        outcome=prior or import_batch(config,packet,results,lambda:read_upstream(config,packet),apply=args.apply)
        print(json.dumps(outcome,ensure_ascii=False))
    except Exception as exc:
        # Only our fixed ValueError codes may leave the process.
        code=str(exc) if type(exc) is ValueError and re_code(str(exc)) else type(exc).__name__
        print(json.dumps({'state':'blocked','error':code}));raise SystemExit(1)


def re_code(value):
    import re
    return re.fullmatch(r'[a-z][a-z0-9_]{0,100}',value) is not None


if __name__=='__main__':main()
