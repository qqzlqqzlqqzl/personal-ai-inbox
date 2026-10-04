"""Retained synthetic malformed-publisher failure bookkeeping; no network."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agent_status_publisher import normalize
import agent_status_reader as reader
import agent_status_store as store

class MalformedPublisherTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='status-malformed-retained-'))
        self.path = self.root/'state.json'
        self.now = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)
        self.sample = {'schema_version': 1, 'sequence': 100, 'observed_at': store.utc(self.now),
                       'tasks': [{'name': 'SYNTHETIC_TASK', 'state': 'waiting', 'model': 'SYNTHETIC_MODEL'}],
                       'statistics': {'total': 1, 'capacity': 6, 'active': 1, 'waiting': 1, 'completed': 0, 'failed': 0, 'unknown': 0}}
        self.good = store.record_pull(self.path, json.dumps(self.sample).encode(), self.now)
        self.original = self.path.read_bytes()
        (self.root/'before-malformed.json').write_bytes(self.original)
        self.source = {'schema_version': 1, 'feed_id': 'fixture', 'writer_id': 'fixture', 'revision': 101,
                       'generated_at': store.utc(self.now), 'observed_at': store.utc(self.now), 'expected_interval_seconds': 600,
                       'collector_state': 'ok', 'scope': {'kind': 'current_main_task_tree', 'root_excluded': True, 'capacity': 6},
                       'running_count': 1, 'workers': [{'id': 'fixture', 'name': '\ud800', 'state': 'waiting',
                       'model': 'SYNTHETIC_MODEL', 'reasoning': 'fixture', 'observed_at': store.utc(self.now)}],
                       'samples': [], 'limitations': [], 'last_error': None}
        self.raw = json.dumps(self.source, ensure_ascii=True).encode('ascii')
        (self.root/'malformed-source.json').write_bytes(self.raw)
    def test_public_normalizer_uses_fixed_invalid_sample_error(self):
        with self.assertRaises(store.InvalidSample) as caught:
            normalize(self.raw, self.now)
        self.assertEqual(str(caught.exception), 'labels')
    def test_actual_pull_records_failure_without_replacing_last_good(self):
        later = self.now + timedelta(seconds=1)
        with mock.patch.object(reader, 'read_file', return_value=self.raw) as read:
            result = reader.pull(self.root/'source', self.root/'cache', reader.APPROVED_REF, self.path, later)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(result['pull_status'], 'failed')
        self.assertEqual(result['last_attempt_at'], store.utc(later))
        self.assertEqual(result['last_successful_pull_at'], self.good['last_successful_pull_at'])
        self.assertEqual(result['sample'], self.good['sample'])
        before = json.loads(self.original); after = json.loads(self.path.read_bytes())
        self.assertEqual(after['sample_sha256'], before['sample_sha256'])
        self.assertEqual(after['error_code'], 'transport')
        self.assertNotIn('sample_sha256', result)
        self.assertNotIn('SYNTHETIC_TASK', json.dumps(result))
        self.assertNotIn('SYNTHETIC_MODEL', json.dumps(result))

if __name__ == '__main__':
    unittest.main()
