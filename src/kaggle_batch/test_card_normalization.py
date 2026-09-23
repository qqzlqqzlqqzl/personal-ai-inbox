import unittest
from validate_business import complete_summary

class CompleteSummaryTests(unittest.TestCase):
    def test_drop_cutoff_tail_without_rewriting_complete_claims(self):
        self.assertEqual('团队重建了差异视图。',complete_summary('团队重建了差异视图。一个 ope……'))
    def test_complete_text_and_internal_ellipsis_are_unchanged(self):
        for text in ('点击查看原文','耗时减少33%。','作者说……仍在测试。'):
            self.assertEqual(text,complete_summary(text))
    def test_all_incomplete_output_is_rejected(self):
        with self.assertRaises(ValueError):
            complete_summary('一个 ope……')

if __name__=='__main__':
    unittest.main()
