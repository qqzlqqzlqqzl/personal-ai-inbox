"""Only image variant admission, native response boundaries and real codecs."""
import asyncio
import io
import json
import threading

import httpx
import pytest
from PIL import Image
from starlette.requests import Request
from starlette.responses import Response

import reader_image_proxy as images
from reader_work import ReaderWorkPool
from reader_image_cache import ImageCache
from stabilize_media import signed_url

PATH = "proxy/" + "A" * 43 + "=/aHR0cHM6Ly9leGFtcGxlLm9yZy9waG90by5qcGc="


def picture(size=(2400, 1600), kind="JPEG", mode="RGB", **save):
    target = io.BytesIO()
    with Image.new(mode, size, (40, 80, 120, 100) if mode == "RGBA" else (40, 80, 120)) as image:
        image.save(target, kind, **save)
    return target.getvalue()


def request(query="reader_width=960", headers=(), method="GET"):
    return Request({"type": "http", "method": method, "path": "/mf/" + PATH,
                    "query_string": query.encode(), "headers": list(headers)})


@pytest.mark.parametrize("width", images.WIDTHS)
def test_real_jpeg_is_smaller_bounded_and_keeps_aspect(width):
    original = picture()
    result, mime = images.resize_image(original, "image/jpeg", width)
    with Image.open(io.BytesIO(result)) as decoded:
        assert decoded.width <= width
        assert decoded.height <= images.MAX_OUTPUT_HEIGHT
        assert decoded.width * decoded.height <= images.MAX_OUTPUT_PIXELS
        assert abs(decoded.width / decoded.height - 1.5) < .004
        assert decoded.getpixel((0, 0)) == pytest.approx((40, 80, 120), abs=3)
    assert mime == "image/jpeg" and len(result) < len(original)


def test_exif_rotation_and_png_alpha_survive():
    exif = Image.Exif(); exif[274] = 6
    body = picture((2000, 1000), exif=exif)
    result, _ = images.resize_image(body, "image/jpeg", 960)
    with Image.open(io.BytesIO(result)) as image:
        assert image.height > image.width and image.width <= 960
        assert image.getexif().get(274, 1) == 1
    result, mime = images.resize_image(picture(kind="PNG", mode="RGBA"), "image/png", 960)
    with Image.open(io.BytesIO(result)) as image:
        assert image.mode == "RGBA" and image.getpixel((0, 0))[3] == 100
    assert mime == "image/png"


def test_small_invalid_nonimage_animation_and_pixel_limit_do_not_reencode(monkeypatch):
    small = picture((32, 24))
    for body, mime in [(small, "image/jpeg"), (b"broken jpeg", "image/jpeg"),
                       (b"<svg>untrusted</svg>", "image/svg+xml"), (b"<html>error</html>", "text/html")]:
        assert images.resize_image(body, mime, 960) == (body, mime)
    target = io.BytesIO()
    with Image.new("RGB", (1200, 900), "red") as first, Image.new("RGB", (1200, 900), "blue") as second:
        first.save(target, "WEBP", save_all=True, append_images=[second], duration=100, loop=0)
    animated = target.getvalue()
    assert images.resize_image(animated, "image/webp", 960) == (animated, "image/webp")
    large = picture()
    monkeypatch.setattr(images, "MAX_PIXELS", 100)
    assert images.resize_image(large, "image/jpeg", 960) == (large, "image/jpeg")


def test_long_diagram_is_not_made_unreadably_narrow_to_fit_output_limit():
    original = picture((1000, 6000))
    assert images.resize_image(original, "image/jpeg", 960) == (original, "image/jpeg")


def test_native_failure_is_preserved_before_codec_and_conditionals_never_forward(monkeypatch):
    calls = []
    def native(req):
        calls.append(req)
        assert req.url.path == "/mf/" + PATH and req.url.query == b""
        assert not any(key in req.headers for key in ("if-none-match", "if-modified-since", "authorization", "cookie"))
        return httpx.Response(403, content=b"Forbidden", headers={"content-type": "text/plain"})
    def factory(**options):
        assert options["follow_redirects"] is False and options["trust_env"] is False
        return httpx.AsyncClient(transport=httpx.MockTransport(native), **options)
    monkeypatch.setattr(images, "resize_image", lambda *_: pytest.fail("codec before native approval"))
    response = images.fetch_variant("http://native.test/mf", PATH, 960, "image/*", client_factory=factory)
    assert response.status_code == 403 and response.body == b"Forbidden" and len(calls) == 1


def test_native_success_keeps_cache_policy_and_has_variant_etag():
    source = picture()
    def factory(**options):
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=source, headers={"content-type": "image/jpeg", "etag": '"native"',
                "cache-control": "public, max-age=259200", "content-security-policy": "default-src 'none'; sandbox"})), **options)
    response = images.fetch_variant("http://native.test/mf", PATH, 960, "image/*", client_factory=factory)
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.headers["etag"] != '"native"'
    assert response.headers["cache-control"] == "public, max-age=259200"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    with Image.open(io.BytesIO(response.body)) as decoded:
        assert decoded.width <= 960


