import copy
import unittest
from reader_loading_body import BODY_CONTRACT, PROSE_TEXT, validate_ready


def ready_snapshot():
    return {'contract':BODY_CONTRACT,'ready':True,'painted':None,'heading':'性能样本 001',
            'bodyPresent':True,'ariaBusy':None,'rootCount':1,'matchingParagraphs':1,'visibleParagraphs':1,
            'prose':[{'exactText':True,'visible':True,'computed':{'visible':True,'opacity':1},
                      'rect':{'left':0,'top':0,'right':100,'bottom':100,'width':100,'height':100},
                      'clip':{'left':0,'top':0,'right':100,'bottom':100},'intersection':{'width':100,'height':100}}]}


class ProseAdmission(unittest.TestCase):
    def test_exact_fixture_paragraph_is_the_required_original_text(self):
        from reader_loading_fixture import Fixture
        fixture=Fixture.__new__(Fixture);fixture.base='http://127.0.0.1:43210'
        self.assertIn('<p>'+PROSE_TEXT+'</p>',fixture.entry(1)['content'])
        self.assertEqual(fixture.entry(1,deferred=True)['content'],'')
        validate_ready(ready_snapshot())

    def test_notes_only_busy_wrong_title_root_and_painted_claim_refused(self):
        for key,value in [('ready',False),('bodyPresent',False),('ariaBusy','true'),('heading','性能样本 002'),
                          ('rootCount',0),('rootCount',2),('painted',True),('visibleParagraphs',0)]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                row=ready_snapshot();row[key]=value;validate_ready(row)

    def test_nonfinite_boolean_fabricated_or_zero_intersection_refused(self):
        for kind in ('nan','infinite','bool','clipped','false-visible','mismatch'):
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                row=ready_snapshot();prose=row['prose'][0]
                if kind=='nan':prose['rect']['left']=float('nan')
                if kind=='infinite':prose['rect']['right']=float('inf')
                if kind=='bool':prose['intersection']['width']=True
                if kind=='clipped':prose['clip']['left']=100
                if kind=='false-visible':prose['computed']['visible']=False
                if kind=='mismatch':row['visibleParagraphs']=2;row['matchingParagraphs']=2
                validate_ready(row)

    def test_comparator_refuses_old_v2_or_mismatched_ready_metric(self):
        from compare_reader_loading import compare
        from test_reader_loading_performance import SamplingContract
        before=SamplingContract().row('baseline');after=SamplingContract().row('candidate')
        self.assertEqual(compare(before,after)['status'],'COMPARED')
        for kind in ('old-contract','notes','metric'):
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                changed=copy.deepcopy(after)
                if kind=='old-contract':changed['measurement_contract']='same-page-reopen-and-new-page-http-cache-v2'
                if kind=='notes':changed['pairs'][0]['warm_body_observation']['prose_ready']['ready']=False
                if kind=='metric':changed['pairs'][0]['cold_body_observation']['prose_dom_ready_ms']=50
                compare(before,changed)


if __name__=='__main__':unittest.main()
