"""Durable prepared article bodies: feed polling must not erase non-AI repairs."""
import hashlib
import json
import time
import asyncio
import threading
from collections import OrderedDict
from copy import deepcopy
from bs4 import BeautifulSoup
import core
from work_admission import check

PRODUCT_LABEL = '产品介绍（Product Hunt）'
_BODY_CHECKS = OrderedDict()
_BODY_CHECK_LOCK = threading.Lock()
_BODY_INFLIGHT = {}
BODY_CHECK_SECONDS = 12
BODY_CHECK_TTL = 900
BODY_CHECK_LIMIT = 128


def migrate(*,admission=None):
    check(admission)
    with core.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS prepared_articles (
          entry_id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,url TEXT NOT NULL,
          title TEXT NOT NULL,content TEXT NOT NULL,kind TEXT NOT NULL,
          input_text_hash TEXT NOT NULL,prepared_at REAL NOT NULL)''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(prepared_articles)')}
        if 'source_receipt' not in columns:
            db.execute('ALTER TABLE prepared_articles ADD COLUMN source_receipt TEXT')


def text_hash(html):
    text = BeautifulSoup(html or '', 'html.parser').get_text(' ', strip=True)
    return hashlib.sha256(' '.join(text.split()).encode()).hexdigest()


def input_hash(html, kind):
    # Attribution can change without changing its visible label (e.g. "medium.com").
    # For followed originals, any source HTML change must invalidate the saved body.
    if kind in ('adafruit_linked_original', 'reader_original_html'):
        return hashlib.sha256((html or '').encode()).hexdigest()
    return text_hash(html)


def remember(entry, content, kind, receipt=None, *,admission=None):
    if kind not in ('product_page', 'body_images_repaired', 'adafruit_linked_original', 'reader_original_html'):
        raise ValueError('unsupported_prepared_content_kind')
    if not content or len(content.encode()) > 2 * 1024 * 1024:
        raise ValueError('prepared_content_invalid_size')
    check(admission)
    with core.connect() as db:
        db.execute('''INSERT OR REPLACE INTO prepared_articles
                   (entry_id,user_id,url,title,content,kind,input_text_hash,prepared_at,source_receipt)
                   VALUES (?,?,?,?,?,?,?,?,?)''',
                   (entry['id'], entry['user_id'], entry['url'], entry['title'], content,
                    kind, input_hash(entry.get('content', ''),kind), time.time(),
                    json.dumps(receipt) if receipt else None))


def apply(entry, *,admission=None):
    if entry.get('content_deferred') or 'user_id' not in entry:
        return entry
    check(admission)
    with core.connect() as db:
        row = db.execute('''SELECT * FROM prepared_articles WHERE entry_id=?
          AND user_id=? AND url=? AND title=?''',
          (entry['id'], entry['user_id'], entry.get('url',''), entry.get('title',''))).fetchone()
    return _apply_row(entry, row)


def _apply_row(entry, row):
    """Apply an identity-matched row using the same source invalidation rules."""
    if row is None:
        return entry
    raw = entry.get('content') or ''
    if row['kind'] == 'product_page':
        # Never hide a new longer source article or overwrite already enriched copy.
        if PRODUCT_LABEL in raw or len(BeautifulSoup(raw,'html.parser').get_text(' ',strip=True)) > 800:
            return entry
    elif row['kind'] in ('adafruit_linked_original', 'reader_original_html'):
        # A changed upstream body invalidates the repair rather than silently hiding it.
        if input_hash(raw,row['kind']) != row['input_text_hash']:
            return entry
        if row['kind'] == 'reader_original_html':
            from content_quality import BODY_POLICY
            try:
                receipt = json.loads(row['source_receipt'] or '{}')
                if (receipt.get('body_policy') != BODY_POLICY or receipt.get('requested_url') != entry.get('url')
                        or receipt.get('html_sha256') != hashlib.sha256(row['content'].encode()).hexdigest()):
                    return entry
            except (ValueError, TypeError, AttributeError):
                return entry
    else:
        from media_repair import needs_repair
        if not needs_repair(raw) or text_hash(raw) != row['input_text_hash']:
            return entry
    return {**entry, 'content':row['content'], 'prepared_source':row['kind'],
            'prepared_at':row['prepared_at'],
            **({'content_source_url':json.loads(row['source_receipt']).get('url'),
                'fulltext_receipt':json.loads(row['source_receipt'])} if row['source_receipt'] else {})}


