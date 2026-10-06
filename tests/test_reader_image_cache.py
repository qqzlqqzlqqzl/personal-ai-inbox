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
