import unittest
from scoring_policy import PROMPT, add_priority


class PriorityTests(unittest.TestCase):
    def test_boundaries_and_original_scores_preserved(self):
        for score, label in [(0, '略过'), (3.9, '略过'), (4, '备选'),
                             (6.9, '备选'), (7, '优先看'), (8.3, '优先看'), (10, '优先看')]:
            original=dict(score=score, reason='正文中的具体理由', worth_reading=False,
                          technical_score=9, business_score=1)
            result=add_priority(original, PROMPT)
            self.assertEqual(result['score'], score)
            self.assertEqual(result['reading_priority'], label)
            self.assertEqual(result['reason'], original['reason'])
            self.assertEqual(result['technical_score'], 9)
            self.assertEqual(result['business_score'], 1)
            self.assertEqual(result['worth_reading'], score>=7)
            self.assertNotIn('reading_priority', original)

    def test_legacy_unaffected(self):
        original=dict(score=6.8, reason='旧结果', worth_reading=True)
        self.assertEqual(add_priority(original, 'old prompt'), original)


if __name__ == '__main__':
    unittest.main()
