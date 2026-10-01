"""Versioned, URL-only continuation manifests; never fetch bodies or mutate state.

The URL importer owns the projections and CAS contract. This command holds the
existing bridge lock and reads the live SQLite database (including its WAL), not
a copy of its main file. Import must still revalidate every mutable dependency.
"""
import argparse
import fcntl
import ipaddress
import json
import re
import time
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import dot_article_import as legacy
import dot_url_article_import as contract
from export_dot_articles_readonly import CARD_ELIGIBLE, ELIGIBLE, FINISHED, digest, ro

EXPORTER_VERSION = 'dot-url-exporter-v1'
SAFE_ERROR_CODES = frozenset({
    'invalid_exclusion_ids', 'duplicate_exclusion_ids', 'invalid_live_feed_catalog',
    'invalid_source_url', 'sensitive_source_url', 'complete_lane_roots_required',
    'invalid_lane_state', 'orphan_lane_claim', 'incomplete_lane_manifest',
    'invalid_lane_claim', 'invalid_prepare_lease', 'explicit_scope_user_mismatch',
    'invalid_batch_limit', 'existing_paid_workers_enabled', 'invalid_analysis_prompt',
    'invalid_output_token_policy',
})
HASH_SPEC = {
    'version': 'python-json-sha256-v1',
    'algorithm': 'sha256',
    'encoding': 'utf-8',
    'json': 'json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))',
    'text': 'sha256(text.encode("utf-8")); no trimming or Unicode normalization',
    'null': 'JSON null is preserved; absent fields are not substituted',
}


def positive_id(value):
    return type(value) is int and 0 < value <= 2**63 - 1


def exclusion_ids(values):
    """Explicit JSON integers only: no coercion, booleans, duplicates or defaults."""
    if not isinstance(values, list) or any(not positive_id(v) for v in values):
        raise ValueError('invalid_exclusion_ids')
    if len(values) != len(set(values)):
        raise ValueError('duplicate_exclusion_ids')
    return set(values)


def enabled_scope(feeds, uid):
    if not isinstance(feeds, list):
        raise ValueError('invalid_live_feed_catalog')  # noqa: TRY004 -- stable operator error code
    enabled, seen = set(), set()
    for feed in feeds:
        if (not isinstance(feed, dict) or not positive_id(feed.get('id'))
                or not positive_id(feed.get('user_id'))
                or type(feed.get('disabled')) is not bool or feed['id'] in seen):
            raise ValueError('invalid_live_feed_catalog')
        seen.add(feed['id'])
        if feed['user_id'] == uid and feed['disabled'] is False:
            enabled.add(feed['id'])
    return enabled


