"""Durable prepared article bodies: feed polling must not erase non-AI repairs."""
import hashlib
import time
from bs4 import BeautifulSoup
import core

PRODUCT_LABEL = '产品介绍（Product Hunt）'


def migrate():
    with core.connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS prepared_articles (
          entry_id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,url TEXT NOT NULL,
          title TEXT NOT NULL,content TEXT NOT NULL,kind TEXT NOT NULL,
          input_text_hash TEXT NOT NULL,prepared_at REAL NOT NULL)''')


def text_hash(html):
    text = BeautifulSoup(html or '', 'html.parser').get_text(' ', strip=True)
    return hashlib.sha256(' '.join(text.split()).encode()).hexdigest()


def remember(entry, content, kind):
    if kind not in ('product_page', 'body_images_repaired'):
        raise ValueError('unsupported_prepared_content_kind')
    if not content or len(content.encode()) > 2 * 1024 * 1024:
        raise ValueError('prepared_content_invalid_size')
    with core.connect() as db:
        db.execute('''INSERT OR REPLACE INTO prepared_articles VALUES (?,?,?,?,?,?,?,?)''',
                   (entry['id'], entry['user_id'], entry['url'], entry['title'], content,
                    kind, text_hash(entry.get('content', '')), time.time()))


def apply(entry):
    if entry.get('content_deferred') or 'user_id' not in entry:
        return entry
    with core.connect() as db:
        row = db.execute('''SELECT * FROM prepared_articles WHERE entry_id=?
          AND user_id=? AND url=? AND title=?''',
          (entry['id'], entry['user_id'], entry.get('url',''), entry.get('title',''))).fetchone()
    if row is None:
        return entry
    raw = entry.get('content') or ''
    if row['kind'] == 'product_page':
        # Never hide a new longer source article or overwrite already enriched copy.
        if PRODUCT_LABEL in raw or len(BeautifulSoup(raw,'html.parser').get_text(' ',strip=True)) > 800:
            return entry
    else:
        from media_repair import needs_repair
        if not needs_repair(raw) or text_hash(raw) != row['input_text_hash']:
            return entry
    return {**entry, 'content':row['content'], 'prepared_source':row['kind'],
            'prepared_at':row['prepared_at']}
