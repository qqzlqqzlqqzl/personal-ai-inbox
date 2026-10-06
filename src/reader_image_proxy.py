"""Bounded display variants fetched through Miniflux's signed media proxy.

The original URL is unchanged. Native signatures are checked before disk hits;
all cache misses still fetch through Miniflux. A cancelled HTTP waiter does not free an in-flight worker slot; Pillow
cannot be hard-killed by an asyncio timeout and is not claimed to be.
"""
import asyncio
import contextlib
import hashlib
import io
import re

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.responses import Response

from reader_work import ReaderWorkPool
from reader_cover_proxy import media_proxy_key, verified_proxy_target
from reader_image_cache import ImageCache, variant_key
from core import ROOT

WIDTHS = (480, 960, 1600)
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_OUTPUT_PIXELS = 4_000_000
MAX_OUTPUT_HEIGHT = 4096
FETCH_SECONDS = 20
REQUEST_SECONDS = 30
SIGNED_PATH = re.compile(r"proxy/[A-Za-z0-9_-]{43}=/[A-Za-z0-9_=-]{1,8192}\Z")
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
image_work = ReaderWorkPool(limit=2)
image_cache = ImageCache(ROOT / 'state' / 'reader-image-cache')
_UNSET = object()


def resize_image(body, content_type, width):
    """Preserve invalid/unsupported/animated originals; never upscale or crop."""
    if width not in WIDTHS or len(body) > MAX_BYTES:
        raise ValueError("image variant limit")
    expected = FORMATS.get(content_type.split(";", 1)[0].strip().lower())
    if expected is None:
        return body, content_type
    try:
        with Image.open(io.BytesIO(body), formats=[expected]) as original:
            w, h = original.size
            if not w or not h or w * h > MAX_PIXELS or getattr(original, "n_frames", 1) != 1:
                return body, content_type
            if w <= width and h <= MAX_OUTPUT_HEIGHT and w * h <= MAX_OUTPUT_PIXELS:
                return body, content_type
            # Leave ordinary JPEGs lazy so thumbnail can use decoder downsampling.
            image = original if original.getexif().get(274, 1) == 1 else ImageOps.exif_transpose(original)
            try:
                w, h = image.size
                scale = min(1, width / w)
                target = (max(1, int(w * scale)), max(1, int(h * scale)))
                # Preserve long diagrams rather than shrink their text below the
                # selected display width just to fit the conversion budget.
                if target[1] > MAX_OUTPUT_HEIGHT or target[0] * target[1] > MAX_OUTPUT_PIXELS:
                    return body, content_type
                if expected == "JPEG" and image is original:
                    image.draft(image.mode, target)
                image.thumbnail(target, Image.Resampling.LANCZOS, reducing_gap=3.0)
                if (image.width > width or image.height > MAX_OUTPUT_HEIGHT
                        or image.width * image.height > MAX_OUTPUT_PIXELS):
                    return body, content_type
                if expected == "JPEG" and image.mode not in ("RGB", "L"):
                    converted = image.convert("RGB")
                    image.close()
                    image = converted
                output = io.BytesIO()
                options = {"quality": 82, "optimize": True} if expected == "JPEG" else (
                    {"quality": 82, "method": 4} if expected == "WEBP" else {"compress_level": 6})
                image.save(output, format=expected, **options)
                result = output.getvalue()
                if len(result) > MAX_BYTES:
                    return body, content_type
                return result, Image.MIME[expected]
            finally:
                image.close()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return body, content_type


