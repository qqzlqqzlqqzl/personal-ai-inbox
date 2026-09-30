import unittest
from fulltext_source import extract_reader,FulltextUnavailable
class ReaderTests(unittest.TestCase):
    def document(self,url,body):return 'Title: Example article\n\nURL Source: '+url+'\n\nMarkdown Content:\n'+body
    def test_rejects_challenge_even_with_200_content(self):
        with self.assertRaises(FulltextUnavailable):extract_reader('Title: Just a moment...','https://openai.com/index/a')
    def test_source_identity_must_match(self):
        with self.assertRaises(FulltextUnavailable):extract_reader(self.document('https://openai.com/index/b','Words '*200),'https://openai.com/index/a')
    def test_keeps_complete_openai_body(self):
        url='https://openai.com/index/a';body='Sentence with evidence. '*100
        self.assertEqual(body.strip(),extract_reader(self.document(url,body),url)['source_text'])
    def test_reviewed_nordic_and_googledev_reader_hosts_are_allowed(self):
        for url in ['https://www.nordicsemi.com/Nordic-news/a','https://nordicsemi.com/Nordic-news/a',
                    'https://developers.googleblog.com/en/a/','https://www.anthropic.com/engineering/a']:
            body='Reviewed publisher article sentence. '*40
            self.assertIn('Reviewed publisher article',extract_reader(self.document(url,body),url)['source_text'])
    def test_reader_rejects_security_verification_body(self):
        url='https://www.nordicsemi.com/Nordic-news/a'
        raw=self.document(url,'## Performing security verification\nThis website uses a security service to protect against malicious bots. '*20)
        with self.assertRaises(FulltextUnavailable):extract_reader(raw,url)
    def test_ars_excludes_author_and_recommendations(self):
        url='https://arstechnica.com/tech/2026/09/a/';body='Article sentence. '*100
        raw=self.document(url,body+'\n\n[![Image 1: Photo](https://cdn.example/a.jpg)](https://arstechnica.com/author/name/)\nBiography and unrelated stories')
        self.assertEqual(body.strip(),extract_reader(raw,url)['source_text'])
    def test_ars_missing_boundary_is_not_silently_accepted(self):
        url='https://arstechnica.com/tech/2026/09/a/'
        with self.assertRaises(FulltextUnavailable):extract_reader(self.document(url,'words '*200),url)
if __name__=='__main__':unittest.main()