class PreparedBatch:
    """Request-local results only; never a cache of article bodies across requests."""

    def __init__(self):
        self._resolved = {}

    @staticmethod
    def _identity(entry):
        # Also preserve unrelated current fields when a caller changes an entry
        # during this request. Values are references, not copies of full HTML.
        return dict(entry)

    def remember(self, entry, prepared):
        # Keep a reference to the exact native object, preventing object-id reuse.
        overlay = ({key: prepared[key] for key in (
            'content', 'prepared_source', 'prepared_at')
            if key in prepared} if prepared is not entry else None)
        if (overlay is not None and 'fulltext_receipt' in prepared
                and prepared['fulltext_receipt'] is not entry.get('fulltext_receipt')):
            # A source receipt decoded from the prepared row owns these fields.
            # Inherited native receipt fields must instead remain current.
            overlay['content_source_url'] = prepared.get('content_source_url')
            overlay['fulltext_receipt'] = deepcopy(prepared['fulltext_receipt'])
        self._resolved[id(entry)] = (entry, self._identity(entry), overlay)

    def apply(self, entry, *, admission=None):
        cached = self._resolved.get(id(entry))
        if cached is None or cached[0] is not entry or cached[1] != self._identity(entry):
            return apply(entry, admission=admission)
        if not entry.get('content_deferred') and 'user_id' in entry:
            check(admission)
        if cached[2] is None:
            return entry
        overlay = dict(cached[2])
        if 'fulltext_receipt' in overlay:
            # One caller cannot mutate a later duplicate entry's receipt.
            overlay['fulltext_receipt'] = deepcopy(overlay['fulltext_receipt'])
        return {**entry, **overlay}


def prepare_many(entries, *, admission=None):
    """Read each user's prepared rows in bounded batches, then validate once."""
    entries = list(entries)
    eligible = [entry for entry in entries
                if not entry.get('content_deferred') and 'user_id' in entry
                and type(entry.get('id')) is int and type(entry['user_id']) is int]
    rows = {}
    if eligible:
        check(admission)
        with core.connect() as db:
            for user_id, ids in core.reader_id_batches(eligible):
                check(admission)
                rows.update(((row['user_id'], row['entry_id']), dict(row)) for row in db.execute(
                    'SELECT * FROM prepared_articles WHERE user_id=? AND entry_id IN ('
                    + ','.join('?' for _ in ids) + ')', (user_id, *ids)))
    batch = PreparedBatch()
    for entry in entries:
        if (entry.get('content_deferred') or 'user_id' not in entry
                or type(entry.get('id')) is not int or type(entry['user_id']) is not int):
            prepared = apply(entry, admission=admission)
        else:
            check(admission)
            row = rows.get((entry['user_id'], entry['id']))
            if row is not None and (row['url'] != entry.get('url', '')
                                    or row['title'] != entry.get('title', '')):
                row = None
            prepared = _apply_row(entry, row)
        batch.remember(entry, prepared)
    return batch


def body_check_key(entry):
    return (str(core.DB), entry['user_id'], entry['id'], entry.get('url'), entry.get('title'),
            input_hash(entry.get('content') or '', 'reader_original_html'))


def body_check_status(entry):
    key = body_check_key(entry)
    with _BODY_CHECK_LOCK:
        cached = _BODY_CHECKS.get(key)
        if cached and cached[0] > time.time():
            _BODY_CHECKS.move_to_end(key)
            return dict(cached[1])
        _BODY_CHECKS.pop(key, None)
    return None


def _remember_body_check(entry, result):
    with _BODY_CHECK_LOCK:
        _BODY_CHECKS[body_check_key(entry)] = (time.time() + BODY_CHECK_TTL, dict(result))
        _BODY_CHECKS.move_to_end(body_check_key(entry))
        while len(_BODY_CHECKS) > BODY_CHECK_LIMIT:
            _BODY_CHECKS.popitem(last=False)
    return result


