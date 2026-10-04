"""Synthetic display catalogue, private identity and last-good retention proofs.

Fixtures are deliberately retained; this suite does not delete files or folders.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import agent_status_store as store
from agent_status_publisher import normalize
import agent_status_reader as reader

NOW = datetime(2026, 10, 4, 1, 40, tzinfo=timezone.utc)
NAME = 'SYNTHETIC_UNAPPROVED_PRIVATE_TASK'
MODEL = 'SYNTHETIC_UNAPPROVED_PRIVATE_MODEL'
def sample(sequence=100):
    return {'schema_version': 1, 'sequence': sequence, 'observed_at': store.utc(NOW),
            'tasks': [{'name': NAME, 'state': 'waiting', 'model': MODEL}],
            'statistics': {'total': 1, 'capacity': 6, 'active': 1, 'waiting': 1, 'completed': 0, 'failed': 0, 'unknown': 0}}
def raw(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')
def publisher(value):
    return {'schema_version': 1, 'feed_id': 'fixture', 'writer_id': 'fixture', 'revision': value['sequence'],
            'generated_at': value['observed_at'], 'observed_at': value['observed_at'], 'expected_interval_seconds': 600,
            'collector_state': 'ok', 'scope': {'kind': 'current_main_task_tree', 'root_excluded': True, 'capacity': 6},
            'running_count': 1, 'workers': [{'id': 'fixture', **value['tasks'][0], 'reasoning': 'synthetic private reasoning',
                                          'observed_at': value['observed_at']}], 'samples': [], 'limitations': [], 'last_error': None}

class DisplayPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='status-display-retained-'))
        self.path = self.root / 'state.json'
        self.round = 0
    def save_prior(self):
        self.round += 1
        if self.path.exists():
            (self.root / f'before-{self.round}.json').write_bytes(self.path.read_bytes())
    def pull(self, value, now=NOW):
        self.save_prior()
        return store.record_pull(self.path, raw(value) if value is not None else None, now)
    def private_absent(self, value):
        text = json.dumps(value)
        self.assertNotIn(NAME, text)
        self.assertNotIn(MODEL, text)
        self.assertNotIn('sample_sha256', text)
    def legacy(self):
        value = {'sample': sample(), 'last_successful_pull_at': store.utc(NOW), 'last_attempt_at': store.utc(NOW),
                 'pull_status': 'ok', 'error_code': None}
        self.path.write_bytes(raw(value))
        return value
    def test_empty_catalogue_and_optional_projection(self):
        self.assertEqual(store.APPROVED_TASK_NAMES, frozenset())
        self.assertEqual(store.APPROVED_MODEL_NAMES, frozenset())
        result = store.validate(raw(sample()), NOW)
        self.assertEqual(result['tasks'], [{'state': 'waiting'}])
        self.assertEqual(result['statistics'], sample()['statistics'])
        self.assertEqual(store.validate(raw(result), NOW), result)
        self.private_absent(result)
    def test_public_publisher_never_exposes_labels_or_ignored_metadata(self):
        result = normalize(raw(publisher(sample())), NOW)
        self.private_absent(result)
        self.assertNotIn('reasoning', json.dumps(result))
        self.assertEqual(result['tasks'], [{'state': 'waiting'}])
        self.assertEqual(result['statistics'], sample()['statistics'])
    def test_new_cache_keeps_only_private_digest_and_public_sample(self):
        result = self.pull(sample())
        self.private_absent(result)
        cache = json.loads(self.path.read_bytes())
        self.assertNotIn(NAME, json.dumps(cache)); self.assertNotIn(MODEL, json.dumps(cache))
        self.assertRegex(cache['sample_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(cache['sample']['tasks'], [{'state': 'waiting'}])
    def test_legacy_read_is_projected_without_api_write(self):
        self.legacy(); before = self.path.read_bytes()
        self.private_absent(store.response(store.read_store(self.path), NOW))
        self.assertEqual(self.path.read_bytes(), before)
    def test_failed_pull_scrubs_legacy_and_retains_high_water(self):
        self.legacy(); result = self.pull(None, NOW + timedelta(seconds=1))
        self.private_absent(result)
        self.assertEqual(result['sample']['sequence'], 100)
        self.assertEqual(result['sample']['statistics'], sample()['statistics'])
        self.assertEqual(result['last_successful_pull_at'], store.utc(NOW))
        self.assertEqual(result['pull_status'], 'failed')
        self.assertNotIn(NAME, self.path.read_text())
        replay = self.pull(sample(), NOW + timedelta(seconds=2))
        self.assertEqual(replay['pull_status'], 'ok')
    def test_hidden_name_and_model_conflicts_are_not_erased_by_projection(self):
        self.pull(sample())
        for field in ('name', 'model'):
            changed = copy.deepcopy(sample()); changed['tasks'][0][field] += '_CHANGED'
            result = self.pull(changed, NOW + timedelta(seconds=1))
            self.assertEqual(result['pull_status'], 'failed')
            self.assertEqual(json.loads(self.path.read_bytes())['error_code'], 'sequence_conflict')
            self.assertEqual(result['last_successful_pull_at'], store.utc(NOW)); self.private_absent(result)
    def test_same_sequence_replay_and_semantic_json_order_are_accepted(self):
        self.pull(sample()); original = json.loads(self.path.read_bytes())['sample_sha256']
        value = dict(reversed(list(sample().items())))
        value['tasks'] = [dict(reversed(list(value['tasks'][0].items())))]
        result = self.pull(value, NOW + timedelta(seconds=1))
        self.assertEqual(result['pull_status'], 'ok')
        self.assertEqual(json.loads(self.path.read_bytes())['sample_sha256'], original)
        self.private_absent(result)
    def test_restart_regressions_conflict_transport_and_recovery(self):
        code = "import sys,json;from datetime import datetime,timezone;from agent_status_store import record_pull;print(json.dumps(record_pull(sys.argv[1],None if sys.argv[2]=='null' else sys.argv[2].encode(),datetime(2026,10,4,1,45,tzinfo=timezone.utc))))"
        def restart(value):
            self.save_prior()
            env = {**os.environ, 'PYTHONPATH': str(Path(store.__file__).parent), 'PYTHONDONTWRITEBYTECODE': '1'}
            run = subprocess.run([sys.executable, '-c', code, str(self.path), json.dumps(value)], env=env,
                                 capture_output=True, text=True, timeout=10, check=True)
            return json.loads(run.stdout)
        self.assertEqual(restart(sample())['pull_status'], 'ok')
        for value in (None, sample(99), {**sample(101), 'observed_at': '2026-10-04T01:39:59.999999Z'}):
            result = restart(value); self.assertEqual(result['pull_status'], 'failed')
            self.assertEqual(result['sample']['sequence'], 100); self.private_absent(result)
        changed = sample(); changed['tasks'][0]['model'] += '_CHANGED'
        self.assertEqual(restart(changed)['pull_status'], 'failed')
        self.assertEqual(restart(sample())['pull_status'], 'ok')
        new = {**sample(101), 'observed_at': '2026-10-04T01:41:00Z'}
        self.assertEqual(restart(new)['pull_status'], 'ok')
    def test_pull_adapters_preserve_unredacted_identity_until_private_store(self):
        for format in ('canonical', 'observed-publisher'):
            path = self.root / format / 'state.json'
            value = sample(); encode = (lambda v: raw(v)) if format == 'canonical' else (lambda v: raw(publisher(v)))
            with mock.patch.object(reader, 'read_file', side_effect=lambda *_: encode(value)):
                result = reader.pull(self.root/'source', self.root/'cache', reader.APPROVED_REF, path, NOW)
                self.assertEqual(result['pull_status'], 'ok'); self.private_absent(result)
                prior = path.read_bytes(); (path.parent/'before-conflict.json').write_bytes(prior)
                value['tasks'][0]['name'] += '_CHANGED'
                result = reader.pull(self.root/'source', self.root/'cache', reader.APPROVED_REF, path, NOW)
                self.assertEqual(result['pull_status'], 'failed'); self.private_absent(result)
    def test_malformed_and_transport_keep_scrubbed_last_good(self):
        self.pull(sample())
        for change in (lambda v:v['tasks'][0].update(state='other'), lambda v:v['statistics'].update(active=0),
                       lambda v:v['tasks'][0].update(name='\ud800'), lambda v:v['tasks'][0].update(prompt=NAME)):
            value = copy.deepcopy(sample(101)); change(value); self.save_prior()
            result = store.record_pull(self.path, json.dumps(value).encode(), NOW)
            self.assertEqual(result['pull_status'], 'failed'); self.private_absent(result)
            self.assertEqual(result['sample']['sequence'], 100)

if __name__ == '__main__':
    unittest.main()
