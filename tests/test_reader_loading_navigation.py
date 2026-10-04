"""Synthetic navigation clock/byte controls, not real browser timing results."""
import ast
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from reader_loading_navigation import NavigationObservation, summarize_navigation


def sample():
    def e(kind,t,rid='',**kw):return {'kind':kind,'timestamp':t,'received_at':t+100,'request_id':rid,**kw}
    return {'url':'http://127.0.0.1:1/inbox/all','overflow':False,
        'marks':{'goto_start':100.9,'goto_end':102.1,'assert_start':102.2,'assert_end':107.2},
        'events':[
            e('request',1,'doc',frame_id='main',loader_id='new',type='Document',url='http://127.0.0.1:1/inbox/all'),
            e('frame',1.1,frame_id='main',loader_id='new',parent_id=None,url='http://127.0.0.1:1/inbox/all'),
            e('finished',1.2,'doc',encoded_bytes=100),
            e('request',1.3,'script',frame_id='main',loader_id='new',type='Script',url='http://127.0.0.1:1/assets/a.js'),
            e('request',1.4,'pending',frame_id='main',loader_id='new',type='Script',url='http://127.0.0.1:1/assets/b.js'),
            e('dcl',2,frame_id='main',loader_id='new'),
            e('finished',3,'script',encoded_bytes=500),
        ]}


class Session:
    def __init__(self):self.handlers={};self.commands=[]
    def on(self,event,fn):self.handlers[event]=fn
    def send(self,method,args=None):self.commands.append((method,args))


class NavigationTests(unittest.TestCase):
    def test_actual_dcl_and_local_assertion_are_distinct_clocks(self):
        out=summarize_navigation(sample())
        self.assertEqual(out['request_to_dcl_ms'],1000)
        self.assertAlmostEqual(out['first24_assertion_python_ms'],5000)
        self.assertAlmostEqual(out['goto_return_to_assert_start_python_ms'],100)
        self.assertEqual(out['at_actual_dcl']['finished_response_encoded_bytes'],100)
        self.assertEqual(out['at_actual_dcl']['in_flight_ids'],['pending','script'])
        self.assertEqual(out['responses_finishing_after_dcl_encoded_bytes'],500)
        self.assertEqual(out['at_python_assert_start']['finished_response_encoded_bytes'],100)
        self.assertEqual(out['at_python_assert_end']['finished_response_encoded_bytes'],600)
        self.assertEqual(out['at_python_assert_end']['in_flight_ids'],['pending'])

    def test_late_callback_is_not_misattributed_to_python_boundary(self):
        data=sample();data['events'][2]['received_at']=106
        out=summarize_navigation(data)
        self.assertEqual(out['at_actual_dcl']['finished_response_encoded_bytes'],100)
        self.assertEqual(out['at_python_assert_start']['finished_response_encoded_bytes'],0)
        self.assertEqual(out['at_python_assert_end']['finished_response_encoded_bytes'],600)

    def test_other_frame_or_loader_dcl_cannot_satisfy_main_document(self):
        for field,value in [('frame_id','iframe'),('loader_id','old')]:
            data=sample();data['events'][5][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'DCL'):summarize_navigation(data)
        data=sample();data['events'][1]['parent_id']='parent'
        with self.assertRaisesRegex(ValueError,'root document'):summarize_navigation(data)

    def test_old_document_and_unrelated_finished_bytes_never_join(self):
        data=sample();other=copy.deepcopy(data['events'][0]);other.update(request_id='old',loader_id='old',url='http://127.0.0.1:1/old');data['events'].append(other)
        data['events'].append({'kind':'finished','request_id':'old','timestamp':1.9,'received_at':101.9,'encoded_bytes':999999})
        out=summarize_navigation(data);self.assertEqual(out['at_actual_dcl']['finished_response_encoded_bytes'],100)
        self.assertNotIn('old',out['at_python_assert_end']['requested_ids'])

    def test_inflight_content_length_cannot_be_counted_as_received(self):
        data=sample();data['events'][4]['content_length']=10000000
        out=summarize_navigation(data)
        self.assertEqual(out['at_python_assert_end']['finished_response_encoded_bytes'],600)
        self.assertIn('pending',out['at_python_assert_end']['in_flight_ids'])

    def test_failed_request_not_finished_or_left_inflight(self):
        data=sample();data['events'].append({'kind':'failed','request_id':'pending','timestamp':4,'received_at':104})
        out=summarize_navigation(data)['at_python_assert_end']
        self.assertEqual(out['failed_ids'],['pending']);self.assertEqual(out['in_flight_ids'],[])
        self.assertEqual(out['finished_response_encoded_bytes'],600)

    def test_missing_ambiguous_invalid_numbers_and_order_are_refused(self):
        mutations=[lambda d:d['events'].append(copy.deepcopy(d['events'][0])),
            lambda d:d['events'].append(copy.deepcopy(d['events'][5])),
            lambda d:d['events'].append(copy.deepcopy(d['events'][2])),
            lambda d:d['events'][2].update(encoded_bytes=True),
            lambda d:d['events'][2].update(encoded_bytes=float('nan')),
            lambda d:d['events'][2].update(encoded_bytes=-1),
            lambda d:d['events'][2].update(timestamp=.1),
            lambda d:d['marks'].pop('assert_end'),
            lambda d:d['marks'].update(assert_end=90),lambda d:d.update(overflow=True)]
        for mutation in mutations:
            data=sample();mutation(data)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):summarize_navigation(data)

    def test_finish_preserves_original_failure_and_bounded_raw_events(self):
        session=Session();obs=NavigationObservation(session,'http://127.0.0.1:1/inbox/all')
        for i in range(4097):session.handlers['Network.loadingFinished']({'requestId':str(i),'timestamp':i,'encodedDataLength':0})
        obs.finish()
        self.assertEqual(len(obs.record['events']),4096);self.assertTrue(obs.record['overflow'])
        self.assertEqual(obs.record['status'],'INCOMPLETE');self.assertIn('budget',obs.record['diagnostic_error'])
        session.handlers['Network.loadingFailed']({'requestId':'later','timestamp':6000})
        self.assertEqual(len(obs.record['events']),4096)

    def test_mark_and_finish_send_no_commands_or_requests(self):
        session=Session();obs=NavigationObservation(session,'http://127.0.0.1:1/inbox/all')
        self.assertEqual(session.commands,[('Page.enable',None),('Page.setLifecycleEventsEnabled',{'enabled':True})])
        commands=copy.deepcopy(session.commands)
        with patch('reader_loading_navigation.time.monotonic',return_value=5):
            self.assertEqual(obs.mark('goto_start'),5)
        obs.finish();self.assertEqual(session.commands,commands)

    def test_original_two_first24_assertions_have_no_timeout_extension(self):
        source=Path(__file__).with_name('reader_loading_performance.py').read_text();tree=ast.parse(source)
        assertions=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
            and n.func.attr=='to_have_attribute' and len(n.args)==2
            and isinstance(n.args[0],ast.Constant) and n.args[0].value=='data-loaded-count'
            and isinstance(n.args[1],ast.Constant) and n.args[1].value=='24']
        self.assertEqual(len(assertions),2);self.assertTrue(all(not n.keywords for n in assertions))
        self.assertEqual(source.count("finally: navigation.mark('assert_end')"),2)
        helper=Path(__file__).with_name('reader_loading_navigation.py').read_text()
        for forbidden in ('unsafe-eval','setBypassCSP','setCacheDisabled','emulateNetworkConditions','dataLength'):
            self.assertNotIn(forbidden,helper)


if __name__=='__main__':unittest.main()
