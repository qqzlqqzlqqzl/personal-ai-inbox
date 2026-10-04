"""Synthetic policy tests and separate official public release-body fixtures.

These do not claim to test the actual production recommendation rows.
"""
import json
from pathlib import Path
import unittest

from content_quality import assess, visible_recommendation

URL = "https://publisher.example/article"


def schema(flag, **extra):
    return '<script type="application/ld+json">' + json.dumps({"@type": "Article", "url": URL, "isAccessibleForFree": flag, **extra}) + '</script>'


def check(body, **kwargs):
    return assess(url=URL, html_body=body, extraction_state="available", observed_at=123, **kwargs)


class AccessTests(unittest.TestCase):
    def test_paid_schema_is_excluded(self):
        result = check(schema(False) + '<article>Public preview.</article>')
        self.assertEqual((result['recommendation_eligible'], result['access']), (False, 'unknown'))
        self.assertIn('publisher_nonfree_pending_review', result['reason_codes'])

    def test_public_schema_preserves_article(self):
        result = check(schema(True) + '<article>Full public explanation.</article>')
        self.assertEqual((result['recommendation_eligible'], result['access']), (True, 'public'))

    def test_exact_contextual_paid_prompt(self):
        result = check('<article>Public preview</article><div class="paywall">Subscribe to continue reading.</div>')
        self.assertEqual((result['recommendation_eligible'], result['access']), (False, 'paid_subscription'))

    def test_price_is_not_emitted(self):
        result = check('<div class="subscriber-only">This article is for paid subscribers only. $2.</div>')
        self.assertFalse(result['recommendation_eligible'])
        self.assertNotIn('$2', json.dumps(result))
        self.assertNotIn('price', result)

    def test_unscoped_payment_discussion_does_not_exclude(self):
        result = check('<article>We compare paid products. The phrase "Subscribe to continue reading" is discussed critically.</article>')
        self.assertTrue(result['recommendation_eligible'])
        self.assertEqual(result['access'], 'unknown')

    def test_newsletter_form_does_not_exclude(self):
        result = check('<article>Complete public technical explanation.</article><aside>Subscribe to our newsletter.</aside>')
        self.assertTrue(result['recommendation_eligible'])

    def test_free_login_is_not_paid(self):
        result = check('<div class="paywall">Sign in to continue reading.</div>')
        self.assertEqual(result['access'], 'login_required')
        self.assertIsNone(result['recommendation_eligible'])

    def test_free_subscription_is_not_paid(self):
        result = check('<div class="paywall">Subscribe to continue reading. Subscribe for free.</div>')
        self.assertNotIn(result['access'], ('paid_fulltext', 'paid_subscription'))
        self.assertIsNot(result['recommendation_eligible'], False)

    def test_unrelated_article_schema_cannot_block(self):
        result = check(schema(False, url='https://publisher.example/other') + '<article>Free content.</article>')
        self.assertTrue(result['recommendation_eligible'])
        self.assertEqual(result['access'], 'unknown')

    def test_organization_schema_is_not_article(self):
        result = check(schema(False, **{'@type': 'Organization'}) + '<article>Free content.</article>')
        self.assertTrue(result['recommendation_eligible'])

    def test_malformed_schema_type_is_ignored(self):
        result = check(schema(False, **{'@type': [{'unexpected': 'object'}]}) + '<article>Free content.</article>')
        self.assertTrue(result['recommendation_eligible'])

    def test_string_false_is_not_boolean_evidence(self):
        result = check(schema('false') + '<article>Content.</article>')
        self.assertEqual(result['access'], 'unknown')

    def test_conflicting_schema_is_unknown(self):
        result = check(schema(False) + schema(True) + '<article>Content.</article>')
        self.assertEqual(result['access'], 'unknown')
        self.assertIn('conflicting_access_evidence', result['reason_codes'])
        self.assertFalse(result['recommendation_eligible'])

    def test_failed_extraction_not_low_information(self):
        result = assess(url=URL, html_body='<div class="paywall">Subscribe to continue reading</div>', extraction_state='failed')
        self.assertIsNone(result['recommendation_eligible'])
        self.assertEqual((result['access'], result['information']), ('unknown', 'unknown'))

    def test_unassessed_source_stays_unknown(self):
        result = assess(url=URL, text='A sentence')
        self.assertIsNone(result['recommendation_eligible'])

    def test_empty_html_is_not_substantive(self):
        result = check('<article><p></p></article>')
        self.assertIsNone(result['recommendation_eligible'])


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = json.loads((Path(__file__).parent/'fixtures/public-llama-release-samples.json').read_text())

    def test_each_official_release_is_separately_excluded(self):
        self.assertEqual({s['tag'] for s in self.samples}, {'b11385', 'b11384', 'b11378'})
        for sample in self.samples:
            with self.subTest(tag=sample['tag']):
                result = assess(url=sample['url'], text=sample['body'], extraction_state='available')
                self.assertEqual(result['information'], 'low_information')
                self.assertFalse(result['recommendation_eligible'])
                self.assertEqual(result['access'], 'unknown')

    def test_template_only_is_excluded(self):
        sample = self.samples[0]
        result = assess(url=sample['url'], text=sample['body'][sample['body'].index('**Website:**'):], extraction_state='available')
        self.assertIn('release_template_only', result['reason_codes'])

    def test_short_security_notice_survives(self):
        sample = self.samples[0]
        body = 'security : fix CVE-2026-12345 remote code execution (#456)\n\n' + sample['body'][sample['body'].index('**Website:**'):]
        result = assess(url=sample['url'], text=body, extraction_state='available')
        self.assertTrue(result['recommendation_eligible'])

    def test_short_actionable_notice_survives(self):
        sample = self.samples[0]
        body = 'Users must upgrade to 2.4 to prevent data loss (#456)\n\n' + sample['body'][sample['body'].index('**Website:**'):]
        self.assertTrue(assess(url=sample['url'], text=body, extraction_state='available')['recommendation_eligible'])

    def test_explanatory_short_release_survives(self):
        sample = self.samples[0]
        body = 'server: update preset validation (#456)\n\nA stale allow-list rejected valid models. This change accepts repository filenames and preserves validation.\n\n' + sample['body'][sample['body'].index('**Website:**'):]
        self.assertTrue(assess(url=sample['url'], text=body, extraction_state='available')['recommendation_eligible'])

    def test_domain_is_not_exclusion(self):
        self.assertTrue(assess(url='https://github.com/org/repo/releases/tag/v1', text='Clear technical explanation of the new API.', extraction_state='available')['recommendation_eligible'])

    def test_short_technical_article_is_not_length_filtered(self):
        self.assertTrue(check('<article>Use fsync before rename to preserve crash consistency.</article>')['recommendation_eligible'])

    def test_flattened_stored_release_text_keeps_download_template_detection(self):
        # core/worker store get_text(' ', strip=True), which loses hrefs and lines.
        text = 'common : add common_is_tty() helper and fix deprecated warnings on Windows (#29860) Signed-off-by: Example Maintainer Website: llama.app Attestations: artifact proof macOS/iOS: Apple Silicon Linux: Ubuntu CPU Android: CPU Windows: CPU UI: UI'
        result = assess(url='https://github.com/ggml-org/llama.cpp/releases/tag/b11378', text=text, extraction_state='available')
        self.assertFalse(result['recommendation_eligible'])
        self.assertEqual(result['information'], 'low_information')


