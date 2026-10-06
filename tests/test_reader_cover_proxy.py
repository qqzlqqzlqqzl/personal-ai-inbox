import base64
import copy
import hashlib
import hmac
import unittest

from reader_cover_proxy import selected_cover_proxy, stored_cover_proxy, verified_proxy_target


NATIVE = "http://127.0.0.1:8091/mf"
COVER = "https://images.example.org/selected.jpg?size=large&v=2"


def signed(url):
    # Synthetic native-shaped fixture, never a production key or signing service.
    digest = hmac.new(b"test-only-native-key", url.encode(), hashlib.sha256).digest()
    return "/mf/proxy/" + base64.urlsafe_b64encode(digest).decode() + "/" + base64.urlsafe_b64encode(url.encode()).decode()


class CoverProxyTests(unittest.TestCase):
    def match(self, entry, cover=COVER, **kwargs):
        return selected_cover_proxy(entry, cover, native_base=NATIVE, **kwargs)

    def test_selected_native_image_preserves_input(self):
        proxy = signed(COVER)
        entry = {"content": f'<img src="{proxy}">', "enclosures": []}
        before = copy.deepcopy(entry)
        self.assertEqual(self.match(entry), proxy)
        self.assertEqual(entry, before)

    def test_different_image_or_target_query_is_not_a_substitute(self):
        for other in ("https://images.example.org/other.jpg", COVER.replace("v=2", "v=3")):
            with self.subTest(other=other):
                self.assertIsNone(self.match({"content": f'<img src="{signed(other)}">',
                    "enclosures": [{"mime_type": "image/jpeg", "url": signed(other)}]}))

    def test_missing_native_signature_keeps_raw_fallback(self):
        self.assertIsNone(self.match({"content": f'<img src="{COVER}">',
            "enclosures": [{"mime_type": "image/jpeg", "url": COVER}]}))
        self.assertIsNone(self.match({"content": ""}))

    def test_native_absolute_origins_normalize_to_relative_proxy(self):
        proxy = signed(COVER)
        for origin in (NATIVE, "http://127.0.0.1:8092/mf", "https://reader.example.org/mf"):
            with self.subTest(origin=origin):
                self.assertEqual(self.match({"content": f'<img src="{origin}{proxy[3:]}">'},
                    reader_base="https://reader.example.org/mf"), proxy)

    def test_foreign_proxy_origin_is_not_reused(self):
        proxy = signed(COVER)
        for prefix in ("https://external.example.org", "//external.example.org", "http://127.0.0.1:8091.evil"):
            with self.subTest(prefix=prefix):
                self.assertIsNone(self.match({"content": f'<img src="{prefix}{proxy}">'}))

    def test_matching_image_enclosure_only(self):
        proxy = signed(COVER)
        entry = {"enclosures": [{"mime_type": "image/jpeg", "url": signed("https://example.org/other.jpg")},
            {"mime_type": "image/webp", "url": proxy}]}
        self.assertEqual(self.match(entry), proxy)
        entry["enclosures"][1]["mime_type"] = "audio/mpeg"
        self.assertIsNone(self.match(entry))

    def test_picture_srcset_image_srcset_and_video_poster(self):
        proxy = signed(COVER)
        for html in (f'<picture><source srcset="{proxy} 960w"></picture>',
                     f'<img srcset="{signed("https://example.org/other.jpg")} 480w, {proxy} 960w">',
                     f'<video poster="{proxy}"></video>'):
            with self.subTest(html=html):
                self.assertEqual(self.match({"content": html}), proxy)
        self.assertIsNone(self.match({"content": f'<source src="{proxy}"><a href="{proxy}">link</a>'}))

    def test_already_signed_cover_still_requires_current_entry_match(self):
        proxy = signed(COVER)
        self.assertEqual(self.match({"content": f'<img src="{proxy}">'}, NATIVE + proxy[3:]), proxy)
        self.assertIsNone(self.match({"content": ""}, proxy))

    def test_invalid_wrapper_and_scheme_are_not_forwarded(self):
        proxy = signed(COVER)
        malformed = [proxy + "?reader_width=480", proxy + "#fragment", proxy.replace("/mf/", "/else/"),
            "/mf/proxy/" + "A" * 43 + "=/bad", proxy.replace("=/", "/"),
            "http://user:secret@127.0.0.1:8091" + proxy]
        for url in malformed:
            with self.subTest(url=url):
                self.assertIsNone(self.match({"content": f'<img src="{url}">'}))
        for url in ("javascript:alert(1)", "data:image/png;base64,AA==", "file:///tmp/image.jpg",
                    "https://user:secret@example.org/image.jpg", "https://[invalid"):
            with self.subTest(target=url):
                self.assertIsNone(self.match({"content": f'<img src="{signed(url)}">'}, url))

    def test_empty_or_non_string_cover_is_unknown(self):
        for cover in (None, "", False, 123):
            with self.subTest(cover=cover):
                self.assertIsNone(self.match({"content": f'<img src="{signed(COVER)}">'}, cover))

    def test_bound_stored_cover_can_be_signed_without_native_image(self):
        entry = dict(id=7, user_id=2, url='https://example.org/article', content='')
        row = dict(entry_id=7, user_id=2, url=entry['url'], cover_url=COVER)
        self.assertEqual(stored_cover_proxy(entry, COVER, row, 2, 'test-only-native-key', native_base=NATIVE), signed(COVER))

    def test_stored_cover_identity_and_selected_url_must_all_match(self):
        entry = dict(id=7, user_id=2, url='https://example.org/article')
        row = dict(entry_id=7, user_id=2, url=entry['url'], cover_url=COVER)
        for changes in (dict(entry_id=8), dict(user_id=3), dict(url='https://example.org/changed'), dict(cover_url=COVER+'x')):
            with self.subTest(changes=changes):
                self.assertIsNone(stored_cover_proxy(entry, COVER, {**row, **changes}, 2,
                                                    'test-only-native-key', native_base=NATIVE))
        self.assertIsNone(stored_cover_proxy(entry, COVER, row, 3, 'test-only-native-key', native_base=NATIVE))
        self.assertIsNone(stored_cover_proxy(entry, COVER, row, 2, None, native_base=NATIVE))

    def test_stored_private_or_non_http_media_is_not_signed(self):
        entry = dict(id=7, user_id=2, url='https://example.org/article')
        for cover in ('http://127.0.0.1/private', 'http://169.254.169.254/latest', 'http://localhost/image',
                      'http://[::1]/image', 'file:///tmp/image', 'https://user:pass@example.org/image'):
            with self.subTest(cover=cover):
                row = dict(entry_id=7, user_id=2, url=entry['url'], cover_url=cover)
                self.assertIsNone(stored_cover_proxy(entry, cover, row, 2, 'test-only-native-key', native_base=NATIVE))

    def test_cache_signature_requires_current_key_and_exact_target(self):
        path = signed(COVER)[4:]
        self.assertEqual(verified_proxy_target(path, 'test-only-native-key'), COVER)
        self.assertIsNone(verified_proxy_target(path, 'rotated-key'))
        self.assertIsNone(verified_proxy_target(path, None))
        other_target = base64.urlsafe_b64encode(b'https://example.org/other').decode()
        self.assertIsNone(verified_proxy_target(path.rsplit('/', 1)[0] + '/' + other_target, 'test-only-native-key'))


if __name__ == "__main__":
    unittest.main()
