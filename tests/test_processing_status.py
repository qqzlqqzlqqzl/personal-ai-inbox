import copy
import unittest
from unittest.mock import patch

from processing_status import LANES, project


def evidence(**changes):
    result = {'local_observed_at': 100, 'pause_exists': False,
              'configs': {key: {'schedule_enabled': False, 'reconcile_only': False} for key in LANES},
              'scheduler': {'at': 100, 'state': 'schedule_disabled'}, 'submission_unknown_count': 5}
    result.update(changes)
    return result


class ProcessingTests(unittest.TestCase):
    def test_current_disabled_condition_keeps_five_unknown_batches(self):
        data = evidence()
        original = copy.deepcopy(data)
        result = project(entry_state='pending', evidence=data, now=105)
        self.assertEqual(result['reason_code'], 'schedule_disabled')
        self.assertEqual(result['reason_codes'], ['schedule_disabled', 'submission_unknown'])
        self.assertFalse(result['stale'])
        self.assertEqual(data, original)

    def test_absent_pause_marker_is_not_pause(self):
        self.assertNotEqual(project(entry_state='pending', evidence=evidence(), now=105)['reason_code'], 'paused')

    def test_pause_marker_prevents_queued_claim(self):
        result = project(entry_state='pending', evidence=evidence(pause_exists=True), now=105)
        self.assertEqual(result['reason_code'], 'paused')

    def test_missing_config_is_unknown_without_fresh_scheduler(self):
        result = project(entry_state='pending', evidence=evidence(configs={}, scheduler={}), now=105)
        self.assertEqual(result['reason_code'], 'unknown')
        self.assertTrue(result['stale'])

    def test_integer_zero_does_not_count_as_boolean_disabled(self):
        result = project(entry_state='pending', evidence=evidence(configs={key: {'schedule_enabled': 0} for key in LANES}, scheduler={}), now=105)
        self.assertEqual(result['reason_code'], 'unknown')

    def test_string_false_does_not_count_as_disabled(self):
        result = project(entry_state='pending', evidence=evidence(configs={key: {'schedule_enabled': 'false'} for key in LANES}, scheduler={}), now=105)
        self.assertEqual(result['reason_code'], 'unknown')

    def test_stale_cache_does_not_claim_current_processing(self):
        result = project(entry_state='pending', evidence=evidence(local_observed_at=0, scheduler={'at': 0, 'state': 'processing'}), now=1000)
        self.assertEqual(result['reason_code'], 'unknown')
        self.assertTrue(result['stale'])

    def test_completed_article_is_not_shown_waiting(self):
        result = project(entry_state='done', evidence=evidence(analyzed_at=90), now=105)
        self.assertEqual(result['reason_codes'], ['complete'])

    def test_global_unknown_does_not_claim_entry_submission(self):
        result = project(entry_state='pending', evidence=evidence(configs={}, scheduler={}), now=105)
        self.assertEqual(result['reason_code'], 'unknown')
        self.assertIn('submission_unknown', result['reason_codes'])

    def test_entry_claim_unknown_has_precise_reason_when_not_stopped(self):
        result = project(entry_state='waiting_model', evidence=evidence(configs={}, scheduler={}, entry_claim_submission_unknown=True), now=105)
        self.assertEqual(result['reason_code'], 'submission_unknown')

    def test_fetch_failure_is_not_low_value(self):
        result = project(entry_state='fetch_error', evidence=evidence(), now=105)
        self.assertEqual(result['reason_code'], 'extraction_failed')
        self.assertIn('不代表文章价值低', result['message'])

    def test_source_review_is_not_paid(self):
        result = project(entry_state='requires_fulltext_adapter', evidence=evidence(), now=105)
        self.assertEqual(result['reason_code'], 'source_review_required')
        self.assertNotIn('付费', result['message'])

    def test_retry_time_is_only_shown_for_fresh_cooldown(self):
        result = project(entry_state='pending', evidence=evidence(configs={}, scheduler={'state': 'waiting_for_item_retry', 'at': 100, 'next_retry_at': 200}), now=105)
        self.assertEqual(result['next_retry_at'], 200)

    def test_no_io_side_effects(self):
        with patch('builtins.open', side_effect=AssertionError('no file writes or reads')), patch('subprocess.run', side_effect=AssertionError('no provider/service command')), patch('socket.socket', side_effect=AssertionError('no network')):
            self.assertEqual(project(entry_state='pending', evidence=evidence(), now=105)['reason_code'], 'schedule_disabled')

    def test_enabled_current_config_does_not_inherit_old_disabled_snapshot(self):
        configs = {key: {'schedule_enabled': True, 'reconcile_only': False} for key in LANES}
        result = project(entry_state='pending', evidence=evidence(configs=configs), now=105)
        self.assertEqual(result['reason_code'], 'unknown')
        self.assertIn('state_evidence_conflict', result['reason_codes'])
        self.assertTrue(result['stale'])

    def test_reserve_gate_is_not_claimed_to_be_zero_quota(self):
        configs = {key: {'schedule_enabled': True, 'reconcile_only': False} for key in LANES}
        snapshot = {'at': 100, 'state': 'no_healthy_lane', 'lanes': {key: {'quota_gate': {'state': 'quota_reserved', 'allowed': False}} for key in LANES}}
        result = project(entry_state='pending', evidence=evidence(configs=configs, scheduler=snapshot), now=105)
        self.assertEqual(result['reason_code'], 'quota_reserved')
        self.assertNotIn('耗尽', result['message'])

    def test_mixed_unknown_quota_is_not_claimed_exhausted(self):
        configs = {key: {'schedule_enabled': True, 'reconcile_only': False} for key in LANES}
        snapshot = {'at': 100, 'state': 'no_healthy_lane', 'lanes': {key: {'quota_gate': {'state': 'quota_reserved', 'allowed': False}} for key in LANES}}
        snapshot['lanes']['third']['quota_gate']['state'] = 'quota_unknown'
        result = project(entry_state='pending', evidence=evidence(configs=configs, scheduler=snapshot), now=105)
        self.assertEqual(result['reason_code'], 'quota_unknown')

    def test_malformed_scheduler_state_does_not_raise_or_claim_healthy(self):
        result = project(entry_state='pending', evidence=evidence(configs={}, scheduler={'at':100,'state':{'bad':'shape'}}), now=105)
        self.assertEqual(result['reason_code'], 'unknown')


if __name__ == '__main__':
    unittest.main(verbosity=2)
