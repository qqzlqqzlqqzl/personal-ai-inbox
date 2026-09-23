import json
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from cloud_cycle import timer_text


class ScheduleTests(unittest.TestCase):
    def test_exact_six_and_twelve_hour_slots(self):
        self.assertIn('00,06,12,18:00:00',timer_text(6))
        self.assertIn('00,12:00:00',timer_text(12))
        with self.assertRaises(ValueError):
            timer_text(1)

    def test_disabled_schedule_exits_before_credentials_state_or_gpu(self):
        root=Path(__file__).parent/'test-runs'/uuid.uuid4().hex
        root.mkdir(parents=True)
        config=root/'config.json'
        # Intentionally no credentials, state paths or provider configuration.
        config.write_text(json.dumps({'interval_hours':6,'schedule_enabled':False}))
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('cloud_cycle.py')),
                               '--config',str(config)],timeout=15,check=True,capture_output=True,text=True)
        self.assertEqual({'state':'schedule_disabled','gpu_started':False},json.loads(result.stdout))


if __name__=='__main__':
    unittest.main()
