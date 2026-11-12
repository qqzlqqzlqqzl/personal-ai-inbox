import importlib.util
from pathlib import Path
import tempfile
import os
import sqlite3
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch
import unittest

spec = importlib.util.spec_from_file_location('agent', Path(__file__).with_name('recovery_agent.py'))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class RecoveryTests(unittest.TestCase):
    def test_live_but_broken_service_needs_three_checks_and_has_budget(self):
        record, calls = {}, []
        restart = lambda: calls.append(1) or True
        for i in range(2):
            self.assertEqual(agent.service_step(record, False, 'active', 10000+i*300, True, restart), 'confirming')
        self.assertEqual(agent.service_step(record, False, 'active', 10600, True, restart), 'restart_requested')
        self.assertEqual(agent.service_step(record, False, 'active', 10900, True, restart), 'restart_limited')
        agent.service_step(record, False, 'active', 11200, True, restart)
        self.assertEqual(agent.service_step(record, False, 'active', 11800, True, restart), 'restart_limited')
        self.assertEqual(len(calls), 2)

    def test_no_restart_when_stopped_dependency_down_or_dry_run(self):
        for active in ('inactive', 'dependency_failed'):
            self.assertEqual(agent.service_step({'failures': 9}, False, active, 10000, True,
                                              lambda: self.fail('must not restart')), 'stopped_no_action')
        self.assertEqual(agent.service_step({'failures': 9}, False, 'active', 10000, False,
                                          lambda: self.fail('dry run')), 'would_restart')

    def test_success_resets_failures_but_not_restart_budget(self):
        record = {'failures': 8, 'restarts': [9900]}
        agent.service_step(record, True, 'active', 10000, True, lambda: False)
        self.assertEqual(record, {'failures': 0, 'restarts': [9900]})

    def test_feed_timeout_is_checkpointed_and_backed_off(self):
        state, writes, calls = {}, [], []
        feed = {'id': 1, 'parsing_error_count': 3, 'checked_at': 'old'}
        def refresh(fid):
            calls.append(fid)
            self.assertTrue(writes)
            raise TimeoutError('do not log this token')
        result = agent.feeds_step([feed], state, 10000, True, refresh, None,
                                  lambda: writes.append(state['1']['next_try']))
        self.assertEqual(result['attempts'][0]['state'], 'TimeoutError')
        agent.feeds_step([feed], state, 10300, True, refresh, None, lambda: None)
        self.assertEqual(calls, [1])
        self.assertEqual(state['1']['next_try'], 11800)

    def test_accepted_refresh_is_not_parse_success(self):
        feed = {'id': 1, 'parsing_error_count': 3, 'checked_at': 'old'}
        for checked, expected in [('old', 'not_yet_recovered'), ('new', 'recovered')]:
            result = agent.feeds_step([feed], {}, 10000, True, lambda _: None,
                lambda _: {'checked_at': checked, 'parsing_error_count': 0}, lambda: None)
            self.assertEqual(result['attempts'][0]['state'], expected)

    def test_batch_limit_disabled_and_native_retry_not_touched(self):
        feeds = [{'id': i, 'parsing_error_count': 3} for i in range(10)]
        feeds += [{'id': 20, 'parsing_error_count': 2}, {'id': 21, 'disabled': True, 'parsing_error_count': 3}]
        calls = []
        result = agent.feeds_step(feeds, {}, 10000, True, lambda fid: calls.append(fid),
                                  lambda _: {}, lambda: None)
        self.assertEqual(calls, [0, 1, 2, 3])
        self.assertEqual(result['stalled'], 10)

    def test_periodic_agent_only_observes_task_recovery_and_never_edits_ledgers(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            folder=root/'src/kaggle_batch';folder.mkdir(parents=True)
            ledger=root/'ledger';ledger.mkdir()
            database=ledger/'batches.sqlite3'
            with sqlite3.connect(database) as db:
                db.execute('CREATE TABLE batches(id TEXT,state TEXT,updated REAL)')
                db.execute("INSERT INTO batches VALUES('old','submit_unknown',0)")
                db.execute('CREATE TABLE task_failure_context(batch_id TEXT,failures INTEGER,next_try REAL,code TEXT)')
                db.execute("INSERT INTO task_failure_context VALUES('old',10,1234,'not_found')")
            for key in ('primary','secondary','third','fourth','fifth'):
                (folder/f'cloud-config-month-{key}.json').write_text(json.dumps({'state_root':str(ledger)}))
            before=database.read_bytes()
            guard=SimpleNamespace(ConfigGuard=lambda *a,**k:lambda:None)
            with patch.object(agent,'ROOT',root),patch.dict(sys.modules,{'dispatch_policy':guard}):
                result=agent.queue_step({},9999999999,True,lambda:self.fail('No write checkpoint'))
            self.assertEqual('native_task_v2',result['primary']['recovery_policy'])
            self.assertEqual(10,result['primary']['task_failures'])
            self.assertEqual(before,database.read_bytes())

    def test_backoff_is_bounded(self):
        self.assertEqual([agent.backoff(i) for i in (1, 2, 3, 100000)], [1800, 3600, 7200, 86400])

    def test_telemetry_nan_and_infinity_are_not_healthy(self):
        self.assertFalse(agent.finite_tree({'cpu': {'usage': float('nan')}}))
        self.assertFalse(agent.finite_tree({'memory': [float('inf')]}))
        self.assertTrue(agent.finite_tree({'cpu': {'usage': 0}, 'memory': {'usage': 55.3}}))

    def test_successful_http_does_not_hide_repeated_stale_metrics(self):
        record,calls={},[]
        for i in range(3):
            good=agent.service_health('public-ai-newapi.service',True,{'fresh':False,'finite':True})
            agent.service_step(record,good,'active',10000+i*300,True,lambda:calls.append(1) or True)
        self.assertEqual([1],calls)
        self.assertTrue(agent.service_health('public-ai-newapi.service',True,{'error_type':'OperationalError'}))
        self.assertTrue(agent.service_health('ai-news-web.service',True,{'fresh':False,'finite':False}))

    def test_log_retention_never_removes_open_logs_or_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, active = root/'oneapi-20260901000000.log', root/'oneapi-20260902000000.log'
            unrelated = root/'account.json'
            for path in (old, active, unrelated):
                path.write_text('test')
                os.utime(path, (10000, 10000))
            agent.prune_logs(root, {active}, 10000+15*86400, True)
            self.assertFalse(old.exists())
            self.assertTrue(active.exists())
            self.assertTrue(unrelated.exists())

    def test_log_retention_dry_run_and_recent_files_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'oneapi-20260901000000.log'
            path.write_text('test')
            os.utime(path, (10000, 10000))
            agent.prune_logs(root, set(), 20000, False, max_bytes=1)
            self.assertTrue(path.exists())
            agent.prune_logs(root, set(), 10100, True, max_bytes=1)
            self.assertTrue(path.exists())


if __name__ == '__main__':
    unittest.main()
