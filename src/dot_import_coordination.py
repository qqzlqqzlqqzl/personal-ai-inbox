"""Read-only coordination checks shared by dot exporters and importer preflights.

Validate declared ledgers and the Controller's fallback manifest identity/item
contract. Declared roots remain an operator catalog; this is not a claim of full
Controller topology, lifecycle or recovery parity.
"""
import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

FINISHED = {'imported', 'retired', 'resolved'}
ACTIVE = {'prepared', 'submitting', 'submitted', 'running', 'submit_unknown', 'terminal', 'downloaded'}
LEDGER_COLUMNS = {'id', 'manifest_hash', 'state', 'remote_status', 'error', 'updated'}


def positive_id(value):
    return type(value) is int and 0 < value <= 2**63 - 1


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def batch_id(value):
    return isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,200}', value) is not None


def ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def manifest_claims(root, batch, expected):
    """A pre-claims manifest must bind its batch, canonical hash and item refs."""
    value = json.loads((root / batch / 'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('batch_id') != batch:
        raise ValueError('invalid_lane_manifest')
    canonical = {k: v for k, v in value.items() if k not in {'batch_id', 'manifest_hash'}}
    actual = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':')).encode()).hexdigest()
    if value.get('manifest_hash') != actual or expected != actual:
        raise ValueError('invalid_lane_manifest')
    items = value.get('items')
    if not isinstance(items, list) or not items:
        raise ValueError('incomplete_lane_manifest')
    claims, item_ids = set(), set()
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not item['id'] or item['id'] in item_ids):
            raise ValueError('invalid_lane_manifest')
        item_ids.add(item['id'])
        refs = item.get('source_refs')
        if not isinstance(refs, list) or not refs:
            raise ValueError('incomplete_lane_manifest')
        for ref in refs:
            if not isinstance(ref, dict) or not positive_id(ref.get('entry_id')):
                raise ValueError('invalid_lane_claim')
            claims.add(ref['entry_id'])
    return claims


def lane_claims(config):
    """Read every declared lane; missing/incomplete state is never an empty lane."""
    peers, own = config.get('peer_state_roots'), config.get('state_root')
    if (not isinstance(peers, list) or not peers or not isinstance(own, str) or not own
            or any(not isinstance(p, str) or not p for p in peers)):
        raise ValueError('complete_lane_roots_required')
    roots = list(dict.fromkeys(Path(p).resolve() for p in [own, *peers]))
    claimed = set()
    for root in roots:
        with closing(ro(root / 'batches.sqlite3')) as db:
            db.execute('BEGIN')
            if [row[0] for row in db.execute('PRAGMA quick_check')] != ['ok']:
                raise ValueError('invalid_lane_ledger')
            columns = {row[1] for row in db.execute('PRAGMA table_info(batches)')}
            if not LEDGER_COLUMNS <= columns:
                raise ValueError('invalid_lane_ledger')
            batches = db.execute('SELECT id,state,manifest_hash FROM batches').fetchall()
            states, hashes = {}, {}
            for row in batches:
                bid, state, fingerprint = row['id'], row['state'], row['manifest_hash']
                if not batch_id(bid) or bid in states or state not in FINISHED | ACTIVE:
                    raise ValueError('invalid_lane_state')
                if not isinstance(fingerprint, str) or re.fullmatch(r'[0-9a-f]{64}', fingerprint) is None:
                    raise ValueError('invalid_lane_ledger')
                states[bid], hashes[bid] = state, fingerprint
            has_claims = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_claims'").fetchone()
            per_batch = {}
            if has_claims:
                for row in db.execute('SELECT batch_id,entry_id FROM batch_claims'):
                    bid, eid = row['batch_id'], row['entry_id']
                    if not batch_id(bid) or not positive_id(eid):
                        raise ValueError('invalid_lane_claim')
                    if bid not in states:
                        raise ValueError('orphan_lane_claim')
                    per_batch.setdefault(bid, set()).add(eid)
            for bid, state in states.items():
                if state in FINISHED:
                    continue
                refs = per_batch.get(bid)
                # Transactional claims are authoritative, including parked batches
                # whose manifest was removed or corrupted after preparation.
                claimed.update(refs if refs else manifest_claims(root, bid, hashes[bid]))
    return claimed


def live_leases(db, cutoff):
    # Validate every lease, including expired or unrelated entries. Require the table.
    if not finite_number(cutoff):
        raise ValueError('invalid_prepare_lease')
    leases = db.execute('SELECT entry_id,expires FROM kaggle_prepare_leases').fetchall()
    if any(not positive_id(row['entry_id']) or not finite_number(row['expires']) for row in leases):
        raise ValueError('invalid_prepare_lease')
    return {row['entry_id'] for row in leases if row['expires'] > cutoff}


def exclusions(config):
    """No missing or malformed state may become an empty exclusion set."""
    claimed = lane_claims(config)
    with closing(ro(config['database'])) as db:
        db.execute('BEGIN')
        leased = live_leases(db, time.time())
    return claimed, leased
