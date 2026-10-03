"""Bounded URL-first dot/Astra imports with reviewed references, not archived bodies.

Reference receipts attest actual full reading and a checked short quote. They are
not proof that this process fetched, cached, or hashed publisher full text.
"""
import argparse
import datetime as dt
import fcntl
import ipaddress
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import dot_article_import as legacy
from export_dot_articles_readonly import digest, ro, ELIGIBLE, CARD_ELIGIBLE

from dot_import_coordination import exclusions

ANALYSIS_FIELDS = ('entry_id','user_id','feed_id','title','url','published_at','state','attempts','next_try','updated_at',
                   'content_hash','content_source','source_chars','truncated','extracted_at','analyzed_at')
CARD_FIELDS = (*legacy.CARD_FIELDS, 'updated_at')
IDENTITY_FIELDS = ('entry_id','user_id','feed_id','title','url')
PRODUCER = legacy.PRODUCER
MODEL = legacy.MODEL


def public_url(value):
    if not isinstance(value,str) or len(value)>4096 or any(c.isspace() or ord(c)<32 for c in value):
        raise ValueError('invalid_source_url')
    try:
        p=urlsplit(value)
        if p.scheme not in {'http','https'} or not p.hostname or p.username or p.password or p.port not in (None,80,443):
            raise ValueError('invalid_source_url')
        host=p.hostname.lower()
        if host=='localhost' or '.' not in host or host.endswith(('.local','.internal','.localhost')):
            raise ValueError('invalid_source_url')
        try:
            if not ipaddress.ip_address(host).is_global:raise ValueError('invalid_source_url')
        except ValueError as exc:
            if str(exc)=='invalid_source_url':raise
        if re.search(r'(token|secret|password|api[_-]?key|signature|credential)=',p.query,re.I):
            raise ValueError('sensitive_source_url')
    except (TypeError,ValueError):
        raise ValueError('invalid_source_url') from None
    return value


def finite_number(value):
    return type(value) in (int,float) and math.isfinite(value)


def reference_snapshot_eligible(snap, cutoff):
    """Shared URL-only eligibility; preserve NULLs and retry boundary semantics."""
    return (snap['state'] in ELIGIBLE and type(snap['attempts']) is int and 0<=snap['attempts']<3
            and finite_number(snap['next_try']) and snap['next_try']<=cutoff
            and finite_number(snap['updated_at']) and snap['updated_at']<=cutoff
            and all(snap[k] is None for k in ('content_hash','content_source','source_chars','extracted_at','analyzed_at'))
            and snap['truncated']==0)


