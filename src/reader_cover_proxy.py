"""Bind a selected cover to native signing; never fetch media from enrichment."""
import base64
import binascii
import hashlib
import hmac
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


SIGNED_PATH = re.compile(r"/mf/proxy/([A-Za-z0-9_-]{43}=)/([A-Za-z0-9_=-]{1,8192})\Z")


def media_proxy_key():
    """Read the existing private config; never create, change, log or return it to clients."""
    try:
        from initialize_secrets import read_env
        value = read_env("miniflux.env").get("MEDIA_PROXY_PRIVATE_KEY")
        return value if isinstance(value, str) and value else None
    except (OSError, ImportError, AttributeError, ValueError):
        return None


def verified_proxy_target(path, key):
    """A cache hit must pass the same HMAC before any cached bytes are exposed."""
    if not key:
        return None
    match = SIGNED_PATH.fullmatch("/mf/" + path)
    if not match:
        return None
    try:
        raw, supplied = _decode(match[2]), _decode(match[1])
        if not hmac.compare_digest(supplied, hmac.new(key.encode(), raw, hashlib.sha256).digest()):
            return None
        target = raw.decode("utf-8")
        # Reuse the existing public URL validation without resolving/fetching here.
        from stabilize_media import signed_url
        signed_url(target, key)
        return target
    except (ValueError, UnicodeError, binascii.Error):
        return None


def stored_cover_proxy(entry, cover_url, row, user_id, key, *, native_base, reader_base=None):
    """Sign only the selected stored cover bound to this authorized current entry."""
    if not key or not row or type(user_id) is not int or not isinstance(cover_url, str):
        return None
    if (entry.get("user_id") != user_id or row.get("user_id") != user_id
            or row.get("entry_id") != entry.get("id") or row.get("url") != entry.get("url")
            or row.get("cover_url") != cover_url):
        return None
    try:
        existing = _native_proxy(cover_url, (native_base, reader_base, "http://127.0.0.1:8092/mf"))
        original = existing[1] if existing else cover_url
        from stabilize_media import signed_url
        signed = signed_url(original, key)
        return signed if SIGNED_PATH.fullmatch(signed) else None
    except (ValueError, UnicodeError):
        return None


def _decode(value):
    raw = base64.b64decode(value, altchars=b"-_", validate=True)
    if base64.urlsafe_b64encode(raw).decode() != value:
        raise ValueError("noncanonical native encoding")
    return raw


def _source_url(value):
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None):
        return None
    return value


def _native_proxy(value, bases):
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if parts.query or parts.fragment or parts.username is not None or parts.password is not None:
            return None
        if parts.netloc or parts.scheme:
            if not any(value.startswith(base.rstrip("/") + "/proxy/") for base in bases if base):
                return None
        elif not value.startswith("/mf/proxy/"):
            return None
        match = SIGNED_PATH.fullmatch(parts.path)
        if not match or len(_decode(match[1])) != 32:
            return None
        target = _decode(match[2]).decode("utf-8")
        if _source_url(target) is None:
            return None
        return parts.path, target
    except (ValueError, UnicodeError, binascii.Error):
        return None


class _CoverFound(Exception):
    pass


class _NativeImages(HTMLParser):
    def __init__(self, find):
        super().__init__(convert_charrefs=True)
        self.find, self.picture_depth, self.result = find, 0, None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "picture":
            self.picture_depth += 1
        urls = []
        if tag == "img" or (tag == "source" and self.picture_depth):
            urls.append(values.get("src"))
            urls.extend(part.strip().split()[0] for part in (values.get("srcset") or "").split(",") if part.strip())
        if tag == "video":
            urls.append(values.get("poster"))
        for url in urls:
            self.result = self.find(url)
            if self.result:
                raise _CoverFound()

    def handle_endtag(self, tag):
        if tag == "picture":
            self.picture_depth = max(0, self.picture_depth - 1)


def selected_cover_proxy(entry, cover_url, *, native_base, reader_base=None):
    """Sign bound stored metadata, or reuse this native entry's same-image signature."""
    if not isinstance(cover_url, str) or not cover_url:
        return None
    if (type(entry.get('id')) is int and type(entry.get('user_id')) is int
            and isinstance(entry.get('url'), str)):
        key = media_proxy_key()
        if key:
            from core import connect
            with connect() as db:
                row = db.execute('SELECT entry_id,user_id,url,cover_url FROM analyses WHERE entry_id=? AND user_id=?',
                                 (entry['id'], entry['user_id'])).fetchone()
            signed = stored_cover_proxy(entry, cover_url, dict(row) if row else None, entry['user_id'], key,
                                        native_base=native_base, reader_base=reader_base)
            if signed:
                return signed
    bases = (native_base, reader_base, "http://127.0.0.1:8092/mf")
    selected_proxy = _native_proxy(cover_url, bases)
    try:
        selected = selected_proxy[1] if selected_proxy else _source_url(cover_url)
    except ValueError:
        return None
    if selected is None:
        return None

    def find(url):
        proxy = _native_proxy(url, bases)
        return proxy[0] if proxy and proxy[1] == selected else None

    for enclosure in entry.get("enclosures") or []:
        if isinstance(enclosure, dict) and str(enclosure.get("mime_type") or "").lower().startswith("image/"):
            matched = find(enclosure.get("url"))
            if matched:
                return matched
    content = entry.get("content")
    if isinstance(content, str) and content:
        parser = _NativeImages(find)
        try:
            parser.feed(content)
        except _CoverFound:
            return parser.result
    return None
