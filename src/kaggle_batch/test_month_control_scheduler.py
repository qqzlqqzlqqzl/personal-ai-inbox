import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import month_control


class MonthControlSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.stage=Path(self.temp.name)
        self.auth=patch.object(month_control,'authenticate',lambda value:None)
        self.auth.start()
    def tearDown(self):
        self.auth.stop();self.temp.cleanup()

    def test_start_uses_health_scheduler_not_blind_four_lane_start(self):
        with patch.object(month_control,'STAGE',self.stage),              patch('kaggle_batch.lane_scheduler.tick',return_value={'state':'started','started':['secondary']}) as tick,              patch.object(month_control.subprocess,'run') as run:
            result=month_control.control('start','test')
        tick.assert_called_once_with();run.assert_not_called()
        self.assertEqual(['secondary'],result['scheduler']['started'])

    def test_resume_clears_pause_then_uses_scheduler(self):
        (self.stage/'paused.json').write_text(json.dumps({'paused_at':1}))
        with patch.object(month_control,'STAGE',self.stage),              patch('kaggle_batch.lane_scheduler.tick',return_value={'state':'idle','started':[]}) as tick:
            result=month_control.control('resume','test')
        tick.assert_called_once_with()
        self.assertFalse((self.stage/'paused.json').exists())
        self.assertEqual('resume',result['action'])


if __name__=='__main__':unittest.main(verbosity=2)
