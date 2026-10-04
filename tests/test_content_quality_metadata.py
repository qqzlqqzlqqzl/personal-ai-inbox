"""Synthetic shape of the coordinator-observed mixed VR access signal.

No historical account body is copied. These fields alone must not assert a
reproduced visible payment demand or an established current access restriction.
"""
import unittest
import json
from pathlib import Path
from content_quality import assess


class MetadataReviewTests(unittest.TestCase):
    def test_actual_public_metadata_fields_exclude_without_fabricated_payment_prompt(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/publisher-nonfree-statements.json').read_text())
        self.assertFalse(fixture['visible_payment_prompt_observed'])
        for item in fixture['statements']:
            with self.subTest(canonical=item['canonical']):
                body='<link rel="canonical" href="'+item['canonical']+'"><script type="application/ld+json">'+json.dumps(item['article'])+'</script><article>Synthetic visible passage; no copied article body.</article>'
                result=assess(url=item['canonical'],html_body=body,extraction_state='available')
                self.assertFalse(result['recommendation_eligible'])
                self.assertEqual(result['access'],'unknown')
                self.assertIn('publisher_nonfree_pending_review',result['reason_codes'])

    def test_document_canonical_mismatch_does_not_bind_nonfree_statement(self):
        body='<link rel="canonical" href="https://publisher.example/other"><script type="application/ld+json">{"@type":"NewsArticle","url":"https://publisher.example/tech/example","isAccessibleForFree":false}</script><article>Current article.</article>'
        result=assess(url='https://publisher.example/tech/example',html_body=body,extraction_state='available')
        self.assertTrue(result['recommendation_eligible'])

    def test_nonfree_metadata_excludes_pending_review_without_claiming_paid_gate(self):
        body = '<script type="application/ld+json">{"@type":"NewsArticle","url":"https://publisher.example/tech/example","isAccessibleForFree":false}</script><article>Publicly visible passage with no payment demand.</article>'
        result = assess(url='https://publisher.example/tech/example', html_body=body, extraction_state='available')
        self.assertEqual(result['access'], 'unknown')
        self.assertFalse(result['recommendation_eligible'])
        self.assertIn('publisher_nonfree_pending_review', result['reason_codes'])

    def test_free_looking_preview_does_not_override_explicit_paid_gate(self):
        body = '<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script><article>Public preview.</article><div class="paywall">This article is for paid subscribers only.</div>'
        result = assess(url='https://publisher.example/tech/example', html_body=body, extraction_state='available')
        self.assertFalse(result['recommendation_eligible'])

    def test_missing_article_identity_does_not_establish_nonfree_current_article(self):
        body = '<script type="application/ld+json">{"@type":"NewsArticle","isAccessibleForFree":false}</script><article>Current article.</article>'
        result = assess(url='https://publisher.example/tech/example', html_body=body, extraction_state='available')
        self.assertTrue(result['recommendation_eligible'])
        self.assertNotIn('publisher_nonfree_pending_review', result['reason_codes'])

    def test_nested_product_article_cannot_establish_nonfree_current_article(self):
        body = '<script type="application/ld+json">{"@type":"Product","subjectOf":{"@type":"NewsArticle","url":"https://publisher.example/tech/example","isAccessibleForFree":false}}</script><article>Current article discusses the product.</article>'
        result = assess(url='https://publisher.example/tech/example', html_body=body, extraction_state='available')
        self.assertTrue(result['recommendation_eligible'])

    def test_graph_recommendation_with_other_canonical_cannot_block_public_article(self):
        body = '<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","url":"https://publisher.example/tech/example","isAccessibleForFree":true},{"@type":"NewsArticle","url":"https://publisher.example/other","isAccessibleForFree":false}]}</script><article>Current public article.</article>'
        result = assess(url='https://publisher.example/tech/example', html_body=body, extraction_state='available')
        self.assertEqual(result['access'], 'public')
        self.assertTrue(result['recommendation_eligible'])

    def test_main_entity_of_page_can_bind_article(self):
        body = '<script type="application/ld+json">{"@type":"NewsArticle","mainEntityOfPage":{"@id":"https://publisher.example/tech/example"},"isAccessibleForFree":false}</script><article>Preview.</article>'
        result = assess(url='https://publisher.example/tech/example', html_body=body, extraction_state='available')
        self.assertFalse(result['recommendation_eligible'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