def identity(packet,results,batch_limit=3):
    if type(batch_limit) is not int or not 1<=batch_limit<=12:raise ValueError('invalid_batch_limit')
    if packet.get('schema')!='dot-url-manifest-v1' or packet.get('manifest_hash')!=digest({k:v for k,v in packet.items() if k!='manifest_hash'}):
        raise ValueError('manifest_integrity_failed')
    if packet.get('intended_executor')!=PRODUCER or results.get('producer')!=PRODUCER:
        raise ValueError('wrong_execution_provenance')
    if set(results)-{'schema','manifest_hash','producer','articles','skipped'}:raise ValueError('unknown_result_packet_fields')
    if results.get('schema')!='dot-url-results-v1' or results.get('manifest_hash')!=packet['manifest_hash']:
        raise ValueError('results_not_bound_to_manifest')
    articles=packet.get('articles'); returned=results.get('articles')
    if not isinstance(articles,list) or not 1<=len(articles)<=12 or not isinstance(returned,list) or not 1<=len(returned)<=batch_limit:
        raise ValueError('invalid_bounded_batch')
    cutoff=packet.get('cutoff')
    if not finite_number(cutoff) or not 0<cutoff<=time.time() or not finite_number(packet.get('exported_at')) or not cutoff<=packet['exported_at']<=time.time():
        raise ValueError('invalid_frozen_cutoff')
    uid=packet.get('scope_user_id')
    if type(uid) is not int or uid<=0:raise ValueError('invalid_scope_user')
    by_id={}
    for a in articles:
        eid=a.get('entry_id'); snap=a.get('analysis_snapshot'); card=a.get('card')
        if type(eid) is not int or eid<=0 or eid in by_id or a.get('user_id')!=uid or a.get('feed_id') not in packet.get('enabled_feed_ids',[]):
            raise ValueError('invalid_manifest_identity')
        if a.get('snapshot_hash')!=digest({k:v for k,v in a.items() if k!='snapshot_hash'}):raise ValueError('article_snapshot_integrity_failed')
        if not isinstance(snap,dict) or set(snap)!=set(ANALYSIS_FIELDS) or digest(snap)!=a.get('analysis_snapshot_hash'):
            raise ValueError('analysis_snapshot_integrity_failed')
        if any(snap[k]!=a[k] for k in (*IDENTITY_FIELDS,'published_at')):raise ValueError('manifest_identity_conflict')
        if not reference_snapshot_eligible(snap,cutoff):
            raise ValueError('reference_snapshot_not_eligible')
        if card is not None:
            if not isinstance(card,dict) or set(card)!=set(CARD_FIELDS) or digest(card)!=a.get('card_snapshot_hash'):
                raise ValueError('card_snapshot_integrity_failed')
            if card['entry_id']!=eid or card['user_id']!=uid or card['status'] not in CARD_ELIGIBLE:
                raise ValueError('card_not_eligible')
        elif a.get('card_snapshot_hash')!=digest(None):raise ValueError('card_snapshot_integrity_failed')
        public_url(a['url']);by_id[eid]=a
    if packet.get('entry_ids')!=[a['entry_id'] for a in articles]:raise ValueError('frozen_ids_mismatch')
    selected=[]
    for r in returned:
        eid=r.get('entry_id') if isinstance(r,dict) else None
        if type(eid) is not int or eid not in by_id or eid in selected:raise ValueError('unexpected_result_entry')
        if set(r)!={'entry_id','requested_url','manifest_snapshot_hash','status','source','analysis','card','producer','review'}:raise ValueError('unknown_result_fields')
        if r.get('status')!='ok' or r.get('producer')!=PRODUCER:raise ValueError('result_not_successful_dot_read')
        a=by_id[eid]
        if r.get('manifest_snapshot_hash')!=a['snapshot_hash'] or r.get('requested_url')!=a['url']:
            raise ValueError('result_input_version_mismatch')
        selected.append(eid)
    skipped=results.get('skipped',[])
    if not isinstance(skipped,list):raise ValueError('invalid_skipped_results')
    seen=set(selected)
    for item in skipped:
        eid=item.get('entry_id') if isinstance(item,dict) else None
        if (type(eid) is not int or eid not in by_id or eid in seen or set(item)!={'entry_id','status','reason'}
                or item['status']!='source_unavailable' or not isinstance(item['reason'],str) or not 1<=len(item['reason'])<=500):
            raise ValueError('invalid_skipped_results')
        seen.add(eid)
    batch_id='dot-url-'+digest({'manifest_hash':packet['manifest_hash'],'entry_ids':sorted(selected)})[:24]
    return batch_id,digest(results),[by_id[eid] for eid in sorted(selected)]


