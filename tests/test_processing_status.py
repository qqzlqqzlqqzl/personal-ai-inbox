import copy
import unittest
from unittest.mock import patch

from processing_status import LANES, project, observe, for_entry
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile


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

    def test_quarantined_entry_keeps_exact_claim_reason_under_new_activity(self):
        configs = {key: {'schedule_enabled': True, 'reconcile_only': False} for key in LANES}
        data = evidence(configs=configs, submission_unknown_count=0,
                        scheduler={'at': 100, 'state': 'started'}, entry_claim_quarantined=True)
        for state in ('pending', 'ai_error', 'fetch_error', 'waiting_model', 'content_excluded'):
            with self.subTest(state=state):
                value = project(entry_state=state, evidence=data, now=105)
                self.assertEqual(value['reason_code'], 'submission_quarantined')
                self.assertTrue(value['claim_held'])
                self.assertIn('等待核实', value['message'])
                self.assertNotIn('完成', value['message'])
                self.assertIsNone(value['next_retry_at'])
        value = project(entry_state='done', evidence={**data, 'analyzed_at': 90}, now=105)
        self.assertEqual(value['reason_code'], 'complete')
        self.assertFalse(value['claim_held'])

    def test_control_pause_and_unknown_receipt_do_not_offer_retry(self):
        data = evidence(pause_exists=True, entry_claim_quarantined=True)
        value = project(entry_state='ai_error', evidence=data, now=105)
        self.assertEqual(value['reason_code'], 'paused')
        self.assertIn('submission_quarantined', value['reason_codes'])
        self.assertTrue(value['claim_held'])
        data = evidence(configs={}, scheduler={'at': 100, 'state': 'started'}, entry_claim_receipt_conflict=True)
        value = project(entry_state='ai_error', evidence=data, now=105)
        self.assertEqual(value['reason_code'], 'submission_unknown')
        self.assertTrue(value['claim_held'])

    def test_unavailable_ledgers_cannot_inherit_processing_from_scheduler(self):
        data = evidence(configs={}, scheduler={'at': 100, 'state': 'started'}, ledgers_complete=False)
        value = for_entry({'entry_id': 99, 'state': 'pending'}, data, now=105)
        self.assertEqual(value['reason_code'], 'unknown')
        self.assertTrue(value['stale'])
        self.assertIn('ledger_unavailable', value['reason_codes'])
        value = for_entry({'entry_id': 99, 'state': 'ai_error'}, data, now=105)
        self.assertEqual(value['reason_code'], 'unknown')
        self.assertFalse(value['claim_held'])  # Unknown is not proof of a held claim.

    def test_readonly_claim_projection_preserves_old_claims_and_new_entry_path(self):
        root = Path(tempfile.mkdtemp(prefix='processing-claims-retained-'))
        configs = root / 'src/kaggle_batch'; configs.mkdir(parents=True)
        for key in LANES:
            (configs / f'cloud-config-month-{key}.json').write_text(json.dumps({'schedule_enabled':True,'reconcile_only':False}))
            dbpath = root / f'state/kaggle-month-{key}/batches.sqlite3'; dbpath.parent.mkdir(parents=True)
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE batches(id TEXT PRIMARY KEY,state TEXT,error TEXT)')
                db.execute('CREATE TABLE batch_claims(batch_id TEXT,entry_id INTEGER)')
                if key == 'primary':
                    db.executemany('INSERT INTO batches VALUES(?,?,?)', [('old','quarantined','unknown'),
                        ('unknown','submit_unknown','submit_receipt_invalid'), ('conflict','running','submit_cas_conflict')])
                    db.executemany('INSERT INTO batch_claims VALUES(?,?)', [('old',1),('old',2),('unknown',3),('conflict',4)])
        schedule=root/'state/kaggle-month-dispatch/scheduler.json';schedule.parent.mkdir(parents=True)
        schedule.write_text(json.dumps({'at':100,'state':'started','due_claimed':0}))
        paths=list(root.rglob('*.sqlite3'))+list(root.rglob('*.json'))
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        with patch('subprocess.run', side_effect=AssertionError('no provider/service')), patch('socket.socket', side_effect=AssertionError('no network')):
            actual=observe(root,[1,2,3,4,99],now=105)
        self.assertTrue(actual['ledgers_complete'])
        self.assertEqual(actual['quarantined_batch_count'],1)
        self.assertEqual(actual['quarantined_claim_count'],2)
        self.assertEqual(actual['entry_claim_quarantined_ids'],{1,2})
        self.assertEqual(actual['entry_claim_unknown_ids'],{3})
        self.assertEqual(actual['entry_claim_receipt_conflict_ids'],{3,4})
        self.assertEqual(for_entry({'entry_id':1,'state':'ai_error'},actual,now=105)['reason_code'],'submission_quarantined')
        self.assertEqual(for_entry({'entry_id':4,'state':'pending'},actual,now=105)['reason_code'],'submission_unknown')
        new=for_entry({'entry_id':99,'state':'pending'},actual,now=105)
        self.assertEqual(new['reason_code'],'processing')
        self.assertFalse(new['claim_held'])
        self.assertNotIn('submission_quarantined',new['reason_codes'])
        self.assertEqual(before,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_ledger_uncertainty_preserves_independent_exact_source_restrictions(self):
        from feed_consumption import RSS_SUMMARY_ERROR, UNVERIFIED_FEED_ERROR
        data=evidence(configs={},scheduler={},ledgers_complete=False)
        for state,error,reason in [('requires_fulltext_adapter',RSS_SUMMARY_ERROR,'rss_summary_only'),
                                   ('requires_source_review',UNVERIFIED_FEED_ERROR,'rss_feed_identity_unverified')]:
            with self.subTest(reason=reason):
                value=for_entry({'entry_id':99,'state':state,'error':error},data,now=105)
                self.assertEqual(value['reason_code'],reason)
                self.assertIn('ledger_unavailable',value['reason_codes'])
                self.assertFalse(value['claim_held'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
