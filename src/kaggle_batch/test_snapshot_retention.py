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

    def test_fresh_extraction_latest_survives_global_cap_before_batch_exists(self):
        lane=self.lane('primary')
        fresh=self.snapshot(lane/'before-extraction-1',999)
        receipt=fresh.parent/'backup-complete.json'
        report=prune(self.root,cap_bytes=1,young_seconds=1800,now=1000)
        self.assertTrue(fresh.exists())
        self.assertTrue(receipt.exists())
        self.assertEqual('over_budget_protected',report['state'])
        self.assertEqual(0,report['deleted_count'])
        self.assertEqual(1,report['protected_count'])
        self.assertEqual(report['before_bytes'],report['after_bytes'])

    def test_fresh_finished_import_latest_survives_global_cap(self):
        lane=self.lane('primary',[('qwen-inbox-new','imported')])
        fresh=self.snapshot(lane/'qwen-inbox-new',999)
        report=prune(self.root,cap_bytes=1,young_seconds=1800,now=1000)
        self.assertTrue(fresh.exists())
        self.assertTrue((fresh.parent/'backup-complete.json').exists())
        self.assertEqual('over_budget_protected',report['state'])
        self.assertEqual(0,report['deleted_count'])

    def test_young_boundary_retains_fresh_then_allows_expired_history_pruning(self):
        lane=self.lane('primary')
        backup=self.snapshot(lane/'before-extraction-1',200)
        report=prune(self.root,cap_bytes=1,young_seconds=1800,now=1999)
        self.assertTrue(backup.exists())
        self.assertEqual(0,report['deleted_count'])
        report=prune(self.root,cap_bytes=1,young_seconds=1800,now=2000)
        self.assertFalse(backup.exists())
        self.assertFalse((backup.parent/'backup-complete.json').exists())
        self.assertEqual('ok',report['state'])
        self.assertEqual(1,report['deleted_count'])

    def test_dry_run_preserves_young_latest_and_reports_protected_overage(self):
        lane=self.lane('primary')
        fresh=self.snapshot(lane/'before-extraction-1',999)
        report=prune(self.root,cap_bytes=1,young_seconds=1800,now=1000,dry_run=True)
        self.assertTrue(fresh.exists())
        self.assertTrue((fresh.parent/'backup-complete.json').exists())
        self.assertEqual('over_budget_protected',report['state'])
        self.assertEqual(0,report['deleted_count'])
        self.assertEqual(report['before_bytes'],report['after_bytes'])

    def orphan(self,folder,name,mtime):
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/name
        path.write_bytes(b'x'*4096)
        os.utime(path,(mtime,mtime))
        return path

    def test_prunes_only_stale_interrupted_backup_copies(self):
        lane=self.lane('primary',[('qwen-inbox-b1','submitted')])
        now=100000
        stale=self.orphan(lane/'qwen-inbox-b1','backup-123.partial',now-7*3600)
        journal=self.orphan(lane/'qwen-inbox-b1','backup-123.partial-journal',now-7*3600)
        live=self.orphan(lane/'qwen-inbox-b1','backup-456.partial',now-60)
        kept=self.orphan(lane/'qwen-inbox-b1','incomplete-backup-1.sqlite3',now-7*3600)
        manifest=self.orphan(lane/'qwen-inbox-b1','manifest.json',now-7*3600)
        report=prune(self.root,now=now)
        self.assertFalse(stale.exists());self.assertFalse(journal.exists())
        self.assertTrue(live.exists());self.assertTrue(kept.exists());self.assertTrue(manifest.exists())
        self.assertEqual(2,report['orphan_deleted_count'])
        self.assertGreater(report['orphan_deleted_bytes'],0)

    def test_prunes_bulky_files_of_old_finished_batches_only(self):
        lane=self.lane('primary',[('qwen-inbox-old','imported'),('qwen-inbox-new','imported'),
                                  ('qwen-inbox-live','submitted'),('../escape','imported')])
        now=30*86400;old=now-8*86400;new=now-3600
        runner=self.orphan(lane/'qwen-inbox-old','runner.py',old)
        log=self.orphan(lane/'qwen-inbox-old'/'output','server.log',old)
        gpu=self.orphan(lane/'qwen-inbox-old'/'output','gpu-memory.jsonl',old)
        kept=[self.orphan(lane/'qwen-inbox-old',name,old) for name in
              ('manifest.json','verified-results.json','prepared-code.json')]
        kept.append(self.orphan(lane/'qwen-inbox-old'/'output','results.jsonl',old))
        kept.append(self.orphan(lane/'qwen-inbox-new','runner.py',new))
        kept.append(self.orphan(lane/'qwen-inbox-live','runner.py',old))
        kept.append(self.orphan(self.root/'state'/'escape','runner.py',old))
        report=prune(self.root,now=now)
        for path in (runner,log,gpu):self.assertFalse(path.exists())
        for path in kept:self.assertTrue(path.exists(),path)
        self.assertEqual(3,report['artifact_deleted_count'])
        self.assertGreater(report['artifact_deleted_bytes'],0)

    def test_burst_of_finished_batches_does_not_pin_young_copies(self):
        rows=[(f'qwen-inbox-b{i}','resolved') for i in range(4)]+[('qwen-inbox-live','submitted')]
        lane=self.lane('primary',rows)
        now=100000
        imports=[self.snapshot(lane/f'qwen-inbox-b{i}',now-600+i*60) for i in range(4)]
        extractions=[self.snapshot(lane/f'before-extraction-{i}',now-590+i*60) for i in range(4)]
        live=self.snapshot(lane/'qwen-inbox-live',now-30)
        report=prune(self.root,now=now)
        # One newest historical copy per lane, the newest (possibly live)
        # extraction copy, and the unfinished batch's copy survive.
        self.assertTrue(live.exists())
        self.assertTrue(extractions[-1].exists())
        survivors=[p for p in imports+extractions[:-1] if p.exists()]
        self.assertEqual(1,len(survivors))
        self.assertEqual(6,report['deleted_count'])

    def test_young_copy_of_unfinished_batch_stays_protected(self):
        lane=self.lane('primary',[('qwen-inbox-a','downloaded'),('qwen-inbox-b','resolved')])
        now=100000
        unfinished=self.snapshot(lane/'qwen-inbox-a',now-120)
        older=self.snapshot(lane/'qwen-inbox-b',now-60)
        newer=self.snapshot(lane/'before-extraction-1',now-30)
        prune(self.root,now=now,cap_bytes=1)
        self.assertTrue(unfinished.exists())
        self.assertTrue(newer.exists())

    def test_dry_run_keeps_stale_interrupted_backup_copies(self):
        lane=self.lane('primary')
        stale=self.orphan(lane/'qwen-inbox-b1','backup-123.partial',1)
        report=prune(self.root,now=100000,dry_run=True)
        self.assertTrue(stale.exists())
        self.assertEqual(1,report['orphan_deleted_count'])


if __name__=='__main__':
    unittest.main(verbosity=2)