def validate_reference(article,result,cutoff):
    source=result.get('source'); review=result.get('review')
    if not isinstance(source,dict) or source.get('method')!='dot_url_reference' or source.get('archive_mode')!='reference_only':
        raise ValueError('reference_source_required')
    if set(source)-{'method','archive_mode','full_article_read','text_file','text_sha256','text','original_url','final_url','page_title','fetched_at_utc','completeness','archive_limit'}:
        raise ValueError('unknown_source_fields')
    if 'archive_limit' in source and (not isinstance(source['archive_limit'],str) or len(source['archive_limit'])>1000):raise ValueError('invalid_archive_note')
    if source.get('full_article_read') is not True or source.get('text_file') is not None or source.get('text_sha256') is not None or source.get('text') is not None:
        raise ValueError('reference_must_not_claim_body_archive')
    if source.get('original_url')!=article['url']:raise ValueError('source_original_url_mismatch')
    public_url(source.get('final_url'))
    if not isinstance(source.get('page_title'),str) or not 1<=len(source['page_title'])<=500:raise ValueError('invalid_source_title')
    try:
        stamp=dt.datetime.fromisoformat(source['fetched_at_utc'].replace('Z','+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset()!=dt.timedelta(0) or not cutoff<=stamp.timestamp()<=time.time():raise ValueError()
    except (KeyError,TypeError,ValueError,AttributeError):raise ValueError('invalid_read_timestamp') from None
    complete=source.get('completeness')
    if not isinstance(complete,dict) or set(complete)!={'full_article','truncated','paywall_or_challenge','evidence'} or complete.get('full_article') is not True or complete.get('truncated') is not False or complete.get('paywall_or_challenge') is not False or not isinstance(complete.get('evidence'),str) or not 10<=len(complete['evidence'])<=2000:
        raise ValueError('full_read_observation_required')
    if not isinstance(review,dict) or set(review)!={'reviewer','full_article_read_verified','evidence_quote_verified','source_observation','article_result_hash'} or review.get('reviewer')!='dot/root' or review.get('full_article_read_verified') is not True or review.get('evidence_quote_verified') is not True or not isinstance(review.get('source_observation'),str) or not 10<=len(review['source_observation'])<=2000:
        raise ValueError('root_read_and_quote_review_required')
    if review.get('article_result_hash')!=digest({k:v for k,v in result.items() if k!='review'}):
        raise ValueError('review_not_bound_to_result')
    return {'source':source,'review':review,'snapshot_hash':article['snapshot_hash']}


def validate_results(config,packet,results,selected):
    sys.path.insert(0,str(Path(config['source']).resolve()))
    from worker import validate_result
    from card_translation import validate_items
    from kaggle_batch.scoring_policy import add_priority
    by_id={r['entry_id']:r for r in results['articles']}; checked={}
    for a in selected:
        eid=a['entry_id'];r=by_id[eid]; receipt=validate_reference(a,r,packet['cutoff']); data=r.get('analysis')
        if not isinstance(data,dict) or set(data)!={'score','technical_score','business_score','summary','reason','evidence','content_type','worth_reading','tags'}:raise ValueError('analysis_schema_mismatch')
        for key in ('score','technical_score','business_score'):
            if not finite_number(data[key]) or not 0<=data[key]<=10:raise ValueError('invalid_score')
        for key,low,high in (('summary',1,120),('reason',1,100),('evidence',8,120)):
            v=data[key]
            if not isinstance(v,str) or v!=v.strip() or not low<=len(v)<=high:raise ValueError('invalid_'+key)
        # Root separately verifies exactness and single-source quote budget. This
        # bound is additional; it does not invent archived full text to compare.
        if len(re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*",data['evidence']))>25:raise ValueError('quote_word_limit_exceeded')
        if data['content_type'] not in legacy.TYPES or type(data['worth_reading']) is not bool:raise ValueError('invalid_type_or_boolean')
        if not isinstance(data['tags'],list) or len(data['tags'])>6 or any(not isinstance(t,str) or not t.strip() or len(t)>40 for t in data['tags']):raise ValueError('invalid_tags')
        value=add_priority(validate_result(data),packet['analysis_prompt'])
        if 'reading-priority-v2' in packet['analysis_prompt'] and (data['worth_reading']!=value['worth_reading'] or not data['reason'].startswith(value['reading_priority'])):raise ValueError('reading_priority_mismatch')
        card=r.get('card'); translated=None
        if a['card'] is None:
            if card is not None:raise ValueError('unrequested_card_result')
        else:
            if not isinstance(card,dict) or set(card) not in ({'title','summary'},{'id','title','summary'}):raise ValueError('card_schema_mismatch')
            if 'id' in card and (type(card['id']) is not int or card['id']!=eid):raise ValueError('card_id_mismatch')
            for key,limit in (('title',180),('summary',100)):
                v=card[key]
                if not isinstance(v,str) or not v.strip() or v!=v.strip() or len(v)>limit:raise ValueError('invalid_card_'+key)
            translated=validate_items(json.dumps({'items':[{'id':eid,'title':card['title'],'summary':card['summary']}]},ensure_ascii=False),[a['card']])[eid]
            if tuple(translated)!=(card['title'],card['summary']):raise ValueError('card_requires_silent_normalization')
        checked[eid]={'analysis':value,'card':translated,'receipt':receipt}
    return checked


def preflight(db,config,packet,selected,upstream):
    ctx=legacy.read_context(config,db)
    if ctx['enabled'] is not False or ctx['translation_enabled'] is not False:raise ValueError('existing_paid_workers_enabled')
    mapping={'analysis_prompt':'prompt','max_output_tokens':'max_output_tokens','translation_prompt':'card_prompt','translation_prompt_version':'card_version','analysis_fidelity':'ANALYSIS_FIDELITY','translation_fidelity':'TRANSLATION_FIDELITY'}
    if any(packet[k]!=ctx[v] for k,v in mapping.items()):raise ValueError('prompt_or_policy_changed')
    if packet['analysis_prompt_sha256']!=legacy.sha(ctx['prompt']) or packet['translation_prompt_sha256']!=legacy.sha(ctx['card_prompt']):raise ValueError('prompt_hash_mismatch')
    claimed,leased=exclusions(config);before=[]
    for a in selected:
        eid=a['entry_id'];snap=a['analysis_snapshot']
        if eid in claimed or eid in leased:raise ValueError('entry_claimed_or_leased_after_export')
        row=db.execute('SELECT * FROM analyses WHERE entry_id=?',(eid,)).fetchone()
        if row is None or row['state']=='done':raise ValueError('existing_result_preserved_or_missing')
        if row['user_id']!=int(config.get('scope_user_id',1)) or any(row[k]!=snap[k] for k in ANALYSIS_FIELDS) or row['source_text'] is not None:
            raise ValueError('analysis_source_snapshot_changed')
        live=upstream.get(eid)
        if not live or any(live.get(k)!=a[k] for k in IDENTITY_FIELDS) or live.get('feed_enabled') is not True or not isinstance(live.get('content_hash'),str) or len(live['content_hash'])!=64:
            raise ValueError('upstream_changed_or_unavailable')
        card=None
        if a['card'] is not None:
            card=db.execute('SELECT * FROM card_translations WHERE entry_id=?',(eid,)).fetchone()
            if card is None or any(card[k]!=a['card'][k] for k in CARD_FIELDS):raise ValueError('card_source_snapshot_changed')
            if (card['attempts'] or 0)>=3 or (card['next_try'] or 0)>time.time():raise ValueError('card_no_longer_eligible')
            live_card=live.get('card_source')
            if not isinstance(live_card,dict) or any(live_card.get(k)!=card[k] for k in ('source_hash','original_title','excerpt','source_kind')):raise ValueError('live_card_source_changed')
            if db.execute('SELECT 1 FROM card_translation_versions WHERE entry_id=? AND user_id=? AND source_hash=?',(eid,card['user_id'],card['source_hash'])).fetchone():raise ValueError('existing_translation_version_preserved')
        before.append((dict(row),dict(card) if card else None))
    return before


def import_batch(config,packet,results,upstream_reader,*,apply=False,batch_limit=3,backup=legacy.backup_database):
    batch_id,result_hash,selected=identity(packet,results,batch_limit)
    checked=validate_results(config,packet,results,selected)
    if not callable(upstream_reader):raise ValueError('fresh_upstream_reader_required')
    with (Path(config['coordination_root'])/'bridge.lock').open('rb') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with ro(config['database']) as db:
            prior=legacy.existing_receipt(db,batch_id,packet['manifest_hash'],result_hash)
            if prior:return prior
            preflight(db,config,packet,selected,upstream_reader())
        if not apply:return {'state':'validated_no_write','batch_id':batch_id,'analysis_count':len(checked),'card_count':sum(v['card'] is not None for v in checked.values()),'model':MODEL,'source_mode':'reference_only'}
        backup_hash=backup(config,packet,results,batch_id)
        fresh=upstream_reader()
        db=sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=rw',uri=True,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                prior=legacy.existing_receipt(db,batch_id,packet['manifest_hash'],result_hash)
                if prior:return prior
                before=preflight(db,config,packet,selected,fresh);now=time.time();card_count=0
                db.execute('''CREATE TABLE IF NOT EXISTS dot_url_sources(entry_id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL,
                    source_mode TEXT NOT NULL,source_receipt TEXT NOT NULL,upstream_content_hash TEXT NOT NULL,recorded_at REAL NOT NULL)''')
                db.execute('''CREATE TABLE IF NOT EXISTS dot_url_batch_receipts(batch_id TEXT PRIMARY KEY,manifest_hash TEXT NOT NULL,skipped TEXT NOT NULL)''')
                db.execute('INSERT INTO dot_url_batch_receipts VALUES (?,?,?)',(batch_id,packet['manifest_hash'],json.dumps(results.get('skipped',[]),ensure_ascii=False)))
                prompt_hash=legacy.sha(json.dumps({'provider':'dot','model':'gpt-6-astra','reasoning':'xhigh','source_mode':'reference_only',
                    'prompt_sha256':legacy.sha(packet['analysis_prompt']),'fidelity_sha256':legacy.sha(packet['analysis_fidelity'])},sort_keys=True))
                for a in selected:
                    eid=a['entry_id'];v=checked[eid];data=v['analysis'];snap=a['analysis_snapshot']
                    where=' AND '.join(k+' IS ?' for k in ANALYSIS_FIELDS)
                    changed=db.execute("UPDATE analyses SET state='done',result=?,score=?,technical_score=?,business_score=?,model=?,prompt_hash=?,tokens=NULL,analyzed_at=?,updated_at=?,attempts=0,next_try=0,error=NULL,content_hash=?,content_source='dot_url_reference' WHERE "+where+' AND source_text IS NULL',
                        (json.dumps(data,ensure_ascii=False),data['score'],data['technical_score'],data['business_score'],MODEL,prompt_hash,now,now,fresh[eid]['content_hash'],*(snap[k] for k in ANALYSIS_FIELDS))).rowcount
                    if changed!=1:raise ValueError('analysis_compare_and_set_failed')
                    db.execute('INSERT INTO dot_url_sources VALUES (?,?,?,?,?,?)',(eid,batch_id,'reference_only',json.dumps(v['receipt'],ensure_ascii=False),fresh[eid]['content_hash'],now))
                    if v['card'] is not None:
                        card=a['card'];title,summary=v['card'];where=' AND '.join(k+' IS ?' for k in CARD_FIELDS)
                        changed=db.execute("UPDATE card_translations SET title_zh=?,summary_zh=?,status='done',model=?,attempts=0,next_try=0,error=NULL,updated_at=?,translated_at=? WHERE "+where,
                            (title,summary,MODEL,now,now,*(card[k] for k in CARD_FIELDS))).rowcount
                        if changed!=1:raise ValueError('card_compare_and_set_failed')
                        db.execute('INSERT INTO card_translation_versions(entry_id,user_id,source_hash,original_title,title_zh,summary_zh,source_kind,model,translated_at) VALUES (?,?,?,?,?,?,?,?,?)',(eid,card['user_id'],card['source_hash'],card['original_title'],title,summary,card['source_kind'],MODEL,now));card_count+=1
                db.execute('''CREATE TABLE IF NOT EXISTS dot_imports(batch_id TEXT PRIMARY KEY,export_hash TEXT NOT NULL,result_hash TEXT NOT NULL,
                    model TEXT NOT NULL,reasoning TEXT NOT NULL,imported_at REAL NOT NULL,analysis_count INTEGER NOT NULL,card_count INTEGER NOT NULL,
                    entry_ids TEXT NOT NULL,backup_sha256 TEXT NOT NULL,before_rows TEXT NOT NULL)''')
                db.execute('INSERT INTO dot_imports VALUES (?,?,?,?,?,?,?,?,?,?,?)',(batch_id,packet['manifest_hash'],result_hash,MODEL,'xhigh',now,len(checked),card_count,json.dumps([a['entry_id'] for a in selected]),backup_hash,json.dumps(before,ensure_ascii=False)))
        finally:db.close()
    return {'state':'imported','batch_id':batch_id,'analysis_count':len(checked),'card_count':card_count,'model':MODEL,'source_mode':'reference_only'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--manifest',required=True)
    parser.add_argument('--results',required=True);parser.add_argument('--batch-limit',type=int,default=3);parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:
        config=json.loads(Path(args.config).read_text());packet=json.loads(Path(args.manifest).read_text());results=json.loads(Path(args.results).read_text())
        _,_,selected=identity(packet,results,args.batch_limit)
        print(json.dumps(import_batch(config,packet,results,lambda:legacy.read_upstream(config,{'articles':selected}),apply=args.apply,batch_limit=args.batch_limit),ensure_ascii=False))
    except Exception as exc:
        code=str(exc) if type(exc) is ValueError and legacy.re_code(str(exc)) else type(exc).__name__
        print(json.dumps({'state':'blocked','error':code}));raise SystemExit(1)


if __name__=='__main__':main()