def _reader_original_html(result):
    """Keep source structure while using the existing source-only HTML allowlist."""
    from urllib.parse import urljoin
    from bilingual_translation import BodyParser, _html
    source = result.get('html')
    if not isinstance(source, str) or not source.strip() or len(source.encode()) > 2 * 1024 * 1024:
        raise ValueError('invalid_original_body')
    base = result['receipt']['url']
    soup = BeautifulSoup(source, 'html.parser')
    for node in soup.find_all(True):
        for attr in ('href', 'src'):
            value = node.get(attr)
            if isinstance(value, str) and not value.startswith('#'):
                node[attr] = urljoin(base, value)
        if node.has_attr('srcset'):
            parts = []
            for part in str(node['srcset']).split(','):
                words = part.strip().split()
                if words:
                    parts.append(' '.join([urljoin(base, words[0]), *words[1:]]))
            node['srcset'] = ', '.join(parts)
    return _html(BodyParser(str(soup)).root)


async def verify_original_body(entry, current_entry, *, analysis_text=None, previous_translation=None):
    """One explicit detail activation; no model, feed update or analysis mutation."""
    from content_quality import BODY_POLICY, body_completeness
    cached = await asyncio.to_thread(apply, entry)
    if body_completeness(cached)['status'] == 'verified':
        return {'status': 'verified', 'reason': 'original_container_verified',
                'checked_at': cached['fulltext_receipt']['checked_at']}
    previous = body_check_status(entry)
    if previous:
        return previous
    key = body_check_key(entry)

    async def verify():
        from kaggle_batch.fulltext_source import rule_for, fetch, FulltextUnavailable
        now = time.time()
        failed = {'status': 'unverified', 'reason': 'original_check_failed', 'checked_at': now}
        try:
            selector, _, _ = rule_for(entry['url'])
            # This narrow repair uses reviewed original HTML only; no browser,
            # alternate reader service, login or paywall workaround is started.
            if selector.startswith('@'):
                return _remember_body_check(entry, {**failed, 'reason': 'original_check_unsupported'})
            result = await asyncio.wait_for(fetch(entry['url'], direct_on_connect_error=True), BODY_CHECK_SECONDS)
            quality = result.get('content_quality') or {}
            if (quality.get('access') in {'paid_fulltext', 'paid_subscription', 'login_required'}
                    or any(reason in {'publisher_nonfree_pending_review', 'conflicting_access_evidence'}
                           for reason in quality.get('reason_codes', []))):
                return _remember_body_check(entry, {**failed, 'reason': 'original_access_restricted'})
            receipt = result.get('receipt') or {}
            if receipt.get('requested_url') != entry['url'] or not receipt.get('page_sha256'):
                raise ValueError('original_receipt_identity_missing')
            final_selector, _, _ = rule_for(receipt.get('url', ''))
            if (final_selector.startswith('@') or receipt.get('selector') != final_selector
                    or core.canonical_url(receipt['url']).rstrip('/') != core.canonical_url(entry['url']).rstrip('/')):
                raise ValueError('original_article_identity_changed')
            html = await asyncio.to_thread(_reader_original_html, result)
            if len(html.encode()) > 2 * 1024 * 1024:
                raise ValueError('original_body_too_large')
        except (FulltextUnavailable, asyncio.TimeoutError, ValueError, TypeError, KeyError):
            return _remember_body_check(entry, failed)
        # Re-authenticate/re-read through the caller before saving. A revoked
        # account or changed native body cannot bind this receipt to a new item.
        fresh = await current_entry()
        if body_check_key(fresh) != key:
            return _remember_body_check(entry, {**failed, 'reason': 'original_input_changed'})
        normalize = lambda text: ' '.join((text or '').split())
        receipt = {**receipt, 'body_policy': BODY_POLICY, 'checked_at': now,
                   'html_sha256': hashlib.sha256(html.encode()).hexdigest(),
                   'analysis_text_matches': (normalize(analysis_text) == normalize(result.get('source_text')))
                       if analysis_text is not None else None}
        if previous_translation and previous_translation.get('blocks_done', 0) > 0:
            receipt['previous_translation_source_hash'] = previous_translation.get('source_hash')
        await asyncio.to_thread(remember, fresh, html, 'reader_original_html', receipt)
        return _remember_body_check(fresh, {'status': 'verified', 'reason': 'original_container_verified', 'checked_at': now})

    task = _BODY_INFLIGHT.get(key)
    if task is None:
        task = asyncio.create_task(verify())
        _BODY_INFLIGHT[key] = task
        def finished(done):
            _BODY_INFLIGHT.pop(key, None)
            if not done.cancelled():
                done.exception()  # Retrieve failures even after a detail was closed.
        task.add_done_callback(finished)
    return await asyncio.shield(task)
