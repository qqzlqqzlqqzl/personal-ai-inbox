"""Shared claims and bounded retries for the two cloud Kaggle workers."""
import json
from pathlib import Path
import sqlite3
import time

FINISHED = {'imported', 'retired', 'resolved'}
ACCEPTED = {'imported', 'already_imported', 'existing_result_preserved'}


def claimed_entries(roots):
    claimed=set()
    for root in map(Path,roots):
        database=root/'batches.sqlite3'
        if not database.exists():
            continue
        db=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
        try:
            batches=db.execute("SELECT id FROM batches WHERE state NOT IN ('imported','retired','resolved')").fetchall()
        finally:
            db.close()
        for (batch,) in batches:
            # Prefer the transactional claim ledger. A missing/corrupt manifest
            # in one parked batch no longer crashes every healthy worker.
            with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as ledger:
                exists=ledger.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_claims'").fetchone()
                rows=ledger.execute('SELECT entry_id FROM batch_claims WHERE batch_id=?',(batch,)).fetchall() if exists else []
            if rows:
                claimed.update(int(row[0]) for row in rows)
            else:
                manifest=json.loads((root/batch/'manifest.json').read_text(encoding='utf-8'))
                claimed.update(ref['entry_id'] for item in manifest['items'] for ref in item['source_refs'])
    return claimed


def defer_unresolved(database, manifest, outcome, delay=21600, infrastructure_ids=()):
    """Keep good imports. Cap quality retries; infrastructure interruptions wait separately.

    The batch receipt makes replay idempotent even across a process crash.
    Changed sources and concurrent successful results are never overwritten.
    """
    if manifest.get('diagnostic',{}).get('must_not_import'):
        raise ValueError('Diagnostic batch cannot change the queue')
    states={item['id']:item['state'] for item in outcome['items']}
    infrastructure_ids=set(infrastructure_ids)
    db=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=rw',uri=True,timeout=15)
    db.row_factory=sqlite3.Row
    actions=[]
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS kaggle_deferred (
                batch_id TEXT,item_id TEXT,entry_id INTEGER,action TEXT,
                PRIMARY KEY(batch_id,item_id,entry_id))''')
            db.execute('CREATE TABLE IF NOT EXISTS kaggle_infra_retries(entry_id INTEGER,kind TEXT,source_version TEXT,failures INTEGER,last_at REAL,PRIMARY KEY(entry_id,kind,source_version))')
            for item in manifest['items']:
                if states.get(item['id']) in ACCEPTED:
                    continue
                for ref in item['source_refs']:
                    key=(manifest['batch_id'],item['id'],ref['entry_id'])
                    previous=db.execute('SELECT action FROM kaggle_deferred WHERE batch_id=? AND item_id=? AND entry_id=?',key).fetchone()
                    if previous:
                        actions.append({'entry_id':ref['entry_id'],'item_id':item['id'],'action':previous[0]})
                        continue
                    table,column,success,fields=('analyses','state',{'done'},('user_id','content_hash','source_text')) if item['kind']=='analysis' else (
                        'card_translations','status',{'done','native'},('user_id','source_hash','original_title','excerpt','source_kind'))
                    row=db.execute('SELECT * FROM '+table+' WHERE entry_id=?',(ref['entry_id'],)).fetchone()
                    action='source_changed_or_completed'
                    if row and row[column] not in success and all(row[field]==ref[field] for field in fields):
                        now=time.time();retry_delay=delay
                        if item['id'] in infrastructure_ids:
                            version=str(ref.get('content_hash') or ref.get('source_hash') or item['input_hash'])
                            old=db.execute('SELECT failures FROM kaggle_infra_retries WHERE entry_id=? AND kind=? AND source_version=?',(ref['entry_id'],item['kind'],version)).fetchone()
                            failures=(old[0] if old else 0)+1
                            db.execute('INSERT OR REPLACE INTO kaggle_infra_retries VALUES (?,?,?,?,?)',(ref['entry_id'],item['kind'],version,failures,now))
                            # A killed/expired GPU session is not evidence of bad article/model output.
                            attempts=row['attempts'] or 0;action='infrastructure_retry_scheduled'
                            state='ai_error' if item['kind']=='analysis' else 'error'
                            retry_delay=max(delay,min(21600,660*2**min(failures-1,5)))
                        else:
                            attempts=(row['attempts'] or 0)+1
                            action='requires_model_review' if attempts>=3 else 'retry_scheduled'
                            state='requires_model_review' if attempts>=3 else ('ai_error' if item['kind']=='analysis' else 'error')
                        db.execute('UPDATE '+table+' SET '+column+'=?,attempts=?,next_try=?,updated_at=?,error=? WHERE entry_id=?',
                                   (state,attempts,now+retry_delay,now,'Kaggle '+action,ref['entry_id']))
                    db.execute('INSERT INTO kaggle_deferred VALUES (?,?,?,?)',(*key,action))
                    actions.append({'entry_id':ref['entry_id'],'item_id':item['id'],'action':action})
    finally:
        db.close()
    return actions


def recovery_generation(database, manifest):
    """Version a genuinely new infrastructure retry without altering article quality attempts."""
    ids=sorted({ref['entry_id'] for item in manifest['items'] for ref in item['source_refs']})
    if not ids:return 0
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=15) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='kaggle_infra_retries'").fetchone():return 0
        marks=','.join('?' for _ in ids)
        return int(db.execute(f'SELECT COALESCE(SUM(failures),0) FROM kaggle_infra_retries WHERE entry_id IN ({marks})',ids).fetchone()[0])
