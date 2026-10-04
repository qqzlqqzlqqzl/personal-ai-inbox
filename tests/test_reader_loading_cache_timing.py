"""Replay only the synthetic cache proof; never promote the failed Hosted pair."""
import ast
import copy
import json
from pathlib import Path
import unittest
import reader_loading_performance as measurement

DATA=json.loads((Path(__file__).parent/'fixtures/reader-disk-cache-ccc.json').read_text())


def prove(data):
    return measurement.warm_image_proof(data['events'],copy.deepcopy(data['images']),
                                        data['origin'],data['start_wall_ms'],data['end_wall_ms'])


class DiskCacheTimingTests(unittest.TestCase):
    def test_original_exact_failure_predicate_and_new_causal_proof(self):
        for replay in DATA['replays']:
            with self.subTest(mode=replay['mode']):
                responses={e['request_id']:e for e in replay['events'] if e['kind']=='response'}
                finishes={e['request_id']:e for e in replay['events'] if e['kind']=='finished'}
                self.assertTrue(all(responses[rid]['timestamp']>e['timestamp'] for rid,e in finishes.items()))
                proof=prove(replay);self.assertEqual(len(proof),6)
                self.assertTrue(all(p['from_disk_cache'] and p['encoded_bytes']==0 for p in proof))
                self.assertTrue(all(p['late_disk_cache_timing']['basis']=='disk-cache-resource-timing' for p in proof))
                self.assertTrue(all(p['finished_wall_ms']<=p['visible_wall_ms'] for p in proof))

    def test_late_response_requires_every_used_timing_field(self):
        for key in ('requestTime','sendStart','sendEnd','receiveHeadersStart','receiveHeadersEnd'):
            for bad in (None,-1,True,float('inf'),float('nan'),'0.1'):
                data=copy.deepcopy(DATA['replays'][0]);response=next(e for e in data['events'] if e['kind']=='response');response['timing'][key]=bad
                with self.subTest(key=key,bad=bad),self.assertRaises(ValueError):prove(data)
        for timing in (None,{},False):
            data=copy.deepcopy(DATA['replays'][0]);next(e for e in data['events'] if e['kind']=='response')['timing']=timing
            with self.subTest(timing=timing),self.assertRaises(ValueError):prove(data)

    def test_not_disk_cache_cannot_use_late_notification_branch(self):
        for mode in (False,1,None):
            data=copy.deepcopy(DATA['replays'][0]);next(e for e in data['events'] if e['kind']=='response')['from_disk_cache']=mode
            with self.subTest(mode=mode),self.assertRaises(ValueError):prove(data)

    def test_phase_request_identity_and_future_completion_still_refused(self):
        for mutation in ['future_finish','future_notification','old_trigger','wrong_finish_id','different_url','service_worker','failed']:
            data=copy.deepcopy(DATA['replays'][0]);req=next(e for e in data['events'] if e['kind']=='request')
            resp=next(e for e in data['events'] if e['kind']=='response');fin=next(e for e in data['events'] if e['kind']=='finished')
            if mutation=='future_finish':fin['timestamp']+=100
            if mutation=='future_notification':resp['timestamp']+=100
            if mutation=='old_trigger':data['start_wall_ms']=req['wall_time']*1000+1
            if mutation=='wrong_finish_id':fin['request_id']='unrelated'
            if mutation=='different_url':req['url']+='?other=1'
            if mutation=='service_worker':resp['from_service_worker']=True
            if mutation=='failed':data['events'].append({'kind':'failed','request_id':req['request_id']})
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):prove(data)

    def test_resource_timing_causality_has_no_tolerance_or_clamping(self):
        for mutation in ['request_before_trigger','headers_after_finish','send_reversed','headers_reversed']:
            data=copy.deepcopy(DATA['replays'][0]);req=next(e for e in data['events'] if e['kind']=='request')
            timing=next(e for e in data['events'] if e['kind']=='response')['timing']
            if mutation=='request_before_trigger':timing['requestTime']=req['timestamp']-.000001
            if mutation=='headers_after_finish':timing['receiveHeadersEnd']=100
            if mutation=='send_reversed':timing['sendEnd']=timing['sendStart']-.000001
            if mutation=='headers_reversed':timing['receiveHeadersStart']=timing['receiveHeadersEnd']+.000001
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):prove(data)

    def test_original_ordered_branch_is_literal_and_no_global_tolerance(self):
        tree=ast.parse(Path(measurement.__file__).read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='warm_image_proof')
        branches=[n for n in ast.walk(fn) if isinstance(n,ast.If) and ast.unparse(n.test)=='response_time > finish_time']
        self.assertEqual(len(branches),1)
        self.assertEqual(ast.unparse(branches[0].orelse[0]),
            "require(request_time <= response_time <= finish_time, 'image event order is invalid')")
        text=ast.unparse(branches[0])
        for forbidden in ['isclose','epsilon','abs(', 'max(', 'min(']:self.assertNotIn(forbidden,text)


if __name__=='__main__':unittest.main()
