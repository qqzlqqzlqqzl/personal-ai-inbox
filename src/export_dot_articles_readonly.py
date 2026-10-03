"""Read-only first-batch export. No credentials, provider calls, or DB writes."""
import argparse
import ast
import hashlib
import json
import sqlite3
import time
import fcntl
import sys
from pathlib import Path

from dot_import_coordination import FINISHED, exclusions
ELIGIBLE = {'pending', 'waiting_model', 'budget_paused', 'fetch_error', 'ai_error'}
CARD_ELIGIBLE = {'pending', 'error', 'budget_paused', 'waiting_model'}
STORED_SOURCES = {'original_url_site_rule', 'adafruit_linked_original', 'social_adapter_post', 'product_page'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def ro(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def constants(path, names):
    values = {}
    for node in ast.parse(Path(path).read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    values[target.id] = ast.literal_eval(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id in names and isinstance(node.op, ast.Add):
            values[node.target.id] += ast.literal_eval(node.value)
    return values


def export(config, limit=3, enabled_feed_ids=None):
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError('First export is limited to 1..3 articles')
    source = Path(config['source'])
    if enabled_feed_ids is None:
        raise ValueError('Verified enabled feed scope is required')
    enabled_feed_ids = set(enabled_feed_ids)
    defaults = constants(source / 'core.py', {'DEFAULT_PROMPT'})
    card_prompt = constants(source / 'card_translation.py', {'PROMPT', 'VERSION'})
    fidelity = constants(source / 'kaggle_batch/build_manifest.py', {'ANALYSIS_FIDELITY', 'TRANSLATION_FIDELITY'})
    claimed, leased = exclusions(config)
    now = time.time()
    with ro(config['database']) as db:
        db.execute('BEGIN')
        stored = db.execute("SELECT value FROM settings WHERE name='preferences'").fetchone()
        preferences = json.loads(stored[0]) if stored else {}
        if preferences.get('enabled', True) is not False or preferences.get('translation_enabled', True) is not False:
            raise ValueError('Existing paid workers must already be disabled')
        prompt = preferences.get('prompt', defaults['DEFAULT_PROMPT'])
        if not isinstance(prompt, str) or not prompt:
            raise ValueError('Invalid current analysis prompt')
        rows = [dict(row) for row in db.execute('SELECT * FROM analyses WHERE user_id=? ORDER BY published_at DESC,entry_id DESC', (int(config.get('scope_user_id', 1)),))]
        cards = {row['entry_id']: dict(row) for row in db.execute('SELECT * FROM card_translations WHERE user_id=?', (int(config.get('scope_user_id', 1)),))}
    # Re-read exclusions after the snapshots. Import must repeat these checks.
    current_claimed, current_leased = exclusions(config)
    blocked = claimed | leased | current_claimed | current_leased
    selected, needs_preparation = [], []
    for row in rows:
        if row.get('feed_id') not in enabled_feed_ids or row['entry_id'] in blocked or row['state'] not in ELIGIBLE or (row.get('next_try') or 0) > now or (row.get('attempts') or 0) >= 3:
            continue
        text = row.get('source_text')
        if (not isinstance(text, str) or not text.strip() or row.get('truncated') or not row.get('content_hash')
                or row.get('content_source') not in STORED_SOURCES or row.get('source_chars') != len(text)
                or not row.get('extracted_at')):
            if len(needs_preparation) < 3:
                needs_preparation.append({'entry_id': row['entry_id'], 'user_id': row['user_id'], 'published_at': row.get('published_at'), 'reason': 'missing_complete_stored_source'})
            continue
        fields = ('entry_id','user_id','feed_id','title','url','published_at','state','attempts',
                  'content_hash','source_text','source_chars','content_source','truncated','extracted_at')
        item = {key: row.get(key) for key in fields}
        item['source_text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
        item['snapshot_hash'] = digest(item)
        card = cards.get(row['entry_id'])
        if card and card['status'] in CARD_ELIGIBLE and (card.get('attempts') or 0) < 3 and (card.get('next_try') or 0) <= now:
            keys = ('entry_id','user_id','source_hash','original_title','excerpt','source_kind','status','attempts','next_try')
            item['card'] = {key: card.get(key) for key in keys}
            item['card']['snapshot_hash'] = digest(item['card'])
        else:
            item['card'] = None
        selected.append(item)
        if len(selected) >= limit:
            break
    packet = {'schema':'dot-article-export-v1','exported_at':now,'read_only':True,
        'intended_executor':{'provider':'dot','model':'gpt-6-astra','reasoning':'xhigh'},
        'analysis_prompt':prompt,'analysis_prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
        'max_output_tokens':preferences.get('max_output_tokens',1500),
        'translation_prompt':card_prompt['PROMPT'], 'translation_prompt_version':card_prompt['VERSION'],
        'translation_prompt_sha256':hashlib.sha256(card_prompt['PROMPT'].encode()).hexdigest(),
        'analysis_fidelity':fidelity['ANALYSIS_FIDELITY'],'translation_fidelity':fidelity['TRANSLATION_FIDELITY'],
        'articles':selected,'needs_source_preparation':needs_preparation,
        'excluded_claimed_count':len(claimed | current_claimed),'excluded_leased_count':len(leased | current_leased),
        'enabled_feed_ids':sorted(enabled_feed_ids),
        'upstream_verification':'required_again_at_import; content_hash is stored source version, not proof of current upstream',
        'source_fidelity':'stored source_text is exported in full; source provenance/truncation still require review'}
    packet['export_hash'] = digest(packet)
    return packet


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    try:
        sys.path.insert(0, config['source'])
        from kaggle_batch.live_scope import enabled_feeds
        feeds = enabled_feeds(config)  # Existing read-only Miniflux GET /v1/feeds.
        uid = int(config.get('scope_user_id', 1))
        enabled = {f['id'] for f in feeds if type(f.get('id')) is int and f.get('user_id') == uid and not f.get('disabled', False)}
        # Open an EXISTING coordination lock read-only; never create a file.
        with (Path(config['coordination_root']) / 'bridge.lock').open('rb') as guard:
            fcntl.flock(guard, fcntl.LOCK_SH | fcntl.LOCK_NB)
            print(json.dumps(export(config, enabled_feed_ids=enabled), ensure_ascii=False))
    except Exception as exc:
        # Do not echo config, credentials, SQL, source contents, or exception text.
        print(json.dumps({'read_only':True,'error':type(exc).__name__,'stage':'export_failed'}))
        raise SystemExit(1)
