"""Resolve daily work from enabled Miniflux subscriptions, not a historical snapshot.

Old allowlists and submitted manifests remain unchanged for replay/reconciliation.
Feed lookup fails closed: an unavailable reader must never expand the work scope.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys


def enabled_feeds(config):
    import httpx
    sys.path.insert(0, str(Path(config['source']).resolve()))
    from initialize_secrets import read_env
    from worker import MF
    token = read_env('ai.env')['MINIFLUX_API_KEY']
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        response = client.get(MF + '/v1/feeds', headers={'X-Auth-Token': token})
        response.raise_for_status()
        feeds = response.json()
    if not isinstance(feeds, list) or any(not isinstance(f, dict) for f in feeds):
        raise ValueError('Invalid reader feed catalog')
    return feeds


def resolve_entry_ids(config):
    mode = config.get('queue_scope', 'allowlist')
    if mode == 'allowlist':
        path = config.get('entry_allowlist')
        return json.loads(Path(path).read_text())['entry_ids'] if path else None
    if mode != 'all_enabled_feeds':
        raise ValueError('Unknown Kaggle queue scope')
    uid = config.get('scope_user_id')
    if type(uid) is not int or uid < 1:
        raise ValueError('Live scope requires an explicit user id')
    feeds = enabled_feeds(config)
    ids = [f['id'] for f in feeds if type(f.get('id')) is int
           and f.get('user_id') == uid and not f.get('disabled', False)]
    if not ids:
        return []
    with sqlite3.connect(Path(config['database']).resolve().as_uri()+'?mode=ro',
                         uri=True, timeout=15) as db:
        # TEMP tables avoid SQLite variable limits for large subscription sets.
        db.execute('CREATE TEMP TABLE live_feed_ids(id INTEGER PRIMARY KEY)')
        db.executemany('INSERT OR IGNORE INTO live_feed_ids VALUES (?)', ((i,) for i in ids))
        rows = db.execute('''SELECT a.entry_id FROM analyses a JOIN live_feed_ids f
                             ON f.id=a.feed_id WHERE a.user_id=? AND a.state!='removed'
                             ORDER BY a.entry_id''', (uid,)).fetchall()
    return [row[0] for row in rows]
