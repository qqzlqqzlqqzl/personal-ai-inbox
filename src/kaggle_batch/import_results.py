"""Transactional import into the existing Inbox tables, after business validation.

Callers must supply IDs whose current upstream input has been checked. The
SQLite source version is checked again while holding the write transaction.
No settings or paid-API budgets are changed here.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time

def import_validated(database, manifest, validation, upstream_unchanged):
    if manifest.get('diagnostic',{}).get('must_not_import'):
        raise ValueError('Diagnostic batch cannot be imported')
    if validation['batch_id']!=manifest['batch_id'] or validation['manifest_hash']!=manifest['manifest_hash']:
        raise ValueError('Validation belongs to another batch')
    inputs={item['id']:item for item in manifest['items']}
    model=manifest['model']['filename']+'@'+manifest['model']['model_revision'][:12]
    db=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=rw',uri=True,timeout=15)
    db.row_factory=sqlite3.Row
    outcome=[]
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS kaggle_imports (
            batch_id TEXT NOT NULL,item_id TEXT NOT NULL,input_hash TEXT NOT NULL,
            imported_at REAL NOT NULL,PRIMARY KEY(batch_id,item_id))''')
        for checked in validation['items']:
            key=checked['id']
            if key not in inputs or not checked['valid']:
                outcome.append({'id':key,'state':'invalid'})
                continue
            item=inputs[key]
            if item['kind'] not in {'analysis','translation'}:
                raise ValueError('Unsupported business task')
            refs=item['source_refs']
            if checked['input_hash']!=item['input_hash']:
                raise ValueError('Validated input version mismatch')
            with db:
                db.execute('BEGIN IMMEDIATE')
                prior=db.execute('SELECT input_hash FROM kaggle_imports WHERE batch_id=? AND item_id=?',
                                 (manifest['batch_id'],key)).fetchone()
                if prior:
                    if prior[0]!=item['input_hash']:
                        raise ValueError('Import identity collision')
                    outcome.append({'id':key,'state':'already_imported'})
                    continue
                if not all(ref['entry_id'] in upstream_unchanged for ref in refs):
                    outcome.append({'id':key,'state':'upstream_changed_or_unavailable'})
                    continue
                now=time.time()
                if item['kind']=='analysis':
                    ref=refs[0]
                    row=db.execute('SELECT * FROM analyses WHERE entry_id=?',(ref['entry_id'],)).fetchone()
                    if not row or any(row[name]!=ref[name] for name in ('user_id','content_hash','source_text')):
                        outcome.append({'id':key,'state':'source_changed'})
                        continue
                    if row['state']=='done':
                        outcome.append({'id':key,'state':'existing_result_preserved'})
                        continue
                    settings_row=db.execute("SELECT value FROM settings WHERE name='preferences'").fetchone()
                    settings=json.loads(settings_row[0]) if settings_row else {}
                    if settings.get('prompt') and settings['prompt']!=item['messages'][0]['content']:
                        outcome.append({'id':key,'state':'prompt_changed'})
                        continue
                    data=checked['result']
                    prompt_hash=hashlib.sha256((item['messages'][0]['content']+model+'kaggle').encode()).hexdigest()
                    db.execute('''UPDATE analyses SET state='done',result=?,score=?,technical_score=?,
                        business_score=?,model=?,prompt_hash=?,tokens=?,analyzed_at=?,updated_at=?,
                        attempts=0,next_try=0,error=NULL WHERE entry_id=?''',
                        (json.dumps(data,ensure_ascii=False),data['score'],data['technical_score'],
                         data['business_score'],model,prompt_hash,int(checked.get('tokens') or 0),
                         now,now,ref['entry_id']))
                else:
                    rows=[db.execute('SELECT * FROM card_translations WHERE entry_id=?',
                                     (ref['entry_id'],)).fetchone() for ref in refs]
                    columns=('user_id','source_hash','original_title','excerpt','source_kind')
                    if any(not row or any(row[name]!=ref[name] for name in columns) for row,ref in zip(rows,refs)):
                        outcome.append({'id':key,'state':'source_changed'})
                        continue
                    # A mixed batch may contain already translated/native cards. Preserve them.
                    changed=0
                    for row in rows:
                        if row['status'] in {'done','native'}:
                            continue
                        pair=checked['result'].get(str(row['entry_id'])) or checked['result'].get(row['entry_id'])
                        if not pair or len(pair)!=2:
                            raise ValueError('Validated translation missing an entry')
                        db.execute('''UPDATE card_translations SET title_zh=?,summary_zh=?,status='done',
                            model=?,attempts=0,next_try=0,error=NULL,updated_at=?,translated_at=? WHERE entry_id=?''',
                            (*pair,model,now,now,row['entry_id']))
                        db.execute('''INSERT OR REPLACE INTO card_translation_versions
                            (entry_id,user_id,source_hash,original_title,title_zh,summary_zh,source_kind,model,translated_at)
                            VALUES (?,?,?,?,?,?,?,?,?)''',
                            (row['entry_id'],row['user_id'],row['source_hash'],row['original_title'],
                             *pair,row['source_kind'],model,now))
                        changed+=1
                    if not changed:
                        outcome.append({'id':key,'state':'existing_result_preserved'})
                        continue
                db.execute('INSERT INTO kaggle_imports VALUES (?,?,?,?)',
                           (manifest['batch_id'],key,item['input_hash'],now))
                outcome.append({'id':key,'state':'imported'})
    finally:
        db.close()
    return {'batch_id':manifest['batch_id'],'items':outcome}
