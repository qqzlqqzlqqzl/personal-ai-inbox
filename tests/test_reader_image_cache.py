"""Run with existing Linux pytest: eviction removes only its tmp_path cache blobs."""
import hashlib

from reader_image_cache import ImageCache, cache_lifetime, variant_key


HEADERS = {'content-type': 'image/jpeg', 'cache-control': 'public, max-age=60'}


def key(label):
    return hashlib.sha256(label.encode()).hexdigest()


def test_persistent_hit_expiry_and_age(tmp_path):
    now = [100.0]
    cache = ImageCache(tmp_path / 'images', clock=lambda: now[0])
    cache.put(key('one'), b'jpeg-bytes', HEADERS)
    now[0] = 120
    fresh_process = ImageCache(tmp_path / 'images', clock=lambda: now[0])
    body, headers = fresh_process.get(key('one'))
    assert body == b'jpeg-bytes' and headers['age'] == '20'
    now[0] = 160
    assert cache.get(key('one')) is None
    assert not list(cache.root.glob('*.image'))


def test_byte_budget_lru_and_file_count(tmp_path):
    defaults = ImageCache(tmp_path / 'defaults')
    assert defaults.max_bytes == 1024 * 1024 * 1024 and defaults.max_files == 8192
    now = [100.0]
    cache = ImageCache(tmp_path / 'images', max_bytes=1100, max_files=2, clock=lambda: now[0])
    for label in ('a', 'b'):
        cache.put(key(label), b'x' * 250, HEADERS)
        now[0] += 1
    assert cache.get(key('a')) is not None
    now[0] += 1
    cache.put(key('c'), b'x' * 250, HEADERS)
    assert cache.get(key('b')) is None
    assert cache.get(key('a')) is not None and cache.get(key('c')) is not None
    assert sum(p.stat().st_size for p in cache.root.iterdir()) <= 1100
    assert len(list(cache.root.glob('*.image'))) == 2


def test_cache_policy_and_single_image_limit(tmp_path):
    cache = ImageCache(tmp_path / 'images')
    for policy in ('no-store', 'public, no-store', 'private', 'no-cache', 'max-age=0', 'max-age=bad'):
        cache.put(key(policy), b'payload', {**HEADERS, 'cache-control': policy})
    cache.put(key('large'), b'x' * (8 * 1024 * 1024 + 1), HEADERS)
    assert not cache.root.exists()
    assert cache_lifetime({'cache-control': 'max-age=60', 'age': '20'}) == 40
    assert cache_lifetime({'cache-control': 'max-age=999999999'}) == 7 * 86400


def test_same_image_size_and_accept_keys_and_corruption(tmp_path):
    first = variant_key('http://native/mf', 'https://example.org/img', 480, 'image/*')
    assert first == variant_key('http://native/mf', 'https://example.org/img', 480, 'image/*')
    assert first != variant_key('http://native/mf', 'https://example.org/img', 960, 'image/*')
    cache = ImageCache(tmp_path / 'images')
    cache.put(first, b'jpeg-bytes', HEADERS)
    (cache.root / (first + '.image')).write_bytes(b'broken header\nwrong image')
    assert cache.get(first) is None


# These direct boundary tests also run with stdlib unittest in the existing
# environment when the full pytest dependency set is unavailable.
import asyncio
import io
import json
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
import reader_image_proxy as images
from stabilize_media import signed_url


