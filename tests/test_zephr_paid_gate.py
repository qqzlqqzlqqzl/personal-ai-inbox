"""Reconstructed from the coordinator's public dynamic DOM observation.

Source: https://www.theverge.com/tech/1003034/meta-vr-glasses-vs-augmented-reality
Observed tokens: #zephr-inline-body directly under #zephr-inline-container;
#zephr-footer-body. Notices below are supplied by the coordinator, not this
worker's independent browser capture. Wrapper paragraphs and negative controls
are synthetic. No article body, reading history, credentials or HTTP is used.
"""
import json
import unittest

from content_quality import assess, meaningful_short

URL = 'https://www.theverge.com/tech/1003034/meta-vr-glasses-vs-augmented-reality'
INLINE_TEXT = 'Subscribe to The Verge to continue reading.'
FOOTER_TEXT = 'Continue reading with a Verge subscription'
PRICE_TEXT = 'Unlock unlimited access to The Verge for just $2.'
INLINE = '<div id="zephr-inline-container"><div id="zephr-inline-body"><p>' + INLINE_TEXT + '</p></div></div>'
FOOTER = '<div id="zephr-footer-body"><p>' + FOOTER_TEXT + '</p><p>' + PRICE_TEXT + '</p></div>'


def schema(flag):
    return '<script type="application/ld+json">' + json.dumps({
        '@type': 'NewsArticle', 'url': URL, 'isAccessibleForFree': flag}) + '</script>'


def check(body, state='available'):
    return assess(url=URL, html_body=body, extraction_state=state, observed_at=123)


class ZephrGateTests(unittest.TestCase):
    def test_observed_inline_footer_and_combined_notices_are_paid_subscription(self):
        for body in (INLINE, FOOTER, INLINE + FOOTER):
            with self.subTest(body=body):
                result = check(body)
                self.assertEqual(result['access'], 'paid_subscription')
                self.assertFalse(result['recommendation_eligible'])
                self.assertIn('explicit_paid_gate', result['reason_codes'])
                self.assertNotIn('$2', json.dumps(result))
                self.assertNotIn('price', result)

    def test_plain_advertising_footer_price_and_quoted_notices_do_not_establish_gate(self):
        for body in (
            '<article>Full public content.</article><footer>' + PRICE_TEXT + '</footer>',
            '<div id="zephr-footer-body">' + PRICE_TEXT + '</div>',
            '<article>We discuss the phrase ' + INLINE_TEXT + '</article>',
            '<aside>' + INLINE_TEXT + ' ' + FOOTER_TEXT + '</aside>',
            '<div id="zephr-inline-container"><div id="zephr-inline-body">Subscribe to our newsletter.</div></div>',
            '<div id="zephr-footer-body">Subscribe to The Verge for updates.</div>',
        ):
            with self.subTest(body=body):
                result = check(body)
                self.assertEqual(result['access'], 'unknown')
                self.assertTrue(result['recommendation_eligible'])
                self.assertNotIn('explicit_paid_gate', result['reason_codes'])

    def test_inline_id_requires_observed_parent_and_ids_are_not_guessed_from_classes(self):
        for body in (
            '<div id="zephr-inline-body">' + INLINE_TEXT + '</div>',
            '<div id="other-container"><div id="zephr-inline-body">' + INLINE_TEXT + '</div></div>',
            '<div class="zephr-inline-container"><div class="zephr-inline-body">' + INLINE_TEXT + '</div></div>',
            '<div class="zephr-footer-body">' + FOOTER_TEXT + '</div>',
        ):
            with self.subTest(body=body):
                self.assertEqual(check(body)['access'], 'unknown')
                self.assertTrue(check(body)['recommendation_eligible'])

    def test_free_offer_in_same_notice_remains_distinct(self):
        body = INLINE.replace(INLINE_TEXT, INLINE_TEXT + ' Subscribe for free.')
        result = check(body)
        self.assertEqual(result['access'], 'login_required')
        self.assertIsNone(result['recommendation_eligible'])

    def test_unrelated_free_newsletter_does_not_cancel_article_gate(self):
        body = INLINE.replace('</div></div>', '<p>Subscribe to our newsletter for free.</p></div></div>')
        result = check(body)
        self.assertEqual(result['access'], 'paid_subscription')
        self.assertFalse(result['recommendation_eligible'])

    def test_hidden_observed_shapes_do_not_establish_gate(self):
        for body in (INLINE.replace('id="zephr-inline-container"', 'id="zephr-inline-container" hidden'),
                     FOOTER.replace('id="zephr-footer-body"', 'id="zephr-footer-body" style="display:none"')):
            with self.subTest(body=body):
                result = check(schema(True) + body)
                self.assertEqual(result['access'], 'public')
                self.assertTrue(result['recommendation_eligible'])

    def test_publisher_nonfree_plus_gate_is_paid_but_metadata_alone_stays_pending(self):
        result = check(schema(False) + INLINE)
        self.assertEqual(result['access'], 'paid_subscription')
        self.assertEqual(result['reason_codes'], ['explicit_paid_gate'])
        self.assertFalse(result['recommendation_eligible'])
        original = check(schema(False) + '<article>Public preview.</article>')
        self.assertEqual(original['access'], 'unknown')
        self.assertEqual(original['reason_codes'], ['publisher_nonfree_pending_review'])

    def test_public_or_conflicting_schema_with_visible_gate_keeps_conflict_semantics(self):
        for metadata in (schema(True), schema(True) + schema(False)):
            with self.subTest(metadata=metadata):
                result = check(metadata + INLINE + FOOTER)
                self.assertEqual(result['access'], 'unknown')
                self.assertEqual(result['reason_codes'], ['conflicting_access_evidence'])
                self.assertFalse(result['recommendation_eligible'])

    def test_public_article_with_price_ad_stays_public(self):
        result = check(schema(True) + '<article>Complete public text.</article><div id="zephr-footer-body">' + PRICE_TEXT + '</div>')
        self.assertEqual(result['access'], 'public')
        self.assertTrue(result['recommendation_eligible'])

    def test_failed_extraction_and_existing_short_change_exception_are_preserved(self):
        result = check(INLINE + FOOTER, state='failed')
        self.assertEqual(result['access'], 'unknown')
        self.assertIsNone(result['recommendation_eligible'])
        self.assertTrue(meaningful_short('http: fix request smuggling by rejecting conflicting Content-Length and Transfer-Encoding headers (#456)'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
