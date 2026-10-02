"""Recover lazy-loaded body images from the same original page, not other articles."""
from work_admission import check,options,http_client
import os
from collections import defaultdict
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from content_input import _safe_image_url

def placeholder(src):
    return not src or str(src).startswith('data:image/svg+xml,') or str(src).startswith('data:image/gif;base64,R0lGODlhAQAB')

def needs_repair(html):
    return any(placeholder(im.get('src')) for im in BeautifulSoup(html or '', 'html.parser').find_all('img'))

def normalized_alt(img):
    return ' '.join(str(img.get('alt') or '').split())

def repair_html(extracted, raw, base):
    page = BeautifulSoup(raw, 'html.parser')
    body = BeautifulSoup(extracted, 'html.parser')
    by_alt = defaultdict(set)
    for im in page.find_all('img'):
        alt = normalized_alt(im)
        src = _safe_image_url(im.get('data-src') or im.get('data-lazy-src') or im.get('src'), base)
        if len(alt) >= 12 and src:
            by_alt[alt].add(src)
    changed = 0
    for im in body.find_all('img'):
        if not placeholder(im.get('src')):
            continue
        own = _safe_image_url(im.get('data-src') or im.get('data-lazy-src'), base)
        matches = by_alt.get(normalized_alt(im), set())
        chosen = own or (next(iter(matches)) if len(matches) == 1 else None)
        if not chosen:
            continue
        im['src'] = chosen
        # Do not retain placeholder/invalid srcsets; the source image remains full-size.
        for key in ('srcset','data-srcset','data-src','data-lazy-src'):
            im.attrs.pop(key, None)
        im['loading'] = 'lazy'
        changed += 1
    return (str(body) if changed else extracted), changed

async def fetch_original(url, *,admission=None):
    check(admission)
    if not _safe_image_url(url):
        raise ValueError('invalid_original_url')
    async with http_client(admission,proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY') or None,
                                 timeout=15, trust_env=False, follow_redirects=True, max_redirects=3) as c:
        async with c.stream('GET', url) as r:
            r.raise_for_status()
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data.extend(chunk)
                if len(data) > 3 * 1024 * 1024:
                    raise ValueError('original_page_too_large')
            return bytes(data), str(r.url)

async def repair_entry(client, entry, backend_url, headers, raw=None, *,admission=None):
    check(admission)
    original = entry.get('content') or ''
    if not needs_repair(original):
        return {'content': original, 'repaired': 0}
    fetched, base = (raw, entry['url']) if raw is not None else await fetch_original(entry['url'],**options(admission))
    html, count = repair_html(original, fetched, base)
    if not count:
        return {'content': original, 'repaired': 0}
    # Keep a private source snapshot; restoring images must never discard original text.
    from core import ROOT
    import json, hashlib
    check(admission)
    folder = ROOT / '.private/media-before'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot = folder / (str(entry['id'])+'-'+hashlib.sha256(original.encode()).hexdigest()[:16]+'.json')
    if not snapshot.exists():
        snapshot.write_text(json.dumps({'id': entry['id'], 'content': original},ensure_ascii=False))
        snapshot.chmod(0o600)
    check(admission)
    r = await client.put(backend_url+f"/v1/entries/{entry['id']}", headers=headers, json={'content': html}, timeout=15)
    r.raise_for_status()
    check(admission)
    r = await client.get(backend_url+f"/v1/entries/{entry['id']}", headers=headers, timeout=15)
    r.raise_for_status()
    content = r.json().get('content') or html
    from prepared_content import remember
    remember(entry, content, 'body_images_repaired',**options(admission))
    return {'content': content, 'repaired': count}
