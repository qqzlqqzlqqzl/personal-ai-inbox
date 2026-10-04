"""Separate decoded reopen observations from a strict fresh-page HTTP-cache audit."""
import copy
import unittest

import reader_loading_performance as measurement
from compare_reader_loading import compare
import test_reader_loading_performance as controls


class WarmPhaseTests(unittest.TestCase):
    def pair(self):return controls.SamplingContract().row('baseline')['pairs'][0]

    def test_reopen_requires_decode_but_does_not_fabricate_cache_identity(self):
        row=self.pair();images=row['warm_images']
        measurement.decoded_reopen_observation(images,900,1500)
        self.assertTrue(all('request_id' not in image for image in images))
        for field,value in [('request_id','old-cold-id'),('from_disk_cache',True),
                            ('browser_cache_event',True),('request_to_finished_ms',100),
                            ('naturalWidth',0),('visible',False),('at',800)]:
            broken=copy.deepcopy(images);broken[0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):measurement.decoded_reopen_observation(broken,900,1500)

    def test_new_target_must_share_the_original_context(self):
        original={'target_id':'first','browser_context_id':'context-1'}
        measurement.require_new_page_same_cache(original,{'target_id':'second','browser_context_id':'context-1'})
        for current in [original,{'target_id':'second','browser_context_id':'context-2'},
                        {'target_id':'second','browser_context_id':None},
                        {'target_id':True,'browser_context_id':'context-1'}]:
            with self.subTest(current=current),self.assertRaises(ValueError):measurement.require_new_page_same_cache(original,current)

    def test_same_page_has_no_new_network_request_and_still_needs_separate_proof(self):
        before,after=controls.SamplingContract().row('baseline'),controls.SamplingContract().row('candidate')
        # Same-page observations intentionally have no requestId/cache metrics.
        self.assertEqual(len(compare(before,after)['pairs']),5)
        after['pairs'][0]['http_cache_page']['cdp']=[]
        with self.assertRaises(ValueError):compare(before,after)

    def test_old_instrumentation_or_changed_semantics_cannot_compare(self):
        for where in ('root','pair','classification'):
            before,after=controls.SamplingContract().row('baseline'),controls.SamplingContract().row('candidate')
            if where=='root':after.pop('measurement_contract')
            elif where=='pair':after['pairs'][0]['measurement_contract']='old-v1'
            else:after['pairs'][0]['warm_observation_class']='HTTP_CACHE_HIT'
            with self.subTest(where=where),self.assertRaises(ValueError):compare(before,after)

    def test_cold_or_bootstrap_events_cannot_satisfy_new_page_window(self):
        for modification in ('cold-time','excluded-bootstrap'):
            before,after=controls.SamplingContract().row('baseline'),controls.SamplingContract().row('candidate')
            cache=after['pairs'][0]['http_cache_page']
            if modification=='cold-time':cache['cdp'][0]['wall_time']=.5
            else:cache['image_event_start']=len(cache['cdp'])
            with self.subTest(modification=modification),self.assertRaises(ValueError):compare(before,after)

    def test_cache_data_cannot_be_substituted_after_the_request_proof(self):
        before,after=controls.SamplingContract().row('baseline'),controls.SamplingContract().row('candidate')
        after['pairs'][0]['http_cache_page']['images'][0]['request_id']='different-query'
        with self.assertRaisesRegex(ValueError,'published cache metrics'):compare(before,after)

    def test_new_page_bootstrap_and_cache_costs_do_not_enter_reopen_latency(self):
        before,after=controls.SamplingContract().row('baseline'),controls.SamplingContract().row('candidate')
        for row in after['pairs']:
            row['http_cache_page']['bootstrap_ms']=1000
            row['http_cache_page']['click_to_body_ms']=800
            row['http_cache_page']['body_observation']['prose_dom_ready_ms']=800
            row['http_cache_page']['body_observation']['first_prose']['at']=1800
        result=compare(before,after)['pairs'][0]
        self.assertEqual(result['milliseconds']['warm_click_to_body_ms']['delta'],0)
        self.assertEqual(result['http_cache_page']['bootstrap_ms']['delta'],996)
        self.assertEqual(result['http_cache_page']['click_to_body_ms']['delta'],797)

    def test_no_image_http_or_nonfinite_cache_cost_can_pass(self):
        for field,value in [('image_http_requests',1),('bootstrap_ms',float('nan')),('click_to_body_ms',True)]:
            before,after=controls.SamplingContract().row('baseline'),controls.SamplingContract().row('candidate')
            after['pairs'][0]['http_cache_page'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):compare(before,after)


if __name__=='__main__':unittest.main()
