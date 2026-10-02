import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from batch_control import Controller
from live_scope import resolve_entry_ids
import lane_scheduler as scheduler

class LiveScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.lane=self.root/'lane';Controller(self.lane,'fixture',initialize=True)
        self.db=self.root/'inbox.db';self.allow=self.root/'allow.json'
        self.allow.write_text(json.dumps({'entry_ids':[1]}))
        self.cfg={'state_root':str(self.lane),'database':str(self.db),'entry_allowlist':str(self.allow),
                  'queue_scope':'all_enabled_feeds','scope_user_id':1,'peer_state_roots':[]}
        with sqlite3.connect(self.db) as db:
            db.execute('CREATE TABLE analyses(entry_id INTEGER,user_id INTEGER,feed_id INTEGER,state TEXT,next_try REAL,attempts INTEGER)')
            db.execute('CREATE TABLE card_translations(entry_id INTEGER,user_id INTEGER,status TEXT,next_try REAL,attempts INTEGER)')
            db.executemany('INSERT INTO analyses VALUES (?,?,?,?,?,?)',[(1,1,1,'done',0,0),(2,1,1,'pending',0,0),(3,1,2,'pending',0,0),(4,2,1,'pending',0,0),(5,1,1,'removed',0,0)])
        self.feeds=[{'id':1,'user_id':1,'disabled':False},{'id':2,'user_id':1,'disabled':True}]
    def tearDown(self): self.tmp.cleanup()
    def test_new_entries_are_picked_without_changing_historical_allowlist(self):
        with patch('live_scope.enabled_feeds',return_value=self.feeds):
            self.assertEqual([1,2],resolve_entry_ids(self.cfg))
            due,_=scheduler.due_entries(time.time(),self.cfg);self.assertEqual({2},due)
            with sqlite3.connect(self.db) as db:db.execute("INSERT INTO analyses VALUES(6,1,1,'pending',0,0)")
            due,_=scheduler.due_entries(time.time(),self.cfg);self.assertEqual({2,6},due)
        self.assertEqual([1],json.loads(self.allow.read_text())['entry_ids'])
    def test_scope_remains_bound_to_active_feeds_and_user(self):
        with patch('live_scope.enabled_feeds',return_value=self.feeds):
            self.assertNotIn(3,resolve_entry_ids(self.cfg));self.assertNotIn(4,resolve_entry_ids(self.cfg))
    def test_empty_feed_catalog_does_not_mean_whole_database(self):
        with patch('live_scope.enabled_feeds',return_value=[]):self.assertEqual([],resolve_entry_ids(self.cfg))
    def test_reader_failure_is_not_silently_widened(self):
        with patch('live_scope.enabled_feeds',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):resolve_entry_ids(self.cfg)
    def test_old_campaign_still_uses_exact_allowlist(self):
        self.assertEqual([1],resolve_entry_ids({**self.cfg,'queue_scope':'allowlist'}))
    def test_completed_items_not_rescored_but_translation_can_be_due(self):
        with sqlite3.connect(self.db) as db:db.execute("INSERT INTO card_translations VALUES(1,1,'pending',0,0)")
        with patch('live_scope.enabled_feeds',return_value=self.feeds):
            due,_=scheduler.due_entries(time.time(),self.cfg);self.assertEqual({1,2},due)

if __name__=='__main__':unittest.main()
