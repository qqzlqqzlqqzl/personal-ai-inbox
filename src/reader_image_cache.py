"""Bounded, disposable image variants. No URLs, credentials or database stored."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

try:
    import fcntl
except ImportError:  # The Windows unit tests still exercise the same cache logic.
    fcntl = None

MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_FILES = 8192
MAX_HEADER = 4096
MAX_TTL = 7 * 86400
DEFAULT_TTL = 86400
NAME = re.compile(r'[0-9a-f]{64}\.image\Z')
CACHE_HEADERS = {'content-type', 'cache-control', 'last-modified', 'content-security-policy',
                 'etag', 'vary', 'age', 'pragma'}


def image_priority(value):
    """Publication time only; unknown dates and ordinary clicks have priority 0."""
    try:
        value = float(value)
        return max(0, min(99999999999, value)) if math.isfinite(value) else 0
    except (ValueError, TypeError):
        return 0


def cache_lifetime(headers):
    directives = {part.strip().split('=', 1)[0].lower(): part.strip().partition('=')[2].strip('"')
                  for part in headers.get('cache-control', '').split(',') if part.strip()}
    if set(directives) & {'no-store', 'no-cache', 'private', 'must-revalidate', 'proxy-revalidate'}:
        return 0
    if 'no-cache' in headers.get('pragma', '').lower() or 'set-cookie' in headers:
        return 0
    try:
        age = max(0, int(headers.get('age', '0')))
        lifetime = int(directives.get('s-maxage', directives.get('max-age', DEFAULT_TTL)))
        return max(0, min(MAX_TTL, lifetime - age))
    except (TypeError, ValueError):
        return 0


class ImageCache:
    def __init__(self, root, *, max_bytes=MAX_TOTAL_BYTES, max_files=MAX_FILES, clock=time.time):
        self.root, self.max_bytes, self.max_files, self.clock = Path(root), max_bytes, max_files, clock
        self._mutex = threading.RLock()
        self._keys = [threading.Lock() for _ in range(64)]

    def _prepare(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or self.root.is_symlink():
            raise OSError('invalid image cache directory')
        if hasattr(os, 'getuid') and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise OSError('image cache is not private')

    @contextmanager
    def _file_lock(self, name):
        self._prepare()
        fd = os.open(self.root / name, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError('invalid image cache lock')
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    @contextmanager
    def singleflight(self, key):
        slot = int(key[:2], 16) % len(self._keys)
        with self._keys[slot], self._file_lock(f'.key-{slot}.lock'):
            yield

    @contextmanager
    def _index(self):
        with self._mutex, self._file_lock('.index.lock'):
            yield

    def get(self, key, *, priority=0):
        path = self.root / (key + '.image')
        with self._index():
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    return None
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as stream:
                    size = os.fstat(stream.fileno()).st_size
                    if size > MAX_IMAGE_BYTES + MAX_HEADER:
                        return None
                    line = stream.readline(MAX_HEADER)
                    if not line.endswith(b'\n'):
                        return None
                    meta = json.loads(line)
                    body = stream.read(MAX_IMAGE_BYTES + 1)
                now = self.clock()
                if meta['expires'] <= now:
                    path.unlink()
                    return None
                if len(body) > MAX_IMAGE_BYTES or hashlib.sha256(body).hexdigest() != meta['sha256']:
                    return None
                # A shared image belongs to its newest reference. Neither an old
                # article nor an ordinary foreground hit can demote it.
                if image_priority(priority) > image_priority(meta.get('priority', 0)):
                    meta['priority'] = image_priority(priority)
                    self._store(key, body, meta, background=False)
                os.utime(path, (now, meta['expires']))
                headers = dict(meta['headers'])
                headers['age'] = str(int(headers.get('age', '0')) + max(0, int(now - meta['created'])))
                return body, headers
            except (OSError, ValueError, KeyError, TypeError):
                return None

    def put(self, key, body, headers, *, priority=0, background=False):
        lifetime = cache_lifetime(headers)
        if not lifetime or not body or len(body) > MAX_IMAGE_BYTES:
            return
        now = self.clock()
        meta = dict(created=now, expires=now + lifetime,
                    priority=image_priority(priority),
                    headers={name: value for name, value in headers.items() if name in CACHE_HEADERS},
                    sha256=hashlib.sha256(body).hexdigest())
        with self._index():
            return self._store(key, body, meta, background=background)

    def _inventory(self):
        """Called under the index lock, including while atomic writes reserve space."""
        files, other = [], 0
        for path in self.root.iterdir():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            if path.name.startswith('.pending-'):
                path.unlink()  # No live writer can hold this same index lock.
                continue
            if not NAME.fullmatch(path.name):
                other += info.st_size
                continue
            if info.st_mtime <= self.clock():
                path.unlink()
                continue
            try:
                with path.open('rb') as stream:
                    meta = json.loads(stream.readline(MAX_HEADER))
                priority = image_priority(meta.get('priority', 0))
            except (ValueError, TypeError, AttributeError):
                path.unlink()
                continue
            files.append((priority, info.st_atime, info.st_size, path))
        return files, other

    def can_admit(self, priority, *, size=MAX_IMAGE_BYTES + MAX_HEADER):
        """Conservative background reservation before HTTP; never churn newer images.

        At most one maximum-size image of headroom is left unused. Equal-date
        images are also protected so one huge article cannot rotate its own tail.
        Foreground requests are never subject to this background admission gate.
        """
        with self._index():
            files, other = self._inventory()
            protected = [row for row in files if row[0] >= image_priority(priority)]
            return (other + sum(row[2] for row in protected) + min(size, self.max_bytes) <= self.max_bytes
                    and len(protected) < self.max_files)

    def _store(self, key, body, meta, *, background):
        # Caller owns the index lock. Include headers and the temporary file in
        # the budget; unlink selected victims before writing the replacement.
        files, other = self._inventory()
        existing = next((row for row in files if row[3].name == key + '.image'), None)
        if existing:
            meta['priority'] = max(image_priority(meta.get('priority')), existing[0])
        line = (json.dumps(meta, separators=(',', ':')) + '\n').encode()
        if len(line) >= MAX_HEADER or len(line) + len(body) > self.max_bytes:
            return False
        total, count = other + sum(row[2] for row in files), len(files)
        victims = [existing] if existing else []
        if existing:
            total -= existing[2]
            count -= 1
        for row in sorted(files):
            if total + len(line) + len(body) <= self.max_bytes and count < self.max_files:
                break
            if row == existing or (background and row[0] >= meta['priority']):
                continue
            victims.append(row)
            total -= row[2]
            count -= 1
        if total + len(line) + len(body) > self.max_bytes or count >= self.max_files:
            return False
        for row in victims:
            row[3].unlink()
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(line)
                output.write(body)
            os.utime(temporary, (self.clock(), meta['expires']))
            os.replace(temporary, self.root / (key + '.image'))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True


def variant_key(native_base, target, width, accept):
    return hashlib.sha256(json.dumps([native_base, target, width, accept], separators=(',', ':')).encode()).hexdigest()
