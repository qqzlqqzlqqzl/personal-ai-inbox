"""Bounded synthetic HTTP origin; no forwarding, credentials, disk cache or product imports."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
import zlib
from reader_loading_transport import IDENTITY, profile_identity, static_representation

TOKEN = 'synthetic-reader-performance-token'
MAX_BUILD_BYTES = 32 * 1024 * 1024
MAX_REQUESTS = 1200
SCENARIO = {'schema': 1, 'entries': 72, 'page_size': 24, 'body_images': 6,
            'list_delay_ms': 150, 'detail_delay_ms': 200, 'image_delay_ms': 150,
            'image_cache_seconds': 3600, 'api_cache': 'no-store'}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
       "media-src 'self'; object-src 'none'; frame-src 'none'; worker-src 'none'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def require(value, message):
    if not value:
        raise ValueError(message)


def checked_directory(value):
    path = Path(os.path.abspath(value))
    for component in [*reversed(path.parents), path]:
        info = component.lstat()
        require(stat.S_ISDIR(info.st_mode) and not component.is_symlink(), 'directory or ancestor is not a real directory')
    return path


def bound_read(root, relative, limit=MAX_BUILD_BYTES):
    """Every component is opened relative to the already bound parent, without following links."""
    parts = PurePosixPath(relative).parts
    require(parts and not relative.startswith('/') and all(p not in ('', '.', '..') for p in parts), 'unsafe relative path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(os.path.abspath(root)).parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        leaf = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(leaf)
            require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'invalid or excessive input file')
            with os.fdopen(leaf, 'rb', closefd=False) as stream:
                data = stream.read(limit + 1)
            after = os.fstat(leaf)
            require(len(data) <= limit and (before.st_size, before.st_mtime_ns, before.st_ino) ==
                    (after.st_size, after.st_mtime_ns, after.st_ino), 'input changed while reading')
            return data
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def admit_build(build, manifest, manifest_sha):
    root = checked_directory(build)
    manifest = Path(os.path.abspath(manifest))
    raw = bound_read(manifest.parent, manifest.name, 1024 * 1024)
    require(hashlib.sha256(raw).hexdigest() == manifest_sha, 'manifest digest mismatch')
    rows = json.loads(raw)
    require(isinstance(rows, list) and 1 <= len(rows) <= 256, 'invalid manifest count')
    files = {}
    for row in rows:
        require(set(row) == {'path', 'bytes', 'sha256'}, 'unexpected manifest fields')
        name = row['path']
        require(isinstance(name, str) and name not in files, 'duplicate manifest path')
        require(str(PurePosixPath(name)) == name and '\\' not in name, 'noncanonical manifest path')
        data = bound_read(root, name)
        require(len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256'], 'build member mismatch: ' + name)
        files[name] = data
        require(sum(map(len, files.values())) <= MAX_BUILD_BYTES, 'build byte cap exceeded')
    actual = set()
    for p in root.rglob('*'):
        require(not p.is_symlink(), 'build contains symlink')
        require(p.is_file() or p.is_dir(), 'build contains special file')
        if p.is_file(): actual.add(p.relative_to(root).as_posix())
    require(actual == set(files) and 'index.html' in files, 'build file set mismatch')
    return files, {'manifest_sha256': manifest_sha, 'files': len(files), 'bytes': sum(map(len, files.values())),
                   'read_only_in_memory': True, 'build_path': str(root)}


def png(seed):
    width, height = 960, 640
    rows = b''.join(b'\0' + bytes(((seed * 43 + y // 20) % 256, 105, 180)) * width for y in range(height))
    def chunk(kind, body):
        return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


class Fixture:
    def __init__(self, build_files, *, failures=False, transport_profile=IDENTITY):
        self.files = build_files
        self.transport_profile = profile_identity(transport_profile)
        self.failures = failures
        self.records = []
        self.lock = threading.Lock()
        self.failed_images = set()
        self.images = {str(i): png(i) for i in range(1, 7)}
        self.deadline = time.monotonic() + 600
        outer = self
        class Server(ThreadingHTTPServer):
            daemon_threads = False
            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(5)
                return connection, address
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_GET(self): self.dispatch()
            def do_HEAD(self): self.dispatch()
            def do_POST(self): self.dispatch()
            def do_PUT(self): self.dispatch()
            def do_CONNECT(self): self.respond(403, b'no forwarding', 'text/plain', 'blocked-connect')
            def do_DELETE(self): self.respond(405, b'unsupported', 'text/plain', 'blocked-method')
            def respond(self, status, body, media, label, cache='no-store', etag=None, started=None, representation=None):
                at = time.monotonic()
                with outer.lock:
                    if len(outer.records) <= MAX_REQUESTS:
                        outer.records.append({'seq': len(outer.records)+1, 'at': at,
                            'elapsed_ms': (at - (started or at))*1000, 'method': self.command,
                            'label': label, 'status': status, 'bytes': len(body),
                            'conditional': bool(self.headers.get('If-None-Match')), 'cache_control': cache,
                            **({'transport':{k:v for k,v in representation.items() if k!='body'}} if representation else {})})
                self.send_response(status)
                self.send_header('Content-Type', media)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', cache)
                self.send_header('Content-Security-Policy', CSP)
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                if etag: self.send_header('ETag', etag)
                if representation and representation['vary']: self.send_header('Vary','Accept-Encoding')
                if representation and representation['encoding']: self.send_header('Content-Encoding',representation['encoding'])
                self.end_headers()
                if self.command != 'HEAD':
                    try: self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError): pass
            def respond_static(self,body,media,label,cache,started):
                values=self.headers.get_all('Accept-Encoding')
                header=None if values is None else ','.join(values)
                representation=static_representation(body,media,header,outer.transport_profile['name'])
                representation.update(accept_encoding=header[:2048] if header is not None else None,
                                      accept_encoding_truncated=header is not None and len(header)>2048)
                status=representation['status']
                return self.respond(status,representation['body'],media if status==200 else 'text/plain',
                    label if status==200 else 'transport-negotiation',cache if status==200 else 'no-store',
                    started=started,representation=representation)
            def dispatch(self):
                started = time.monotonic()
                if len(outer.records) >= MAX_REQUESTS or started > outer.deadline:
                    return self.respond(429, b'fixture budget exhausted', 'text/plain', 'budget')
                parsed = urlsplit(self.path)
                if (parsed.scheme and (parsed.scheme != 'http' or parsed.netloc != outer.authority)) or self.headers.get('Host') != outer.authority:
                    return self.respond(403, b'only this fixture origin is permitted', 'text/plain', 'blocked-origin')
                if self.headers.get('Upgrade') or self.headers.get('Transfer-Encoding'):
                    self.close_connection = True
                    return self.respond(400, b'unsupported transfer', 'text/plain', 'blocked-transfer')
                path = unquote(parsed.path)
                if '%' in path or '\\' in path or '..' in path.split('/') or '//' in path:
                    return self.respond(400, b'unsafe path', 'text/plain', 'blocked-path')
                query = parse_qs(parsed.query, keep_blank_values=True)
                if any(len(values) != 1 for values in query.values()):
                    return self.respond(400, b'query values must be singular', 'text/plain', 'bad-input')
                if path.startswith('/mf/'):
                    if self.headers.get('X-Auth-Token') != TOKEN:
                        return self.respond(401, b'{"error_message":"fixture authentication required"}', 'application/json', 'auth')
                    try:
                        size = int(self.headers.get('Content-Length', '0'))
                        require(0 <= size <= 4096, 'body too large')
                        body = json.loads(self.rfile.read(size)) if size else None
                        status, value, label, delay = outer.api(path, self.command, query, body)
                    except (ValueError, TypeError, json.JSONDecodeError):
                        status, value, label, delay = 400, {'error_message': 'invalid fixture input'}, 'bad-input', 0
                    time.sleep(delay / 1000)
                    data = b'' if status == 204 else json.dumps(value, ensure_ascii=False).encode()
                    return self.respond(status, data, 'application/json', label, started=started)
                match = re.fullmatch(r'/fixture-images/([1-6])\.png', path)
                if match and self.command in ('GET', 'HEAD'):
                    key = match[1]
                    with outer.lock:
                        fail = outer.failures and key == '3' and key not in outer.failed_images
                        if fail: outer.failed_images.add(key)
                    time.sleep(SCENARIO['image_delay_ms']/1000)
                    if fail: return self.respond(503, b'synthetic image failure', 'text/plain', 'image-' + key, started=started)
                    body = outer.images[key]
                    etag = '"'+hashlib.sha256(body).hexdigest()+'"'
                    cached = self.headers.get('If-None-Match') == etag
                    return self.respond(304 if cached else 200, b'' if cached else body, 'image/png', 'image-' + key,
                                        'public, max-age=3600', etag, started)
                if self.command not in ('GET', 'HEAD'):
                    return self.respond(405, b'unsupported method', 'text/plain', 'blocked-method')
                relative = path.removeprefix('/inbox/')
                if relative in self.server.fixture.files and path.startswith('/inbox/'):
                    import mimetypes
                    content = outer.files[relative]
                    media = mimetypes.guess_type(relative)[0] or 'application/octet-stream'
                    cache = 'public, max-age=31536000, immutable' if relative.startswith('assets/') else 'no-store'
                    return self.respond_static(content, media, 'asset:' + relative, cache, started)
                if re.fullmatch(r'/inbox(?:/(?:all|today|starred)(?:/\d+)?)?/?', path):
                    return self.respond_static(outer.files['index.html'], 'text/html; charset=utf-8', 'document', 'no-store', started)
                return self.respond(404, b'unknown fixture resource', 'text/plain', 'unknown')
        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.fixture = self
        self.authority = f'127.0.0.1:{self.server.server_port}'
        self.base = 'http://' + self.authority
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)

    def entry(self, number, deferred=False):
        paragraphs = '<p>' + ('合成性能正文，只用于测量阅读时序与滚动。' * 12) + '</p>'
        images = ''.join(paragraphs * (1 if i < 3 else 3) + f'<img src="/fixture-images/{i}.png" width="960" height="640" alt="合成图片 {i}">' for i in range(1, 7))
        feed = {'id': 7, 'user_id': 1, 'title': '合成性能源', 'feed_url': self.base+'/fixture-feed', 'site_url': self.base,
                'category': {'id': 1, 'title': '合成分类'}, 'icon': {'feed_id': 7, 'icon_id': 0}}
        return {'id': number, 'user_id': 1, 'feed_id': 7, 'title': f'性能样本 {number:03d}', 'url': self.base+'/inbox/all/'+str(number),
                'comments_url': '', 'author': 'Synthetic', 'content': '' if deferred else paragraphs + images,
                'content_deferred': deferred, 'hash': str(number), 'published_at': '2026-10-04T01:00:00Z',
                'created_at': '2026-10-04T01:00:00Z', 'changed_at': '2026-10-04T01:00:00Z', 'status': 'read',
                'starred': False, 'reading_time': 4, 'enclosures': [], 'feed': feed,
                'ai': {'state': 'done', 'score': 8, 'technical_score': 8, 'business_score': 7,
                       'summary': '本地合成摘要', 'tags': ['合成'], 'reason': '性能样本', 'has_note': False}}

    def api(self, path, method, query, body):
        if method == 'GET':
            if path in ('/mf/version', '/mf/v1/version'): return 200, {'version': '2.3.3'}, 'synthetic-version', 0
            if path == '/mf/v1/me': return 200, {'id': 1, 'username': 'synthetic-fixture', 'is_admin': False}, 'me', 0
            if path == '/mf/v1/integrations/status':
                require(not query and body is None, 'unexpected integration status input')
                return 200, {'has_integrations': False}, 'integrations-status', 0
            if path == '/mf/v1/entries/ids':
                require(body is None and set(query) <= {'status','starred','limit','offset'}, 'unknown entry IDs input')
                require(all(isinstance(v,list) and len(v)==1 and isinstance(v[0],str) for v in query.values()), 'entry IDs values must be singular')
                status, starred = query.get('status',[None])[0], query.get('starred',[None])[0]
                require(status in (None,'read','unread') and starred in (None,'true','false'), 'unsupported entry IDs filter')
                limit, offset = int(query.get('limit',['10000'])[0]), int(query.get('offset',['0'])[0])
                require(1 <= limit <= 10000 and 0 <= offset <= 2147483647, 'unbounded entry IDs request')
                # Native endpoint sorts by ID DESC, counts the filtered scope
                # before pagination, and returns entry_ids (never entry bodies).
                # Derive from the same 72 synthetic rows used by list/detail.
                entries = (self.entry(n, True) for n in range(1, SCENARIO['entries']+1))
                ids = sorted((row['id'] for row in entries
                    if (status is None or row['status']==status)
                    and (starred is None or row['starred']==(starred=='true'))), reverse=True)
                return 200, {'total':len(ids),'entry_ids':ids[offset:offset+limit]}, 'entry-ids', 0
            if path == '/mf/v1/entries':
                require(set(query) <= {'ai_view','ai_min','ai_sort','status','order','direction','limit','offset','globally_visible','starred','search','published_after','published_before','before','after','has_note'}, 'unknown query')
                limit, offset = int(query.get('limit', ['24'])[0]), int(query.get('offset', ['0'])[0])
                require(1 <= limit <= 24 and 0 <= offset <= 72, 'unbounded list request')
                require(query.get('status', ['read'])[0] in ('read','unread'), 'unsupported fixture status')
                if query.get('status') == ['unread']:
                    return 200, {'total':0,'entries':[]}, 'unread-count', 0
                require(query.get('ai_view',['recommended'])[0]=='recommended', 'unsupported benchmark view')
                label = 'list-count' if limit == 1 else f'list:{offset}:{limit}'
                return 200, {'total': 72, 'entries': [self.entry(i, True) for i in range(offset+1, min(offset+limit, 72)+1)]}, label, SCENARIO['list_delay_ms']
            match = re.fullmatch(r'/mf/v1/entries/(\d+)', path)
            if match:
                number = int(match[1]); require(1 <= number <= 72, 'unknown entry')
                return 200, self.entry(number), 'detail:'+str(number), SCENARIO['detail_delay_ms']
            if path == '/mf/v1/feeds': return 200, [self.entry(1)['feed']], 'feeds', 0
            if path == '/mf/v1/categories': return 200, [self.entry(1)['feed']['category']], 'categories', 0
            if path == '/mf/v1/feeds/counters': return 200, {'reads': {'7': 72}, 'unreads': {}}, 'counts', 0
            if path == '/mf/v1/ai/settings': return 200, {'enabled': False, 'translation_enabled': False, 'minimum_score': 6}, 'settings', 0
            if path == '/mf/v1/ai/status': return 200, {'counts': {}, 'coverage': {'total_articles': 72, 'source_count': 1}, 'usage': [], 'events': [], 'resources': {}, 'kaggle': {'enabled': False}}, 'status', 0
            if re.fullmatch(r'/mf/v1/ai/notes/(?:[1-9]|[1-6]\d|7[0-2])', path): return 200, {'note':'', 'note_count':0, 'updated_at':None}, 'note-read', 0
        if path == '/mf/v1/entries' and method == 'PUT':
            require(isinstance(body, dict) and set(body) == {'entry_ids','status'} and body['status'] in ('read','unread') and
                    isinstance(body['entry_ids'], list) and len(body['entry_ids']) <= 24 and all(type(x) is int and 1 <= x <= 72 for x in body['entry_ids']), 'invalid read mutation')
            return 204, {}, 'synthetic-mark-read', 0
        if path == '/mf/v1/ai/reading-session' and method == 'POST':
            require(isinstance(body, dict) and body.get('action') in ('open','heartbeat','close') and type(body.get('entry_id')) is int and 1 <= body['entry_id'] <= 72, 'invalid telemetry')
            return 200, {'ok': True}, 'synthetic-telemetry', 0
        return 501, {'error_message':'unsupported_fixture_api'}, 'unsupported-api', 0
