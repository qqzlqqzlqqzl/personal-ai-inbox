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
            manifest=json.loads((root/batch/'manifest.json').read_text(encoding='utf-8'))
            claimed.update(ref['entry_id'] for item in manifest['items'] for ref in item['source_refs'])
    return claimed


def defer_unresolved(database, manifest, outcome, delay=21600):
    """Keep good imports. Retry only unchanged failed inputs, at most 3 attempts.

    The batch receipt makes replay idempotent even across a process crash.
    Changed sources and concurrent successful results are never overwritten.
    """
    if manifest.get('diagnostic',{}).get('must_not_import'):
        raise ValueError('Diagnostic batch cannot change the queue')
    states={item['id']:item['state'] for item in outcome['items']}
    db=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=rw',uri=True,timeout=15)
    db.row_factory=sqlite3.Row
    actions=[]
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE IF NOT EXISTS kaggle_deferred (
                batch_id TEXT,item_id TEXT,entry_id INTEGER,action TEXT,
                PRIMARY KEY(batch_id,item_id,entry_id))''')
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
                        attempts=(row['attempts'] or 0)+1
                        action='requires_model_review' if attempts>=3 else 'retry_scheduled'
                        state='requires_model_review' if attempts>=3 else ('ai_error' if item['kind']=='analysis' else 'error')
                        now=time.time()
                        db.execute('UPDATE '+table+' SET '+column+'=?,attempts=?,next_try=?,updated_at=?,error=? WHERE entry_id=?',
                                   (state,attempts,now+delay,now,'Kaggle '+states.get(item['id'],'missing_output'),ref['entry_id']))
                    db.execute('INSERT INTO kaggle_deferred VALUES (?,?,?,?)',(*key,action))
                    actions.append({'entry_id':ref['entry_id'],'item_id':item['id'],'action':action})
    finally:
        db.close()
    return actions
