"""Bounded display variants fetched through Miniflux's signed media proxy.

The original URL is unchanged. No key, external URL fetch, or server cache lives
here. A cancelled HTTP waiter does not free an in-flight worker slot; Pillow
cannot be hard-killed by an asyncio timeout and is not claimed to be.
"""
import asyncio
import hashlib
import io
import re
import time

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.responses import Response

from reader_work import ReaderWorkPool

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


def fetch_variant(native_base, path, width, accept, *, client_factory=httpx.Client):
    """Signature and upstream access checks remain entirely with Miniflux."""
    started = time.monotonic()
    # Do not forward conditional headers: native 304 short-circuits its signature
    # validation. Validate/fetch first, then derive our own representation ETag.
    with client_factory(timeout=httpx.Timeout(5, connect=3), follow_redirects=False,
                        trust_env=False) as client:
        with client.stream("GET", native_base.rstrip("/") + "/" + path,
                           headers={"Accept": accept}) as upstream:
            length = upstream.headers.get("content-length")
            if length and int(length) > MAX_BYTES:
                return Response(status_code=413, headers={"Cache-Control": "no-store"})
            body = bytearray()
            for chunk in upstream.iter_bytes():
                if time.monotonic() - started > FETCH_SECONDS:
                    raise TimeoutError("image fetch deadline")
                if len(body) + len(chunk) > MAX_BYTES:
                    return Response(status_code=413, headers={"Cache-Control": "no-store"})
                body.extend(chunk)
            headers = {key: value for key, value in upstream.headers.items() if key.lower() in
                       {"content-type", "cache-control", "last-modified", "content-security-policy", "location"}}
            if upstream.status_code != 200:
                return Response(bytes(body), status_code=upstream.status_code, headers=headers)
            content_type = headers.get("content-type", "application/octet-stream")
            result, content_type = resize_image(bytes(body), content_type, width)
            headers.update({"Content-Type": content_type, "Vary": "Accept",
                            "ETag": '"' + hashlib.sha256(result).hexdigest() + '"'})
            # Discard any old-casing content-type before Starlette serializes.
            headers.pop("content-type", None)
            return Response(result, headers=headers)


async def proxy_reader_image(path, request, native_base):
    values = request.query_params.getlist("reader_width")
    if (request.method != "GET" or not SIGNED_PATH.fullmatch(path)
            or len(values) != 1 or values[0] not in {str(n) for n in WIDTHS}
            or set(request.query_params) != {"reader_width"}):
        return Response(status_code=400, headers={"Cache-Control": "no-store"})
    try:
        response = await asyncio.wait_for(image_work.run(
            fetch_variant, native_base, path, int(values[0]), request.headers.get("accept", "image/*")),
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