@pytest.mark.parametrize("declared", [False, True])
def test_actual_and_declared_input_caps_stop_before_codec(monkeypatch, declared):
    monkeypatch.setattr(images, "MAX_BYTES", 16)
    monkeypatch.setattr(images, "resize_image", lambda *_: pytest.fail("oversized codec input"))
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 17
            pytest.fail("read after byte cap")
    def factory(**options):
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, stream=Stream(), headers={"content-type": "image/jpeg", **({"content-length": "100"} if declared else {})})), **options)
    assert images.fetch_variant("http://native.test/mf", PATH, 960, "image/*", client_factory=factory).status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["reader_width=0", "reader_width=320", "reader_width=999999", "reader_width=nan",
                                    "reader_width=960&reader_width=1600", "reader_width=960&url=https://outside.test/"])
async def test_invalid_variant_never_fetches(monkeypatch, query):
    monkeypatch.setattr(images, "fetch_variant", lambda *_: pytest.fail("invalid variant fetched"))
    assert (await images.proxy_reader_image(PATH, request(query), "http://native.test/mf")).status_code == 400


@pytest.mark.asyncio
async def test_cache_validator_is_applied_only_after_native_response(monkeypatch):
    calls = []
    def worker(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(b"pixels", headers={"ETag": '"variant"', "Cache-Control": "public, max-age=259200"})
    monkeypatch.setattr(images, "fetch_variant", worker)
    result = await images.proxy_reader_image(PATH, request(headers=[(b"if-none-match", b'"variant"')]), "http://native.test/mf")
    assert len(calls) == 1 and result.status_code == 304 and result.body == b""
    assert result.headers["cache-control"] == "public, max-age=259200"
    assert 0 < calls[0][1]['hot_until'] - images.time.time() <= 900


@pytest.mark.asyncio
@pytest.mark.parametrize('width', [480, 960])
async def test_actual_gateway_dispatch_keeps_original_route_unchanged(monkeypatch, width):
    import api
    calls = []
    async def variant(path, req, native):
        calls.append((path, native))
        return Response(b"variant", media_type="image/jpeg")
    monkeypatch.setattr(images, "proxy_reader_image", variant)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, content=b"original", headers={"content-type": "image/jpeg"}))) as native:
        monkeypatch.setattr(api.app.state, "client", native, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://testserver") as browser:
            original = await browser.get("/mf/" + PATH)
            resized = await browser.get("/mf/" + PATH + f"?reader_width={width}")
    assert original.content == b"original" and resized.content == b"variant"
    assert calls == [(PATH, api.MF)]


@pytest.mark.asyncio
async def test_cancellation_and_timeout_keep_actual_workers_bounded(monkeypatch):
    pool = ReaderWorkPool(limit=2)
    monkeypatch.setattr(images, "image_work", pool)
    monkeypatch.setattr(images, "REQUEST_SECONDS", .08)
    entered = threading.Event(); release = threading.Event(); calls = []
    def blocked(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2: entered.set()
        assert release.wait(2)
        return Response(b"late")
    monkeypatch.setattr(images, "fetch_variant", blocked)
    first = asyncio.create_task(images.proxy_reader_image(PATH, request(), "http://native.test/mf"))
    second = asyncio.create_task(images.proxy_reader_image(PATH, request(), "http://native.test/mf"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError): await first
        assert (await second).status_code == 504
        assert (await images.proxy_reader_image(PATH, request(), "http://native.test/mf")).status_code == 504
        assert len(calls) == 2  # No replacement workers or late queued job after timeout.
    finally:
        release.set()
        await asyncio.to_thread(pool.executor.shutdown, wait=True)


def test_disk_hit_deduplicates_fetch_and_codec_and_rejects_forged_signature(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    key = 'test-only-stable-key'
    path = signed_url('https://example.org/photo.jpg', key)[4:]
    cache = ImageCache(tmp_path / 'images')
    fetches, codecs = [], []
    resize = images.resize_image
    def codec(*args):
        codecs.append(1)
        return resize(*args)
    monkeypatch.setattr(images, 'resize_image', codec)
    async def native(req):
        fetches.append(req)
        assert req.headers['accept'] == images.NATIVE_IMAGE_ACCEPT
        await asyncio.sleep(.02)
        if req.url.path != '/mf/' + path:
            return httpx.Response(403, content=b'Forbidden')
        return httpx.Response(200, content=picture(), headers={'content-type': 'image/jpeg',
            'cache-control': 'public, max-age=60', 'set-cookie': 'native-session=synthetic; HttpOnly'})
    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(native), **kwargs)
    def read(accept, hot_until=0):
        return images.fetch_variant('http://native.test/mf', path, 960, accept,
                                    client_factory=factory, cache=cache, signature_key=key, hot_until=hot_until)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(read, ('image/*', 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8')))
    assert all(result.status_code == 200 for result in results)
    assert len(fetches) == len(codecs) == 1 and results[0].body == results[1].body
    assert all(result.headers['cache-control'] == 'public, max-age=60'
               and 'set-cookie' not in result.headers for result in results)
    assert len(list(cache.root.glob('*.image'))) == 1
    assert read('image/avif,image/webp,image/*').body == results[0].body
    assert len(fetches) == len(codecs) == 1
    until = (int(images.time.time()) // 300) * 300 + 900
    assert read("image/*", hot_until=until).body == results[0].body
    assert len(fetches) == len(codecs) == 1
    meta = json.loads(next(cache.root.glob("*.image")).read_bytes().split(b"
", 1)[0])
    assert meta["hot_until"] == until
    assert meta["expires"] - meta["created"] == 60
    forged = path.replace(path.split('/')[1], 'A' * 43 + '=')
    denied = images.fetch_variant('http://native.test/mf', forged, 960, 'image/*',
                                  client_factory=factory, cache=cache, signature_key=key)
    assert denied.status_code == 403 and len(fetches) == 2 and len(codecs) == 1


@pytest.mark.parametrize('policy,pragma,status', [
    (None, None, 200), ('max-age=60', None, 200), ('public, private', None, 200),
    ('public, no-store', None, 200), ('public, no-cache', None, 200),
    ('public, must-revalidate', None, 200), ('public, max-age=0', None, 200),
    ('public, max-age=60', 'no-cache', 200), ('public, max-age=60', None, 403)])
def test_cookie_without_unconflicted_public_image_does_not_cache(tmp_path, policy, pragma, status):
    key = 'test-only-stable-key'
    path = signed_url('https://example.org/photo.jpg', key)[4:]
    cache = ImageCache(tmp_path / 'images')
    calls = []
    headers = {'content-type': 'image/jpeg', 'set-cookie': 'native-session=synthetic; HttpOnly'}
    if policy is not None:
        headers['cache-control'] = policy
    if pragma is not None:
        headers['pragma'] = pragma
    def native(req):
        calls.append(req)
        return httpx.Response(status, content=picture((32, 24)), headers=headers)
    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(native), **kwargs)
    for _ in range(2):
        response = images.fetch_variant('http://native.test/mf', path, 960, 'image/*',
                                        client_factory=factory, cache=cache, signature_key=key)
        assert response.status_code == status and response.headers['cache-control'] == 'no-store'
        assert 'set-cookie' not in response.headers
    assert len(calls) == 2 and not list(cache.root.glob('*.image'))


@pytest.mark.parametrize('body,mime,policy', [(b'<html>login</html>', 'text/html', 'public, max-age=60'),
    (b'<html>not an image</html>', 'image/jpeg', 'public, max-age=60'),
    (None, 'image/jpeg', 'no-store')])
def test_errors_and_no_store_do_not_persist(tmp_path, body, mime, policy):
    key = 'test-only-stable-key'
    path = signed_url('https://example.org/photo.jpg', key)[4:]
    cache = ImageCache(tmp_path / 'images')
    calls = []
    def native(req):
        calls.append(req)
        return httpx.Response(200, content=picture() if body is None else body,
                              headers={'content-type': mime, 'cache-control': policy})
    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(native), **kwargs)
    for _ in range(2):
        images.fetch_variant('http://native.test/mf', path, 960, 'image/*', client_factory=factory, cache=cache, signature_key=key)
    assert len(calls) == 2 and not list(cache.root.glob('*.image'))


def test_slow_origin_past_old_five_seconds_and_true_whole_fetch_deadline(monkeypatch):
    # Scale the wall time, while checking that the actual native read budget is
    # 20s rather than the former 5s and that all headers/body I/O is cancellable.
    assert images.FETCH_SECONDS == 20 and images.REQUEST_SECONDS == 30
    body = picture((32, 24))
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(.06)
            yield body[:len(body)//2]
            await asyncio.sleep(.06)
            yield body[len(body)//2:]
    def factory(**kwargs):
        assert kwargs['timeout'].read == images.FETCH_SECONDS
        async def native(req):
            return httpx.Response(200, stream=SlowBody(), headers={'content-type': 'image/jpeg'})
        return httpx.AsyncClient(transport=httpx.MockTransport(native), **kwargs)
    monkeypatch.setattr(images, 'FETCH_SECONDS', .2)
    assert images.fetch_variant('http://native.test/mf', PATH, 960, 'image/*', client_factory=factory, signature_key=None).status_code == 200
    monkeypatch.setattr(images, 'FETCH_SECONDS', .08)
    with pytest.raises(TimeoutError):
        images.fetch_variant('http://native.test/mf', PATH, 960, 'image/*', client_factory=factory, signature_key=None)
