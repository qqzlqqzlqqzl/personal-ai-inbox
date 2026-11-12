import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from snapshot_retention import prune


class SnapshotRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        (self.root/'state').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def lane(self,name,rows=()):
        root=self.root/'state'/f'kaggle-month-{name}'
        root.mkdir()
        with sqlite3.connect(root/'batches.sqlite3') as db:
            db.execute('CREATE TABLE batches(id TEXT,state TEXT)')
            db.executemany('INSERT INTO batches VALUES (?,?)',rows)
        return root

    def snapshot(self,folder,mtime,size=8192):
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/'before-import.sqlite3'
        path.write_bytes(b'x'*size)
        (folder/'backup-complete.json').write_text(json.dumps({'completed':mtime}))
        os.utime(path,(mtime,mtime))
        os.utime(folder/'backup-complete.json',(mtime,mtime))
        os.utime(folder,(mtime,mtime))
        return path

    def test_keeps_only_latest_complete_history_per_lane(self):
        lane=self.lane('primary',[('qwen-inbox-b1','imported'),('qwen-inbox-b2','resolved')])
        self.snapshot(lane/'before-extraction-1',100)
        self.snapshot(lane/'before-extraction-2',200)
        self.snapshot(lane/'qwen-inbox-b1',300)
        newest=self.snapshot(lane/'qwen-inbox-b2',400)
        report=prune(self.root,cap_bytes=10**9,young_seconds=0,now=1000)
        self.assertEqual('ok',report['state'])
        self.assertEqual(3,report['deleted_count'])
        self.assertTrue(newest.exists())
        remaining=list(lane.glob('**/before-import.sqlite3'))
        self.assertEqual([newest],remaining)

    def test_nonterminal_snapshot_is_never_pruned(self):
        lane=self.lane('primary',[('qwen-inbox-old','imported'),('qwen-inbox-live','running')])
        self.snapshot(lane/'qwen-inbox-old',100)
        live=self.snapshot(lane/'qwen-inbox-live',200)
        report=prune(self.root,cap_bytes=1,young_seconds=0,now=1000)
        self.assertTrue(live.exists())
        self.assertEqual('over_budget_protected',report['state'])
        self.assertEqual(2,report['snapshot_count_before'])
        self.assertEqual(1,report['deleted_count'])

    def test_global_cap_can_override_lane_history_minimum(self):
        a=self.lane('primary',[('qwen-inbox-a','imported')])
        b=self.lane('secondary',[('qwen-inbox-b','imported')])
        old=self.snapshot(a/'qwen-inbox-a',100)
        new=self.snapshot(b/'qwen-inbox-b',200)
        one_file_blocks=new.stat().st_blocks*512
        report=prune(self.root,cap_bytes=one_file_blocks,young_seconds=0,now=1000)
        self.assertEqual('ok',report['state'])
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertLessEqual(report['after_bytes'],one_file_blocks)


if __name__=='__main__':
    unittest.main(verbosity=2)