class SelectionTests(unittest.TestCase):
    def test_null_false_true_correction_uses_same_selection_rule(self):
        kwargs = {'state': 'done', 'score': 8.1, 'minimum': 6}
        self.assertTrue(visible_recommendation(**kwargs, quality={'recommendation_eligible': None}))
        self.assertFalse(visible_recommendation(**kwargs, quality={'recommendation_eligible': False}))
        self.assertTrue(visible_recommendation(**kwargs, quality={'recommendation_eligible': True}))

    def test_pending_does_not_become_recommended(self):
        self.assertFalse(visible_recommendation(state='pending', score=8, minimum=6, quality={'recommendation_eligible': True}))

    def test_lower_user_threshold_is_kept(self):
        self.assertTrue(visible_recommendation(state='done', score=4.5, minimum=4, quality={'recommendation_eligible': True}))

    def test_record_is_bound_to_source_and_observation(self):
        paid = check(schema(False) + '<article>Preview.</article>')
        free = check(schema(True) + '<article>Complete public text.</article>')
        self.assertNotEqual(paid['source_sha256'], free['source_sha256'])
        self.assertFalse(paid['recommendation_eligible'])
        self.assertTrue(free['recommendation_eligible'])
        self.assertEqual(free, check(schema(True) + '<article>Complete public text.</article>'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
