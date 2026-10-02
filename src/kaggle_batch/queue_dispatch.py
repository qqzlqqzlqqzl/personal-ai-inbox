"""Required lane claims and bounded retries for cloud Kaggle workers."""
import json
import hashlib
import re
try:
    from .recovery_policy import ProviderError
except ImportError:
    from recovery_policy import ProviderError
from pathlib import Path
import sqlite3
import time

FINISHED = {'imported', 'retired', 'resolved'}
ACCEPTED = {'imported', 'already_imported', 'existing_result_preserved'}


class DispatchBlocked(ProviderError):
    """Fixed diagnostics only: never retain database paths or raw exceptions."""
    REASONS = {'missing_ledger', 'unreadable_ledger', 'invalid_ledger',
               'invalid_state', 'invalid_id', 'orphan_claim', 'invalid_manifest',
               'invalid_topology', 'initialization_evidence'}

    def __init__(self, reason, peer=0):
        super().__init__('local_state')
        self.reason = reason if reason in self.REASONS else 'invalid_ledger'
        self.retry_record = None
        self.peer = 'peer_' + str(peer) if type(peer) is int and peer >= 0 else 'peer_0'

    def report(self):
        return {'state': 'dispatch_blocked', 'recovery_error': self.code,
                'reason': self.reason, 'peer': self.peer, 'gpu_started': False}


def block_with_backoff(root, error):
    """Best-effort fixed local-state backoff; never open or recreate a ledger."""
    try:
        from .recovery_policy import record_failure
    except ImportError:
        from recovery_policy import record_failure
    class QuietAudit:
        def append(self, *args, **kwargs):
            pass
    now=time.time()
    report = {**error.report(),'code':'local_state','failures':1,'at':now,'retry_at':now+660}
    if error.retry_record is not None:
        return {**report, **error.retry_record}
    try:
        if root is not None and Path(root).is_dir():
            report.update(record_failure(root, 'local_state', QuietAudit(), None))
    except (OSError, ValueError, TypeError):
        pass
    return report


def month_roots(root):
    """Known campaign topology also applies to the standalone Controller CLI."""
    root=Path(root).resolve()
    keys=('primary','secondary','third','fourth','fifth')
    if root.name in {'kaggle-month-' + key for key in keys}:
        return [root.parent / ('kaggle-month-' + key) for key in keys]
    return []


def required_roots(config):
    try:
        own = config['state_root']
        peers = config.get('peer_state_roots', [])
        if not isinstance(own, (str, Path)) or not own or not isinstance(peers, list):
            raise ValueError
        values = [own, *peers]
        if any(not isinstance(p, (str, Path)) or not p for p in values):
            raise ValueError
        roots = list(dict.fromkeys(Path(p).resolve() for p in values))
        if not set(month_roots(own)) <= set(roots):
            raise ValueError
        return roots
    except (KeyError, TypeError, ValueError, OSError):
        raise DispatchBlocked('invalid_topology') from None


def _batch_id(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,200}', value) is not None


def _entry_id(value):
    if type(value) is not int or value <= 0 or value > 2**63-1:
        raise ValueError
    return value


def _manifest_claims(root, batch, expected):
    value = json.loads((root / batch / 'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('batch_id') != batch:
        raise ValueError
    canonical = {k: v for k, v in value.items() if k not in {'batch_id', 'manifest_hash'}}
    actual = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                            separators=(',', ':')).encode()).hexdigest()
    if value.get('manifest_hash') != actual or (expected is not None and expected != actual):
        raise ValueError
    items = value.get('items')
    if not isinstance(items, list) or not items:
        raise ValueError
    claims = set()
    item_ids = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or item['id'] in item_ids:
            raise ValueError
        item_ids.add(item['id'])
        refs = item.get('source_refs')
        if not isinstance(refs, list) or not refs:
            raise ValueError
        for ref in refs:
            if not isinstance(ref, dict):
                raise ValueError
            claims.add(_entry_id(ref.get('entry_id')))
    return claims


def claimed_entries(roots):
    """All required ledgers or a typed block; one read-only snapshot per root."""
    claimed = set()
    try:
        roots = list(roots)
    except (TypeError,ValueError):
        raise DispatchBlocked('invalid_topology') from None
    if not roots:
        raise DispatchBlocked('invalid_topology')
    for peer, root in enumerate(roots):
        db = None
        try:
            root = Path(root)
            database = root / 'batches.sqlite3'
            if not database.is_file():
                raise DispatchBlocked('missing_ledger', peer)
            db = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)
            db.execute('BEGIN')
            if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise DispatchBlocked('invalid_ledger', peer)
            columns = {r[1] for r in db.execute('PRAGMA table_info(batches)')}
            if not {'id','manifest_hash','state','remote_status','error','updated'} <= columns:
                raise DispatchBlocked('invalid_ledger', peer)
            batches = db.execute('SELECT id,state,manifest_hash FROM batches').fetchall()
            states = {}
            hashes = {}
            for batch, state, manifest_hash in batches:
                if not _batch_id(batch) or batch in states:
                    raise DispatchBlocked('invalid_id', peer)
                if state not in FINISHED | {'prepared','submitting','submitted','running','submit_unknown','terminal','downloaded'}:
                    raise DispatchBlocked('invalid_state', peer)
                if not isinstance(manifest_hash, str) or re.fullmatch(r'[0-9a-f]{64}', manifest_hash) is None:
                    raise DispatchBlocked('invalid_ledger', peer)
                states[batch] = state
                hashes[batch] = manifest_hash
            ledger = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_claims'").fetchone()
            per_batch = {}
            if ledger:
                for batch, entry in db.execute('SELECT batch_id,entry_id FROM batch_claims'):
                    if not _batch_id(batch):
                        raise DispatchBlocked('invalid_id', peer)
                    if batch not in states:
                        raise DispatchBlocked('orphan_claim', peer)
                    try:
                        entry = _entry_id(entry)
                    except ValueError:
                        raise DispatchBlocked('invalid_id', peer) from None
                    per_batch.setdefault(batch, set()).add(entry)
            for batch, state in states.items():
                if state in FINISHED:
                    continue
                refs = per_batch.get(batch)
                if refs:
                    # Transactional claims remain authoritative even if a parked
                    # manifest has disappeared or is corrupt.
                    claimed.update(refs)
                else:
                    try:
                        claimed.update(_manifest_claims(root, batch, hashes[batch]))
                    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                        raise DispatchBlocked('invalid_manifest', peer) from None
        except DispatchBlocked:
            raise
        except sqlite3.OperationalError:
            raise DispatchBlocked('unreadable_ledger', peer) from None
        except (sqlite3.Error, OSError, ValueError, TypeError):
            raise DispatchBlocked('invalid_ledger', peer) from None
        finally:
            if db is not None:
                db.close()
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