def safe_url(value):
    """Reuse import validation; also reject ambiguous hosts/encoded secret keys.

    This is a conservative lexical check, not a DNS or redirect attestation.
    No article request or DNS lookup is performed by this exporter.
    """
    contract.public_url(value)
    parsed = urlsplit(value)
    host = parsed.hostname.encode('idna').decode('ascii').lower()
    if ('\\' in value or '\x7f' in value or host.endswith('.')
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
            or any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-')
                   for label in host.split('.'))
            or re.fullmatch(r'[0-9.]+', host) and len(host.split('.')) != 4
            or re.fullmatch(r'(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*', host)
               and any(label.startswith(('0x', '0')) for label in host.split('.'))):
        raise ValueError('invalid_source_url')
    if any(re.search(r'token|secret|password|api[_-]?key|signature|credential|auth|session|jwt|bearer|access[_-]?key', key, re.IGNORECASE)
           for part in (parsed.query, parsed.fragment)
           for key, _ in parse_qsl(part, keep_blank_values=True)):
        raise ValueError('sensitive_source_url')
    if re.fullmatch(r'[0-9.]+', host):
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError('invalid_source_url') from None
        if not address.is_global or str(address) != host:
            raise ValueError('invalid_source_url')
    return value


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
            batches = db.execute('SELECT id,state FROM batches').fetchall()
            has_claims = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_claims'").fetchone()
            if len({row['id'] for row in batches}) != len(batches):
                raise ValueError('invalid_lane_state')
            if has_claims and db.execute('SELECT 1 FROM batch_claims c LEFT JOIN batches b ON b.id=c.batch_id WHERE b.id IS NULL LIMIT 1').fetchone():
                raise ValueError('orphan_lane_claim')
            for batch in batches:
                bid, state = batch['id'], batch['state']
                if (not isinstance(bid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', bid)
                        or not isinstance(state, str) or not state):
                    raise ValueError('invalid_lane_state')
                if state in FINISHED:
                    continue
                refs = db.execute('SELECT entry_id FROM batch_claims WHERE batch_id=?', (bid,)).fetchall() if has_claims else []
                if refs:
                    ids = [row['entry_id'] for row in refs]
                else:
                    # The existing controller also supports pre-claims manifests.
                    manifest = json.loads((root / bid / 'manifest.json').read_text(encoding='utf-8'))
                    items = manifest.get('items') if isinstance(manifest, dict) else None
                    if not isinstance(items, list) or not items:
                        raise ValueError('incomplete_lane_manifest')
                    ids = []
                    for item in items:
                        sources = item.get('source_refs') if isinstance(item, dict) else None
                        if not isinstance(sources, list) or not sources:
                            raise ValueError('incomplete_lane_manifest')
                        ids.extend(ref.get('entry_id') if isinstance(ref, dict) else None for ref in sources)
                if any(not positive_id(eid) for eid in ids):
                    raise ValueError('invalid_lane_claim')
                claimed.update(ids)
    return claimed


def live_leases(db, cutoff):
    # Require the table. Do not silently treat an uninitialized store as safe.
    leases = db.execute('SELECT entry_id,expires FROM kaggle_prepare_leases').fetchall()
    if any(not positive_id(row['entry_id']) or not contract.finite_number(row['expires']) for row in leases):
        raise ValueError('invalid_prepare_lease')
    return {row['entry_id'] for row in leases if row['expires'] > cutoff}


def analysis_reason(row, cutoff):
    if row['source_text_is_null'] and contract.reference_snapshot_eligible(row, cutoff):
        return None
    if row['state'] not in ELIGIBLE:
        return 'analysis_state_ineligible'
    if type(row['attempts']) is not int or not 0 <= row['attempts'] < 3:
        return 'analysis_attempts_ineligible'
    if not contract.finite_number(row['next_try']) or row['next_try'] > cutoff:
        return 'analysis_retry_not_due'
    if not contract.finite_number(row['updated_at']) or row['updated_at'] > cutoff:
        return 'analysis_updated_after_cutoff'
    if (not row['source_text_is_null'] or any(row[k] is not None for k in ('content_hash', 'content_source',
                                       'source_chars', 'extracted_at', 'analyzed_at'))
            or row['truncated'] != 0):
        return 'analysis_not_body_null'
    return 'analysis_not_body_null'


def card_reason(card, uid, cutoff, version_exists):
    if card is None:
        return 'card_missing'
    if card['user_id'] != uid:
        return 'card_user_mismatch'
    if card['status'] not in CARD_ELIGIBLE:
        return 'card_state_ineligible'
    if type(card['attempts']) is not int or not 0 <= card['attempts'] < 3:
        return 'card_attempts_ineligible'
    if not contract.finite_number(card['next_try']) or card['next_try'] > cutoff:
        return 'card_retry_not_due'
    if not contract.finite_number(card['updated_at']) or card['updated_at'] > cutoff:
        return 'card_updated_after_cutoff'
    if version_exists:
        return 'card_version_preserved'
    return None


def export(config, *, scope_user_id, limit, exclude_entry_ids, feed_reader=None):
    if not positive_id(scope_user_id) or config.get('scope_user_id') != scope_user_id or type(config.get('scope_user_id')) is not int:
        raise ValueError('explicit_scope_user_mismatch')
    if type(limit) is not int or not 1 <= limit <= 12:
        raise ValueError('invalid_batch_limit')
    excluded = exclusion_ids(exclude_entry_ids)
    if feed_reader is None:
        from kaggle_batch.live_scope import enabled_feeds
        feed_reader = enabled_feeds
    # Open only an existing coordination lock; no directory or lock creation.
    with (Path(config['coordination_root']) / 'bridge.lock').open('rb') as guard:
        fcntl.flock(guard, fcntl.LOCK_SH | fcntl.LOCK_NB)
        feeds = enabled_scope(feed_reader(config), scope_user_id)
        claimed = lane_claims(config)
        with closing(ro(config['database'])) as db:
            db.execute('BEGIN')
            context = legacy.read_context(config, db)  # First SELECT pins the WAL snapshot.
            cutoff = time.time()
            if context['enabled'] is not False or context['translation_enabled'] is not False:
                raise ValueError('existing_paid_workers_enabled')
            if not isinstance(context['prompt'], str) or not context['prompt']:
                raise ValueError('invalid_analysis_prompt')
            if type(context['max_output_tokens']) is not int or not 200 <= context['max_output_tokens'] <= 8000:
                raise ValueError('invalid_output_token_policy')
            leased = live_leases(db, cutoff)
            # Explicit projection excludes result/error/notes and all credentials.
            fields = (*contract.ANALYSIS_FIELDS, 'source_text IS NULL AS source_text_is_null')
            rows = db.execute('SELECT ' + ','.join(fields) + ' FROM analyses WHERE user_id=? '
                              'ORDER BY published_at DESC,entry_id DESC', (scope_user_id,)).fetchall()
            cards = {row['entry_id']: dict(row) for row in db.execute(
                'SELECT ' + ','.join(contract.CARD_FIELDS) + ' FROM card_translations WHERE user_id=?',
                (scope_user_id,))}
            versions = {(row['entry_id'], row['source_hash']) for row in db.execute(
                'SELECT entry_id,source_hash FROM card_translation_versions WHERE user_id=?', (scope_user_id,))}
            # Recheck lane state while the same analysis/card/settings snapshot is open.
            claimed |= lane_claims(config)
            selected, skipped, omitted_cards = [], Counter(), Counter()
            for row in rows:
                eid = row['entry_id']
                reason = None
                if not positive_id(eid):
                    reason = 'invalid_analysis_identity'
                elif eid in excluded:
                    reason = 'explicitly_excluded'
                elif row['feed_id'] not in feeds:
                    reason = 'feed_not_current_enabled_owned'
                elif eid in claimed:
                    reason = 'lane_claimed'
                elif eid in leased:
                    reason = 'prepare_lease_live'
                else:
                    reason = analysis_reason(row, cutoff)
                if reason is None:
                    try:
                        safe_url(row['url'])
                    except (ValueError, UnicodeError):
                        reason = 'unsafe_url'
                if reason is None and len(selected) >= limit:
                    reason = 'batch_limit_reached'
                if reason:
                    skipped[reason] += 1
                    continue
                snap = {key: row[key] for key in contract.ANALYSIS_FIELDS}
                card = cards.get(eid)
                why = card_reason(card, scope_user_id, cutoff, card is not None and (eid, card['source_hash']) in versions)
                if why:
                    omitted_cards[why] += 1
                    card = None
                article = {key: row[key] for key in (*contract.IDENTITY_FIELDS, 'published_at')}
                article.update(analysis_snapshot=snap, analysis_snapshot_hash=digest(snap),
                               card=card, card_snapshot_hash=digest(card))
                article['snapshot_hash'] = digest(article)
                selected.append(article)
            policy = {
                'analysis_prompt': context['prompt'], 'analysis_prompt_sha256': legacy.sha(context['prompt']),
                'max_output_tokens': context['max_output_tokens'],
                'translation_prompt': context['card_prompt'], 'translation_prompt_version': context['card_version'],
                'translation_prompt_sha256': legacy.sha(context['card_prompt']),
                'analysis_fidelity': context['ANALYSIS_FIDELITY'], 'translation_fidelity': context['TRANSLATION_FIDELITY'],
            }
            packet = {
                'schema': 'dot-url-manifest-v1', 'exporter_version': EXPORTER_VERSION, 'hash_spec': dict(HASH_SPEC),
                'read_only': True, 'intended_executor': dict(contract.PRODUCER),
                'scope_user_id': scope_user_id, 'cutoff': cutoff, 'exported_at': time.time(), 'limit': limit,
                'enabled_feed_ids': sorted(feeds), 'excluded_entry_count': len(excluded),
                'excluded_entry_ids_hash': digest(sorted(excluded)),
                'entry_ids': [a['entry_id'] for a in selected], 'articles': selected, **policy,
                'policy_snapshot_hash': digest(policy),
                'selection': {'status': 'full' if len(selected) == limit else 'short' if selected else 'empty',
                              'selected_count': len(selected), 'scoped_row_count': len(rows),
                              'skip_counts': dict(sorted(skipped.items())),
                              'omitted_card_counts': dict(sorted(omitted_cards.items())),
                              'ordering': 'published_at DESC, entry_id DESC; SQLite NULLs last'},
                'source_mode': 'reference_only; no article body fetched or archived',
                'import_requirement': 'Fresh upstream, prompt/policy, CAS, feed, claims and leases must pass importer preflight; empty manifests are not importable',
            }
            packet['manifest_hash'] = digest(packet)
            return packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Output: dot-url-manifest-v1 with manifest_hash, article snapshot hashes, raw UTF-8 prompt SHA256, '
        'and sorted exclusion-ID hash. Verify identity/scope and hashes before use. Short/empty batches '
        'include reason counts; empty batches are not importable. Then run dot_url_article_import.py '
        '--config CONFIG --manifest MANIFEST --results RESULTS --batch-limit LIMIT without --apply. '
        'See docs/ops/DOT-URL-EXPORT.md for the versioned hash contract and safety boundaries.'))
    parser.add_argument('--config', required=True, help='Existing bridge config; never included in output')
    parser.add_argument('--scope-user-id', type=int, required=True, help='Must match the explicit config scope')
    parser.add_argument('--limit', type=int, required=True, help='Requested batch size, 1 through 12')
    parser.add_argument('--exclude-entry-ids-file', required=True,
                        help='UTF-8 JSON array of unique positive integer IDs; use [] for the first batch')
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        excluded = json.loads(Path(args.exclude_entry_ids_file).read_text(encoding='utf-8'))
        packet = export(config, scope_user_id=args.scope_user_id, limit=args.limit, exclude_entry_ids=excluded)
        print(json.dumps(packet, ensure_ascii=False, allow_nan=False))
    except Exception as exc:  # noqa: BLE001 -- sanitize every CLI failure, including I/O/provider errors
        # Exception text can contain credentials, SQL, paths or private data.
        code = str(exc) if type(exc) is ValueError and str(exc) in SAFE_ERROR_CODES else type(exc).__name__
        print(json.dumps({'read_only': True, 'state': 'blocked', 'error': code}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