class HotWindowCacheTests(unittest.TestCase):
    def setUp(self):
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [1800000000.0]  # A five-minute boundary.
        self.cache = ImageCache(Path(self.temp.name) / 'images', max_bytes=65536,
                                max_files=16, clock=lambda: self.now[0])
        self.headers = {**HEADERS, 'cache-control': 'public, max-age=3600'}
        self.deadline = self.now[0] + 900

    def put(self, label, *, priority=0, hot_until=0, body=b'x' * 100, background=False):
        return self.cache.put(key(label), body, self.headers, priority=priority,
                              hot_until=hot_until, background=background)

    def path(self, label):
        return self.cache.root / (key(label) + '.image')

    def hot_paths(self):
        with self.cache._index():
            rows, _ = self.cache._inventory()
            hot = self.cache._protected(rows, 0, 0, 0, background=False)
            self.assertLessEqual(sum(row[2] for row in rows if row[3] in hot),
                                 min(128 * 1024 * 1024, self.cache.max_bytes // 8))
            self.assertLessEqual(len(hot), self.cache.max_files // 8)
            return hot

    def test_bucketed_hits_do_not_scan_or_rewrite_and_do_not_extend_http_ttl(self):
        self.put('shared', priority=200, hot_until=self.deadline)
        original = self.path('shared').read_bytes()
        for step in (30, 60, 120, 240):
            self.now[0] = 1800000000 + step
            with patch.object(self.cache, '_inventory', side_effect=AssertionError('hot hit scanned')):
                self.assertIsNotNone(self.cache.get(key('shared'), hot_until=self.now[0] + 900))
                self.assertIsNotNone(self.cache.get(key('shared'), priority=50))
            self.assertEqual(self.path('shared').read_bytes(), original)
        self.now[0] = 1800000300
        with patch.object(self.cache, '_inventory', wraps=self.cache._inventory) as scan:
            self.cache.get(key('shared'), hot_until=self.now[0] + 900)
            self.assertEqual(scan.call_count, 1)
        meta = json.loads(self.path('shared').read_bytes().split(b'\n')[0])
        self.assertEqual(meta['priority'], 200)
        self.assertEqual(meta['hot_until'], 1800001200)
        self.assertEqual(meta['expires'], 1800003600)
        self.now[0] = 1800003600
        self.assertIsNone(self.cache.get(key('shared'), hot_until=self.now[0] + 900))

    def test_shared_key_merges_lease_without_duplicate_quota_after_restart(self):
        self.put('shared', priority=200, hot_until=self.deadline)
        self.now[0] += 30
        self.put('shared', priority=50, hot_until=0)
        self.put('shared', priority=100, hot_until=self.deadline - 300)
        self.cache = ImageCache(self.cache.root, max_bytes=65536, max_files=16, clock=lambda: self.now[0])
        self.assertEqual(self.hot_paths(), {self.path('shared')})
        meta = json.loads(self.path('shared').read_bytes().split(b'\n')[0])
        self.assertEqual((meta['priority'], meta['hot_until']), (200, self.deadline))

    def test_hot_admission_displaces_nonhot_regardless_of_date_but_deep_pages_cannot(self):
        for index in range(16):
            self.put(str(index), priority=100 + index)
        self.cache.get(key('0'), hot_until=self.deadline)
        self.assertFalse(self.cache.can_admit(101, size=512))
        self.assertFalse(self.put('deep', priority=101, background=True))
        self.assertTrue(self.cache.can_admit(1, size=512, hot_until=self.deadline))
        self.assertTrue(self.put('view', priority=1, hot_until=self.deadline, background=True))
        self.assertTrue(self.path('0').exists())
        self.assertFalse(self.path('1').exists())
        self.assertTrue(self.put('newest', priority=999, background=True))
        self.assertTrue(self.path('view').exists())
        self.assertTrue(self.path('0').exists())
        self.assertLessEqual(sum(path.stat().st_size for path in self.cache.root.iterdir()), 65536)
        self.assertEqual(len(list(self.cache.root.glob('*.image'))), 16)

    def test_hot_expiry_returns_to_publication_order_without_expiring_the_image(self):
        for index in range(16):
            self.put(str(index), priority=100 + index, hot_until=self.deadline if index == 0 else 0)
        self.now[0] = self.deadline
        self.assertIsNotNone(self.cache.get(key('0')))
        self.assertEqual(self.hot_paths(), set())
        self.assertTrue(self.cache.can_admit(101, size=512))
        self.assertTrue(self.put('replacement', priority=101, background=True))
        self.assertFalse(self.path('0').exists())

    def test_hot_byte_quota_uses_physical_sizes_and_real_access_not_inventory_reads(self):
        self.cache.max_bytes, self.cache.max_files = 8192, 64
        for label in ('a', 'b', 'c'):
            self.put(label, hot_until=self.deadline, body=b'x' * 200)
            self.now[0] += 1
        self.assertEqual(self.hot_paths(), {self.path('b'), self.path('c')})
        before = {path: path.stat().st_atime_ns for path in self.cache.root.glob('*.image')}
        self.cache.can_admit(0, size=512)
        self.assertEqual(before, {path: path.stat().st_atime_ns for path in before})
        self.cache.get(key('a'))  # A real hit can reclaim a bounded hot slot.
        self.assertEqual(self.hot_paths(), {self.path('a'), self.path('c')})
        self.assertTrue(all(self.path(label).exists() for label in ('a', 'b', 'c')))

    def test_get_promotion_respects_hot_file_quota(self):
        self.cache.max_files = 8  # One protected file, even for tiny images.
        for index in range(8):
            self.put(str(index), priority=index)
        self.cache.get(key('1'), hot_until=self.deadline)
        self.now[0] += 1
        self.cache.get(key('0'), hot_until=self.deadline)
        self.assertEqual(self.hot_paths(), {self.path('0')})
        self.assertTrue(self.put('newest', priority=999, background=True))
        self.assertTrue(self.path('0').exists())
        self.assertFalse(self.path('1').exists())

    def test_invalid_and_oversized_hot_leases_fall_back_to_ordinary_policy(self):
        from reader_image_cache import image_hot_until
        for invalid in (None, 'bad', float('nan'), float('inf'), -1, 0, self.now[0] + 10):
            self.assertEqual(image_hot_until(invalid, self.now[0]), 0)
        self.assertEqual(image_hot_until(self.now[0] + 100000, self.now[0]), self.deadline)
        self.cache.max_bytes = 4096
        self.put('large', priority=100, hot_until=self.deadline, body=b'x' * 600)
        self.assertEqual(self.hot_paths(), set())
        self.assertFalse(self.cache.can_admit(0, size=4096, hot_until=self.deadline))
        self.assertFalse(self.put('old', body=b'x' * 3500, hot_until=self.deadline, background=True))
        for policy in ('no-store', 'private', 'no-cache'):
            self.cache.put(key(policy), b'x', {**HEADERS, 'cache-control': policy}, hot_until=self.deadline)
            self.assertFalse(self.path(policy).exists())


class ArticleImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from pathlib import Path
        self.cache = ImageCache(Path(self.temp.name) / 'images')
        self.secret = 'unit-only-key'
        self.path = signed_url('https://example.org/image.jpg', self.secret)[4:]

    def picture(self, kind='JPEG', size=(100, 80)):
        output = io.BytesIO()
        with Image.new('RGB', size, 'red') as image:
            image.save(output, format=kind)
        return output.getvalue()

    def metadata(self, label):
        return json.loads((self.cache.root / (key(label) + '.image')).read_bytes().split(b'\n')[0])

    def test_latest_reference_priority_never_demotes_and_old_background_cannot_churn(self):
        self.cache.max_files = 2
        self.cache.put(key('old'), b'x', HEADERS, priority=100, background=True)
        self.cache.put(key('new'), b'x', HEADERS, priority=300, background=True)
        self.cache.get(key('old'), priority=400)
        self.cache.get(key('old'), priority=50)
        self.cache.put(key('old'), b'x', HEADERS, priority=0)
        self.assertEqual(self.metadata('old')['priority'], 400)
        self.assertFalse(self.cache.can_admit(200))
        self.assertFalse(self.cache.put(key('too-old'), b'x', HEADERS, priority=200, background=True))
        self.assertIsNotNone(self.cache.get(key('new')))
        # Foreground clicks retain their normal ability to populate the cache.
        self.assertTrue(self.cache.put(key('click'), b'x', HEADERS))
        self.assertIsNotNone(self.cache.get(key('click')))
        self.assertIsNotNone(self.cache.get(key('old')))

    def test_newer_background_evicts_oldest_publication_not_recent_access(self):
        self.cache.max_files = 2
        self.cache.put(key('old'), b'x', HEADERS, priority=100)
        self.cache.put(key('new'), b'x', HEADERS, priority=300)
        self.cache.get(key('old'))
        self.assertTrue(self.cache.put(key('next'), b'x', HEADERS, priority=200, background=True))
        self.assertIsNone(self.cache.get(key('old')))
        self.assertIsNotNone(self.cache.get(key('new')))

    def test_expiry_reopens_background_admission_and_budget_includes_headers_and_temp(self):
        now = [100.0]
        self.cache.clock = lambda: now[0]
        self.cache.max_files = 1
        self.cache.put(key('one'), b'x', HEADERS, priority=100)
        self.assertFalse(self.cache.can_admit(50))
        now[0] = 161
        self.assertTrue(self.cache.can_admit(50))
        self.cache.max_bytes = 1100
        self.cache.max_files = 2
        (self.cache.root / '.pending-interrupted').write_bytes(b'z' * 2000)
        for label in ('a', 'b', 'c'):
            self.cache.put(key(label), b'x' * 250, HEADERS)
            self.assertLessEqual(sum(p.stat().st_size for p in self.cache.root.iterdir()), 1100)
        self.assertFalse(list(self.cache.root.glob('.pending-*')))

    def test_raw_warmer_and_browser_share_key_bytes_and_etag(self):
        body, calls = self.picture(), []
        def native(request):
            calls.append(request)
            self.assertEqual(request.headers['accept'], images.NATIVE_IMAGE_ACCEPT)
            return httpx.Response(200, content=body, headers=HEADERS)
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(native), **kwargs)
        with patch.object(images, 'image_cache', self.cache), patch.object(images, 'media_proxy_key', return_value=self.secret):
            warm = images.fetch_variant('http://native/mf', self.path, 0, 'image/*',
                client_factory=factory, priority=100, background=True)
            browser = images.original_cache_get('http://native/mf', self.path)
            self.assertEqual(warm.body, body)
            self.assertEqual(browser.body, body)
            self.assertEqual(warm.headers['etag'], browser.headers['etag'])
            self.assertEqual(len(calls), 1)
            self.assertIsNone(images.original_cache_get('http://native/mf', self.path, 'no-cache'))
            forged = self.path.replace(self.path.split('/')[1], 'A' * 43 + '=')
            self.assertIsNone(images.original_cache_get('http://native/mf', forged))

    def test_raw_put_native_status_policy_signature_and_public_cookie(self):
        body = self.picture()
        with patch.object(images, 'image_cache', self.cache), patch.object(images, 'media_proxy_key', return_value=self.secret):
            for status, data, policy in ((403, body, 'public'), (200, b'<html>error</html>', 'public'),
                    (200, body, 'no-store'), (200, body, 'private')):
                images.original_cache_put('http://native/mf', self.path, data,
                    {'content-type': 'image/jpeg', 'cache-control': policy}, status)
                self.assertIsNone(images.original_cache_get('http://native/mf', self.path))
            images.original_cache_put('http://native/mf', self.path, body,
                {**HEADERS, 'set-cookie': 'native-session=synthetic'}, 200)
            hit = images.original_cache_get('http://native/mf', self.path)
            self.assertEqual(hit.body, body)
            self.assertNotIn('set-cookie', hit.headers)

    def test_original_width_does_not_resize_and_supported_animation_validates(self):
        body = self.picture(size=(2000, 1000))
        self.assertEqual(images.resize_image(body, 'image/jpeg', 0), (body, 'image/jpeg'))
        output = io.BytesIO()
        with Image.new('RGB', (20, 20), 'red') as first, Image.new('RGB', (20, 20), 'blue') as second:
            first.save(output, format='GIF', save_all=True, append_images=[second], duration=50)
        gif = output.getvalue()
        self.assertTrue(images._is_cache_image(gif, 'image/gif'))
        self.assertEqual(images.resize_image(gif, 'image/gif', 0), (gif, 'image/gif'))
        self.assertFalse(images._is_cache_image(gif[:-10], 'image/gif'))
        self.assertFalse(images._is_cache_image(b'<svg/>', 'image/svg+xml'))
        self.assertFalse(images._is_cache_image(body[:-100], 'image/jpeg'))

    def test_avif_when_available_is_validated_as_original(self):
        Image.init()
        if 'AVIF' not in Image.SAVE:
            self.skipTest('existing Pillow has no AVIF codec')
        body = self.picture('AVIF')
        self.assertTrue(images._is_cache_image(body, 'image/avif'))
        self.assertEqual(images.resize_image(body, 'image/avif', 0)[0], body)

    def test_background_full_never_starts_native_and_nonimage_never_streams(self):
        self.cache.max_files = 1
        self.cache.put(key('protected'), b'x', HEADERS, priority=200)
        with self.assertRaises(images.BackgroundCacheFull):
            images.fetch_variant('http://native/mf', self.path, 0, 'image/*',
                client_factory=lambda **_: self.fail('full cache initiated network'), cache=self.cache,
                signature_key=self.secret, priority=100, background=True)
        class ForbiddenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise AssertionError('nonimage body streamed')
                yield b''
        def factory(**kwargs):
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
                200, headers={'content-type': 'video/mp4'}, stream=ForbiddenStream())), **kwargs)
        self.cache.max_files = 2
        response = images.fetch_variant('http://native/mf', self.path, 0, 'image/*',
            client_factory=factory, cache=self.cache, signature_key=self.secret, priority=300, background=True)
        self.assertEqual(response.status_code, 415)
