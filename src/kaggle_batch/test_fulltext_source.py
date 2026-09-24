import unittest
from fulltext_source import extract,rule_for,FulltextUnavailable

class FulltextTests(unittest.TestCase):
    def test_article_includes_late_sections_code_tables_and_captions(self):
        body='<article class="post-content"><p>'+('Opening. '*20)+'</p><pre>x = 1\ny = 2</pre><table><tr><td>42 ms</td></tr></table><figure><img src="x"><figcaption>measured output</figcaption></figure><p>Final paragraph.</p></article>'
        result=extract('<nav>Wrong article</nav>'+body+'<footer>Subscribe</footer>','https://karpathy.github.io/a')
        self.assertIn('x = 1\ny = 2',result['source_text'])
        self.assertIn('42 ms',result['source_text'])
        self.assertIn('measured output',result['source_text'])
        self.assertTrue(result['source_text'].endswith('Final paragraph.'))
        self.assertNotIn('Wrong article',result['source_text'])
        self.assertEqual(1,result['image_count'])

    def test_missing_ambiguous_and_nested_containers_are_rejected(self):
        for html in ['<main>'+('RSS excerpt '*50)+'</main>',
                     '<div class="post">'+('body '*50)+'</div><div class="post">other</div>']:
            with self.assertRaises(FulltextUnavailable):
                extract(html,'https://blog.rust-lang.org/article')
        with self.assertRaises(FulltextUnavailable):
            extract('<div class="duet--article--article-body-component"><div class="duet--article--article-body-component">'+('body '*50)+'</div></div>','https://www.theverge.com/article')

    def test_highlighted_code_is_not_split_with_inserted_spaces(self):
        result=extract('<article class="post-content"><p>'+('Article '*30)+'</p><pre><code><span>print</span><span>(</span><span>f</span><span>"hello"</span><span>)</span>\n    x=1</code></pre></article>','https://karpathy.github.io/a')
        self.assertIn('print(f"hello")\n    x=1',result['source_text'])

    def test_repeated_components_preserve_order_including_short_last_paragraph(self):
        prefix='<div class="duet--article--article-body-component">'
        result=extract(prefix+('First '*30)+'</div><aside>sidebar</aside>'+prefix+'End.</div>','https://www.theverge.com/article')
        self.assertEqual(2,result['receipt']['components'])
        self.assertTrue(result['source_text'].endswith('End.'))
        self.assertNotIn('sidebar',result['source_text'])

    def test_reading_card_event_unknown_host_and_unsafe_url_do_not_fallback(self):
        for url in ['https://ursb.me/reading/123','https://www.technologyreview.com/2026/roundtables-test/',
                    'https://unknown.example/article','file:///article','https://user@go.dev/blog/a',
                    'http://go.dev:8080/blog/a']:
            with self.subTest(url=url),self.assertRaises(FulltextUnavailable):
                rule_for(url)

    def test_newsletter_and_share_ui_removed_but_stock_update_retained(self):
        result=extract('<div id="article-body"><div id="utility-bar">Share and subscribe</div><p>Now out of stock.</p><p>'+('Article '*30)+'</p><div class="slice-container-newsletterForm">newsletter</div></div>','https://www.tomshardware.com/article')
        self.assertIn('Now out of stock.',result['source_text'])
        self.assertNotIn('subscribe',result['source_text'])
        self.assertNotIn('newsletter',result['source_text'])

if __name__=='__main__':
    unittest.main()
