"""Durable prepared article bodies: feed polling must not erase non-AI repairs."""
import hashlib
import json
import time
from copy import deepcopy
from bs4 import BeautifulSoup
import core
from work_admission import check

PRODUCT_LABEL = '产品介绍（Product Hunt）'


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
    if kind == 'adafruit_linked_original':
        return hashlib.sha256((html or '').encode()).hexdigest()
    return text_hash(html)


def remember(entry, content, kind, receipt=None, *,admission=None):
    if kind not in ('product_page', 'body_images_repaired', 'adafruit_linked_original'):
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
    elif row['kind'] == 'adafruit_linked_original':
        # A changed upstream body invalidates the repair rather than silently hiding it.
        if input_hash(raw,row['kind']) != row['input_text_hash']:
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
