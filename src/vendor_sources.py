"""Bounded Python replacements for the twelve former n8n RSS adapters.

Read endpoints serve persisted snapshots only. Refresh/import use a per-source
flock and atomic rename; a bad upstream response never replaces good entries.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
import httpx
from bs4 import BeautifulSoup

ROOT = Path(os.environ.get("AI_NEWS_ROOT", "/home/ubuntu/ai-news"))
CONFIGS = {c['key']: c for c in json.loads(Path(__file__).with_name('vendor_sources.json').read_text())}
INTERVAL = 6 * 3600
RETRY = 30 * 60
MAX_BYTES = 8 * 1024 * 1024


def folder() -> Path:
    p = ROOT / 'state/vendor-adapters'
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def config(key: str) -> dict:
    if key not in CONFIGS:
        raise KeyError('unknown source')
    return CONFIGS[key]


def load(key: str, *, read_only=False) -> dict:
    config(key)
    base = ROOT / 'state/vendor-adapters' if read_only else folder()
    path = base / (key + '.json')
    if not path.exists():
        return {'entries': [], 'seen': {}, 'seeded': False}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or not isinstance(value.get('entries'), list) or not isinstance(value.get('seen'), dict):
        raise ValueError('invalid source state')
    for entry in value['entries']:
        if not isinstance(entry, dict) or not isinstance(entry.get('title'), str) or not entry.get('title'):
            raise ValueError('invalid stored article')
        if allowed_url(CONFIGS[key], entry.get('url') or '') != entry.get('url') or not parsed_date(entry.get('published') or entry.get('discovered')):
            raise ValueError('invalid stored article URL/date')
    return value


def save(key: str, value: dict) -> None:
    config(key)
    fd, name = tempfile.mkstemp(prefix=key + '-', suffix='.tmp', dir=folder())
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, folder() / (key + '.json'))
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked(key: str):
    config(key)
    with (folder() / (key + '.lock')).open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def iso(value=None) -> str:
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def parsed_date(value) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    candidates = [lambda: datetime.fromisoformat(value.replace('Z', '+00:00')), lambda: parsedate_to_datetime(value)]
    candidates += [lambda f=f: datetime.strptime(value, f) for f in ('%d %b %Y', '%B %d, %Y', '%b %d, %Y', '%m/%d/%Y')]
    for parse in candidates:
        try:
            dt = parse()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return iso(dt.timestamp())
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def allowed_url(cfg: dict, value: str) -> str | None:
    url = urljoin(cfg['url'], str(value))
    part, origin = urlsplit(url), urlsplit(cfg['origin'])
    if (part.scheme, part.netloc) != (origin.scheme, origin.netloc) or part.username:
        return None
    if not part.path.startswith(cfg['prefix']) or part.path == cfg['prefix']:
        return None
    if cfg['key'] == 'kickstarter' and not re.fullmatch(r'/brands/kickstarter/[a-z0-9-]+', part.path):
        return None
    return urlunsplit((part.scheme, part.netloc, part.path, '', ''))


def fetch_text(url: str) -> str:
    # URLs are fixed configuration or validated links, never caller-provided URLs.
    hosts = {urlsplit(c['origin']).hostname for c in CONFIGS.values()} | {'r.jina.ai'}
    proxy = os.environ.get('VENDOR_OUTBOUND_PROXY', 'http://127.0.0.1:17890') or None
    cfg = next((c for c in CONFIGS.values() if c.get('fetch_url', c['url']) == url), None)
    if cfg is None:
        cfg = next((c for c in CONFIGS.values() if urlsplit(c['origin']).hostname == urlsplit(url).hostname), {})
    headers = {'User-Agent': 'PersonalInboxSourceAdapter/1.0', 'Accept': 'text/html,application/json,text/plain', **cfg.get('http_headers', {})}
    with httpx.Client(timeout=httpx.Timeout(cfg.get('timeout_seconds', 30), connect=10), proxy=proxy, trust_env=False,
                      follow_redirects=False, headers=headers) as client:
        for _ in range(5):
            p = urlsplit(url)
            if p.scheme != 'https' or p.hostname not in hosts or p.port not in (None, 443) or p.username:
                raise ValueError('unapproved upstream URL')
            with client.stream('GET', url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('upstream response too large')
                    chunks.append(chunk)
                return b''.join(chunks).decode('utf-8', errors='replace')
    raise ValueError('too many redirects')


def parse_list(cfg: dict, text: str) -> list[dict]:
    if not text or re.search(r'Warning: Target URL returned error|Title: Just a moment', text):
        raise ValueError('blocked or empty source')
    rows = []
    if cfg.get('json'):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError('expected source array')
        rows = [{'title': x.get('headline') or x.get('name'), 'url': x.get('path'), 'published': x.get('date')} for x in data if isinstance(x, dict) and x.get('path')]
    elif cfg.get('reader'):
        if cfg['key'] == 'nordic':
            pattern = r'\bCustomer\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+([^\n]+?)\]\((https://www\.nordicsemi\.com/Nordic-news/\d{4}/\d{2}/[^)\s]+)\)'
            rows = [{'published': m[0], 'title': m[1], 'url': m[2]} for m in re.findall(pattern, text)]
        else:
            pattern = r'^### ([^\n]+)\s*\n\s*\[Learn More\]\((https://www\.microchip\.com/en-us/(?:tools-resources/reference-designs|solutions/technologies/motor-control-and-drive/applications-and-reference-designs)/[^)\s]+)\)'
            rows = [{'title': m[0], 'url': m[1]} for m in re.findall(pattern, text, re.M)]
    else:
        soup = BeautifulSoup(text, 'html.parser')
        values = {}
        for field in cfg['extract']:
            selected = soup.select(field['cssSelector'])
            values[field['key']] = [n.get(field.get('attribute', 'href'), '') if field['returnValue'] == 'attribute' else n.get_text(' ', strip=True) for n in selected]
        titles, links = values.get('titles', []), values.get('links', [])
        dates = values.get('dates', [])
        if len(titles) != len(links):
            raise ValueError('unaligned source titles and URLs')
        rows = [{'title': title, 'url': link, 'published': dates[i] if i < len(dates) else None} for i, (title, link) in enumerate(zip(titles, links))]
    valid = []
    for row in rows:
        url = allowed_url(cfg, row.get('url') or '')
        title = re.sub(r'\s+', ' ', str(row.get('title') or '')).strip()
        if cfg.get('relative') and not re.fullmatch(r'eval-cn[0-9]+-[a-z0-9]+/', str(row.get('url', ''))):
            continue
        if url and title:
            valid.append({'url': url, 'title': title, 'published': parsed_date(row.get('published'))})
    if not valid:
        raise ValueError('no valid allowed articles; previous feed retained')
    return valid


def newsletter_rows(cfg: dict, text: str, state: dict, fetch) -> list[dict]:
    soup = BeautifulSoup(text, 'html.parser')
    links = list(dict.fromkeys(u for a in soup.select('a[href]') if (u := allowed_url(cfg, a.get('href', '')))))
    if not links:
        raise ValueError('no newsletter links')
    pending = [u for u in links if u not in state['seen']]
    rows = []
    for url in (pending[:20] or links[:1]):
        page = BeautifulSoup(fetch(url), 'html.parser')
        title, body, canonical, date = page.select_one('h1'), page.select_one('table.nl-container'), page.select_one('link[rel=canonical]'), page.select_one('h1 + div > span:first-child')
        canonical_url = allowed_url(cfg, canonical.get('href', '')) if canonical else None
        if not title or not body or len(body.get_text()) < 200 or canonical_url != url:
            raise ValueError('invalid newsletter; previous feed retained')
        text_body = re.sub(r'[\u034f\u200b-\u200d\ufeff]', '', body.get_text(' ', strip=True))
        rows.append({'url': url, 'title': title.get_text(' ', strip=True), 'body': re.sub(r'\s+', ' ', text_body).strip(),
                     'dateLabel': date.get_text(' ', strip=True) if date else '归档未提供可确认的时间',
                     'selected': bool(re.search(r'EXPLORE DESIGN\s*&\s*TECH', text_body, re.I))})
    return rows


def refresh(key: str, *, force=False, fetch=fetch_text, now=None) -> dict:
    cfg = config(key)
    now = time.time() if now is None else now
    try:
        with locked(key):
            state = load(key)
            if not force and now < state.get('next_run_at', 0):
                return {'key': key, 'state': 'not_due'}
            try:
                text = fetch(cfg.get('fetch_url', cfg['url']))
                rows = newsletter_rows(cfg, text, state, fetch) if cfg.get('kind') == 'newsletter' else parse_list(cfg, text)
                pending, seen = [], dict(state['seen'])
                for i, row in enumerate(rows):
                    if row['url'] in seen:
                        continue
                    seen[row['url']] = iso(now)
                    if row.get('selected') is False or (not state['seeded'] and (cfg.get('baseline_only') or i >= 20)):
                        continue
                    pending.append({k: v for k, v in {**row, 'discovered': iso(now)}.items() if k != 'selected'})
                if cfg.get('kind') == 'newsletter':
                    pending.reverse()
                state.update(entries=(pending + state['entries'])[:200], seen=seen, seeded=True,
                             checkedAt=iso(now), last_success_at=now, last_attempt_at=now,
                             next_run_at=now + INTERVAL, last_error=None, last_added=len(pending))
                save(key, state)
                return {'key': key, 'state': 'ok', 'found': len(rows), 'added': len(pending), 'stored': len(state['entries'])}
            except Exception as exc:
                state.update(last_attempt_at=now, next_run_at=now + RETRY, last_error={'type': type(exc).__name__, 'at': now,
                             'http_status': exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None})
                save(key, state)
                return {'key': key, 'state': 'error', 'error_type': type(exc).__name__, 'retained': len(state['entries'])}
    except BlockingIOError:
        return {'key': key, 'state': 'busy'}
    except (ValueError, OSError, TypeError) as exc:
        # A corrupt existing state cannot be overwritten as an empty feed.
        return {'key': key, 'state': 'error', 'error_type': type(exc).__name__, 'state_preserved': True}


def import_n8n(database: Path) -> list[dict]:
    reports = []
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        for key, cfg in CONFIGS.items():
            row = db.execute('SELECT staticData FROM workflow_entity WHERE id=?', (cfg['workflow_id'],)).fetchone()
            if not row:
                raise ValueError('missing legacy workflow: ' + key)
            data = json.loads(row[0] or '{}').get('global', {})
            if not data.get('seeded') or not isinstance(data.get('entries'), list) or not isinstance(data.get('seen'), dict):
                raise ValueError('unseeded legacy workflow: ' + key)
            for entry in data['entries']:
                if allowed_url(cfg, entry.get('url', '')) != entry.get('url') or not entry.get('title') or not parsed_date(entry.get('published') or entry.get('discovered')):
                    raise ValueError('invalid legacy article: ' + key)
            with locked(key):
                current = load(key)
                if current.get('seeded'):
                    reports.append({'key': key, 'state': 'already_imported', 'entries': len(current['entries'])})
                    continue
                save(key, {**data, 'imported_from': 'n8n', 'imported_at': time.time(),
                           'last_success_at': datetime.fromisoformat(data['checkedAt'].replace('Z', '+00:00')).timestamp(),
                           'next_run_at': 0, 'last_error': None})
                reports.append({'key': key, 'state': 'imported', 'entries': len(data['entries'])})
    return reports


def render(key: str) -> bytes:
    cfg, state = config(key), load(key)
    if not state['seeded']:
        raise ValueError('no successful collection yet')
    root = ET.Element('rss', version='2.0')
    channel = ET.SubElement(root, 'channel')
    ET.SubElement(channel, 'title').text = cfg['name']
    ET.SubElement(channel, 'link').text = cfg.get('site', cfg['url'])
    newsletter = cfg.get('kind') == 'newsletter'
    ET.SubElement(channel, 'description').text = ('Kickstarter 精选邮件，经第三方 BrandsNinja 公开归档获取；不是 Kickstarter 官方 RSS。项目按钮原始链接在归档中缺失。' if newsletter else '官方栏目更新；由 Python 定时采集，按原文 URL 去重。正文需从原文获取。')
    entries = state['entries'] if newsletter else sorted(state['entries'], key=lambda e: e.get('published') or e['discovered'], reverse=True)
    for entry in entries:
        item = ET.SubElement(channel, 'item')
        ET.SubElement(item, 'title').text = entry['title']
        ET.SubElement(item, 'link').text = entry['url']
        ET.SubElement(item, 'guid', isPermaLink='true').text = entry['url']
        dt = datetime.fromisoformat((entry.get('published') or entry['discovered']).replace('Z', '+00:00'))
        ET.SubElement(item, 'pubDate').text = format_datetime(dt.astimezone(timezone.utc), usegmt=True)
        if newsletter:
            note = '来源：Kickstarter 邮件的 BrandsNinja 第三方归档。归档显示时间：' + entry.get('dateLabel', '') + '。RSS 时间为首次采集时间。以下是精选邮件文字，不是每个项目的完整正文；归档未保留项目直达链接。\n\n'
            readable = re.sub(r'\[https?://[^\]]+\]', '', entry.get('body', ''))
            readable = re.sub(r'https://www\.kickstarter\.com/?', ' ', readable)
            description = note + re.sub(r'\s+', ' ', readable).strip()
        else:
            note = '官方栏目提供的发布日期。' if entry.get('published') else '此时间为首次发现时间，原文发布日期未确认。'
            description = note + ' 原文：' + entry['url']
        ET.SubElement(item, 'description').text = description
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def status(*, read_only=False) -> dict:
    result = {}
    for key in CONFIGS:
        try:
            state = load(key, read_only=read_only)
            result[key] = {'name': CONFIGS[key]['name'], 'entries': len(state['entries']), 'seeded': state['seeded'],
                           **{k: state.get(k) for k in ('last_success_at', 'last_attempt_at', 'next_run_at', 'last_error', 'last_added')}}
        except (ValueError, OSError, TypeError, KeyError):
            result[key] = {'state': 'invalid_state'}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import-n8n', type=Path)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--source', choices=CONFIGS)
    args = parser.parse_args()
    if args.import_n8n:
        output = import_n8n(args.import_n8n)
    elif args.refresh:
        with ThreadPoolExecutor(max_workers=2) as pool:
            output = list(pool.map(lambda k: refresh(k, force=args.force), [args.source] if args.source else CONFIGS))
    else:
        output = status()
    print(json.dumps(output, ensure_ascii=False), flush=True)
    return int(isinstance(output, list) and any(x.get('state') == 'error' for x in output))


if __name__ == '__main__':
    raise SystemExit(main())