async def _native_image(native_base, path, accept, client_factory):
    """An actual deadline includes connection, response headers and the whole body."""
    # Do not forward conditional headers: native 304 short-circuits its signature
    # validation. Validate/fetch first, then derive our own representation ETag.
    async with asyncio.timeout(FETCH_SECONDS), client_factory(timeout=httpx.Timeout(FETCH_SECONDS, connect=3), follow_redirects=False,
                        trust_env=False) as client:
        async with client.stream("GET", native_base.rstrip("/") + "/" + path,
                           headers={"Accept": accept}) as upstream:
            length = upstream.headers.get("content-length")
            if length and int(length) > MAX_BYTES:
                return Response(status_code=413, headers={"Cache-Control": "no-store"})
            body = bytearray()
            async for chunk in upstream.aiter_bytes():
                if len(body) + len(chunk) > MAX_BYTES:
                    return Response(status_code=413, headers={"Cache-Control": "no-store"})
                body.extend(chunk)
            headers = {key: value for key, value in upstream.headers.items() if key.lower() in
                       {"content-type", "cache-control", "last-modified", "content-security-policy", "location", "age", "pragma"}}
            if 'set-cookie' in upstream.headers:
                headers['cache-control'] = 'no-store'
            if upstream.status_code != 200:
                return Response(bytes(body), status_code=upstream.status_code, headers=headers)
            return bytes(body), headers


def _is_cache_image(body, content_type):
    expected = FORMATS.get(content_type.split(';', 1)[0].strip().lower())
    if not expected:
        return False
    try:
        with Image.open(io.BytesIO(body), formats=[expected]) as image:
            if image.width * image.height > MAX_PIXELS:
                return False
            image.verify()
        return True
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return False


def _make_variant(native_base, path, width, accept, client_factory):
    native = asyncio.run(_native_image(native_base, path, accept, client_factory))
    if isinstance(native, Response):
        return native
    body, headers = native
    result, content_type = resize_image(body, headers.get('content-type', 'application/octet-stream'), width)
    headers.update({'Content-Type': content_type, 'Vary': 'Accept',
                    'ETag': '"' + hashlib.sha256(result).hexdigest() + '"'})
    headers.pop('content-type', None)
    return Response(result, headers=headers)


def fetch_variant(native_base, path, width, accept, cache_policy='', *, client_factory=httpx.AsyncClient,
                  cache=None, signature_key=_UNSET):
    """Local HMAC gates hits; native validates every miss. Failure never becomes a hit."""
    private_key = media_proxy_key() if signature_key is _UNSET else signature_key
    target = verified_proxy_target(path, private_key)
    bypass = any(part.strip().split('=', 1)[0].lower() in {'no-store', 'no-cache'}
                 for part in cache_policy.split(','))
    bypass = bypass or any(part.strip().lower() in {'max-age=0', 'max-age="0"'} for part in cache_policy.split(','))
    if target and not bypass:
        cache = image_cache if cache is None else cache
        key = variant_key(native_base, target, width, accept)
        try:
            with cache.singleflight(key):
                hit = cache.get(key)
                if hit:
                    return Response(hit[0], headers=hit[1])
                response = _make_variant(native_base, path, width, accept, client_factory)
                if response.status_code == 200 and _is_cache_image(response.body, response.headers.get('content-type', '')):
                    with contextlib.suppress(OSError):
                        cache.put(key, response.body, dict(response.headers))
                return response
        except OSError:
            pass  # Cache unavailable: still use the ordinary native proxy.
    return _make_variant(native_base, path, width, accept, client_factory)


async def proxy_reader_image(path, request, native_base):
    values = request.query_params.getlist("reader_width")
    if (request.method != "GET" or not SIGNED_PATH.fullmatch(path)
            or len(values) != 1 or values[0] not in {str(n) for n in WIDTHS}
            or set(request.query_params) != {"reader_width"}):
        return Response(status_code=400, headers={"Cache-Control": "no-store"})
    try:
        response = await asyncio.wait_for(image_work.run(
            fetch_variant, native_base, path, int(values[0]), request.headers.get("accept", "image/*"),
            request.headers.get('cache-control', '')),
            timeout=REQUEST_SECONDS)
    except (httpx.HTTPError, TimeoutError):
        return Response(status_code=504, headers={"Cache-Control": "no-store"})
    except ValueError:
        return Response(status_code=502, headers={"Cache-Control": "no-store"})
    # The two existing worker slots stay occupied until real work completes,
    # including after cancellation or timeout. Queued timed-out calls never start.
    if response.status_code == 200 and request.headers.get("if-none-match") == response.headers.get("etag"):
        return Response(status_code=304, headers=dict(response.headers))
    return response
