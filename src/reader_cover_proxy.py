"""Reuse an existing native signature for the same selected cover; never sign/fetch."""
import base64
import binascii
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


SIGNED_PATH = re.compile(r"/mf/proxy/([A-Za-z0-9_-]{43}=)/([A-Za-z0-9_=-]{1,8192})\Z")


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
    """Only current native media URLs may supply a signature; missing means raw fallback."""
    if not isinstance(cover_url, str) or not cover_url:
        return None
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
