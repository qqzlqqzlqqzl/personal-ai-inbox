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

    def test_new_reviewed_site_rules_select_article_body(self):
        cases=[
            ('https://interrupt.memfault.com/blog/a','<article id="post-page"><div class="content"><p>'+('Memfault body '*20)+'</p></div></article>','Memfault body'),
            ('https://lilianweng.github.io/posts/a/','<article class="post-single"><div class="post-content"><div class="toc">TOC junk</div><p>'+('Lilian body '*20)+'</p></div></article>','Lilian body'),
            ('https://blog.python.org/2026/09/a/','<article class="prose"><nav>nav junk</nav><p>'+('Python body '*20)+'</p></article>','Python body'),
            ('https://www.nidec.com/cn/technology/casestudy/a/','<section id="main"><p>'+('Nidec body '*20)+'</p></section>','Nidec body'),
            ('https://analogdevicesinc.github.io/system-level/a/','<div id="top-anchor" class="bodywrapper"><div class="body"><p>'+('ADI body '*30)+'</p></div></div>','ADI body'),
        ]
        for url,raw,needle in cases:
            with self.subTest(url=url):
                result=extract(raw,url)
                self.assertIn(needle,result['source_text'])

    def test_deepmind_both_layouts_are_supported(self):
        modern='<article class="uni-article-wrapper"><p>'+('Modern DeepMind body '*20)+'</p></article>'
        legacy='<main id="page-content"><section class="grid section-default"><div class="rich-text">'+('Legacy A '*20)+'</div></section><section class="grid section-default"><div class="rich-text">'+('Legacy B '*20)+'</div></section></main>'
        self.assertIn('Modern DeepMind body',extract(modern,'https://deepmind.google/blog/a/')['source_text'])
        result=extract(legacy,'https://deepmind.google/blog/b/')
        self.assertEqual(2,result['receipt']['components'])
        self.assertIn('Legacy A',result['source_text']);self.assertIn('Legacy B',result['source_text'])

    def test_openai_reviewed_reader_paths_are_allowed_but_forms_are_not(self):
        for url in ['https://openai.com/index/a','https://openai.com/academy/a','https://openai.com/global-affairs/a',
                    'https://openai.com/business/a','https://openai.com/signals/research/a','https://openai.com/openai-o1-contributions']:
            self.assertEqual('@reader',rule_for(url)[0])
        with self.assertRaises(FulltextUnavailable):
            rule_for('https://openai.com/form/stargate-infrastructure')

    def test_browser_and_feed_only_rules_are_explicit(self):
        self.assertEqual('@reader:nordic',rule_for('https://www.nordicsemi.com/Nordic-news/a')[0])
        self.assertEqual('@browser:.news-content .editor.heti',rule_for('https://www.oschina.net/news/1')[0])
        self.assertEqual('@feed:adafruit',rule_for('https://blog.adafruit.com/2026/09/25/a/')[0])
        self.assertEqual('@feed:netflix',rule_for('https://netflixtechblog.com/article-id?source=rss')[0])
        with self.assertRaises(FulltextUnavailable):
            extract('<div>not rendered</div>','https://www.oschina.net/news/1')
        with self.assertRaises(FulltextUnavailable):
            extract('<article>not feed content</article>','https://blog.adafruit.com/2026/09/25/a/')

    def test_reviewed_reader_sites_require_article_paths(self):
        allowed=[
            'https://embeddedartistry.com/blog/2023/06/12/a/',
            'https://www.technologyreview.com/2026/09/17/1144251/a/',
            'https://www.anthropic.com/engineering/a',
            'https://blog.csdn.net/user/article/details/149394084',
            'https://oshwhub.com/user/project_name',
            'https://jvns.ca/blog/2024/11/09/a/',
            'https://www.brandsninja.com/brands/kickstarter/id',
            'https://www.ruanx.net/llm-app-develop/',
            'https://engineering.fb.com/2026/08/03/ml-applications/a/',
            'https://dropbox.tech/infrastructure/a',
            'https://devblogs.microsoft.com/engineering-at-microsoft/a/',
            'https://www.renesas.com/en/blogs/a',
            'https://www.microsoft.com/en-us/research/blog/a/',
            'https://www.maxongroup.com/en/knowledge-and-support/blog/a-123',
            'https://github.blog/engineering/architecture-optimization/a/',
            'https://slack.engineering/a/',
            'http://bair.berkeley.edu/blog/2025/09/01/a/',
            'https://www.microchip.com/en-us/tools-resources/reference-designs/a',
            'https://customerstories.rainmaker.espressif.com/gravity',
            'https://spectrum.ieee.org/a',
            'https://sspai.com/post/114788',
            'https://sspai.com/prime/story/a',
        ]
        for url in allowed:
            with self.subTest(url=url):self.assertTrue(rule_for(url)[0].startswith('@reader'))
        for url in ['https://www.anthropic.com/','https://embeddedartistry.com/',
                    'https://blog.csdn.net/user','https://www.microsoft.com/en-us/']:
            with self.subTest(url=url),self.assertRaises(FulltextUnavailable):rule_for(url)

    def test_ursb_non_reading_content_paths_are_supported(self):
        cases=[
            ('https://ursb.me/posts/a/','<article id="postContent" class="post-content">'+('Post body '*30)+'</article>','Post body'),
            ('https://ursb.me/immersive/a/','<div class="container">'+('Immersive body '*30)+'</div>','Immersive body'),
            ('https://ursb.me/notes/a/','<article class="prose">'+('Notes body '*30)+'</article>','Notes body'),
            ('https://ursb.me/playbook/a/','<main id="exhibit">'+('Playbook body '*30)+'</main>','Playbook body'),
            ('https://blog.google/innovation-and-ai/a/','<article class="uni-article-wrapper">'+('Google body '*30)+'</article>','Google body'),
        ]
        for url,raw,needle in cases:
            with self.subTest(url=url):self.assertIn(needle,extract(raw,url)['source_text'])

if __name__=='__main__':
    unittest.main()
