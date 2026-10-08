import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import uuid
from batch_control import Controller
from cloud_cycle import drain, timer_text
from dispatch_policy import DispatchStopped
from queue_dispatch import claimed_entries


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


class QuarantineCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.provider=Mock(side_effect=AssertionError('provider must not be called'))
        self.control=Controller(root/'lane','fixture',client=self.provider,initialize=True)
        manifest={'session_timeout':600,'runtime_source':'fixture/runtime','items':[
            {'id':'a','messages':[{'role':'user','content':'fixture'}],
             'input_hash':'fixture','source_refs':[{'entry_id':11},{'entry_id':12}]}]}
        self.batch=self.control.prepare(manifest,'MANIFEST = None\n')
        self.control._set(self.batch,'submit_unknown',error='inaccessible')
        self.expected=self.control.row(self.batch)
        self.folder=self.control.root/self.batch
        self.files={name:(self.folder/name).read_bytes() for name in
                    ('manifest.json','runner.py','prepared-code.json','kernel-metadata.json')}
        self.recovery=self.control.root/'recovery.json'
        self.recovery.write_text('{"code":"inaccessible","failures":2,"retry_at":0,"at":1}')
        self.recovery_before=self.recovery.read_bytes()
        self.config={'schedule_enabled':True,'batch_limit':20,'drain_queue':True,
                     'cycle_timeout_seconds':1800,'exception_audit_root':str(root/'audit')}
        self.sleep=Mock(side_effect=AssertionError('quarantined cycle must not sleep'))
        self.receipt=None

    def quarantine(self):
        row=self.control.quarantine_unknown(self.batch,self.expected,
            'Synthetic reviewed unknown attempt','test-cycle-quarantine')
        self.receipt=(self.folder/'quarantine-reviewed.json').read_bytes()
        return row

    def run_cycle(self,call,*,manual=False,authorize=None):
        return drain('unused-config',self.config,self.control,call=call,
                     sleep=self.sleep,clock=lambda:0,authorize=authorize or (lambda:None),
                     recovery_batch=self.batch if manual else None)

    def assert_parked(self,result):
        self.assertEqual('quarantined',result['state'])
        self.assertEqual(self.batch,result['batch_id'])
        self.assertEqual(0,result['completed_batches'])
        row=self.control.row(self.batch)
        self.assertEqual('quarantined',row['state'])
        for field in ('id','manifest_hash','remote_status','error'):
            self.assertEqual(self.expected[field],row[field])
        self.assertEqual({11,12},claimed_entries([self.control.root]))
        with self.control.db() as db:
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM batches').fetchone()[0])
        for name,value in self.files.items():
            self.assertEqual(value,(self.folder/name).read_bytes())
        self.assertEqual(self.receipt,(self.folder/'quarantine-reviewed.json').read_bytes())
        self.assertEqual(self.recovery_before,self.recovery.read_bytes())
        self.provider.assert_not_called()
        self.sleep.assert_not_called()

    def test_initial_quarantined_manual_recovery_exits_without_child(self):
        self.quarantine()
        call=Mock(side_effect=AssertionError('no child for an already parked batch'))
        self.assert_parked(self.run_cycle(call,manual=True))
        call.assert_not_called()

    def test_initial_quarantined_automatic_cycle_does_not_advance_or_prepare_again(self):
        self.quarantine()
        call=Mock(return_value={'existing_batch':self.batch})
        self.assert_parked(self.run_cycle(call))
        self.assertEqual(['prepare'],[c.args[1] for c in call.call_args_list])

    def test_observation_quarantine_winner_stops_cycle_even_with_stale_outcome(self):
        def child(config,action,*args,**kwargs):
            if action=='prepare':return {'existing_batch':self.batch}
            self.assertEqual('advance',action)
            self.assertEqual(('--batch',self.batch),args)
            self.quarantine()
            return {'state':'submitted'}  # The fresh ledger, not a stale reply, wins.
        call=Mock(side_effect=child)
        self.assert_parked(self.run_cycle(call))
        self.assertEqual(['prepare','advance'],[c.args[1] for c in call.call_args_list])

    def test_quarantine_at_admission_boundary_prevents_next_child(self):
        checks=[]
        def authorize():
            checks.append(True)
            # drain checks twice before drain_once; the fifth check is just
            # before advance, after the initial state was read as submit_unknown.
            if len(checks)==5:self.quarantine()
        call=Mock(return_value={'existing_batch':self.batch})
        self.assert_parked(self.run_cycle(call,authorize=authorize))
        self.assertEqual(['prepare'],[c.args[1] for c in call.call_args_list])

    def test_stop_after_observation_still_outranks_quarantine(self):
        stopped=[]
        def authorize():
            if stopped:raise DispatchStopped('schedule_disabled')
        def child(config,action,*args,**kwargs):
            self.assertEqual('recover',action)
            row=self.quarantine()
            stopped.append(True)
            return row
        call=Mock(side_effect=child)
        result=self.run_cycle(call,manual=True,authorize=authorize)
        self.assertEqual('schedule_disabled',result['state'])
        self.assertEqual(['recover'],[c.args[1] for c in call.call_args_list])
        self.assertEqual('quarantined',self.control.row(self.batch)['state'])
        self.assertEqual({11,12},claimed_entries([self.control.root]))
        self.assertEqual(self.receipt,(self.folder/'quarantine-reviewed.json').read_bytes())
        self.assertEqual(self.recovery_before,self.recovery.read_bytes())
        self.provider.assert_not_called()
        self.sleep.assert_not_called()


if __name__=='__main__':
    unittest.main()
