"""Readiness deadline/observation controls; actual CSP browser rerun is separate."""
import ast
from pathlib import Path
import unittest
from unittest.mock import patch

import reader_loading_performance as measurement


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.now=0.0
        self.waits=[]
        self.evaluations=[]
        self.values=[False,True]
        owner=self
        class Page:
            def wait_for_timeout(self,ms):owner.waits.append(ms);owner.now+=ms/1000
            def wait_for_function(self,*args,**kwargs):raise AssertionError('page-side eval polling must not be used')
        class Locator:
            def evaluate(self,predicate,*,timeout):
                owner.evaluations.append((predicate,timeout))
                return owner.values.pop(0) if len(owner.values)>1 else owner.values[0]
        self.page=Page();self.locator=Locator()

    def run_wait(self,timeout=15000):
        with patch.object(measurement.time,'monotonic',lambda:self.now):
            measurement.wait_for_observation(self.page,self.locator,'e=>Boolean(e.complete)',timeout)

    def test_observation_progresses_without_eval_waiter(self):
        self.run_wait()
        self.assertEqual(self.waits,[50])
        self.assertEqual(self.evaluations,[('e=>Boolean(e.complete)',15000),('e=>Boolean(e.complete)',14950)])

    def test_one_absolute_deadline_not_reset_per_poll(self):
        self.values=[False]
        with self.assertRaisesRegex(TimeoutError,'readiness deadline'):self.run_wait(75)
        self.assertEqual(len(self.waits),2)
        self.assertAlmostEqual(self.waits[0],50);self.assertAlmostEqual(self.waits[1],25)
        self.assertEqual([t for _,t in self.evaluations],[75,25])
        self.assertEqual(self.now,.075)

    def test_ready_result_after_the_deadline_is_rejected(self):
        def late(*args,**kwargs):self.now=.076;return True
        self.locator.evaluate=late
        with self.assertRaisesRegex(TimeoutError,'readiness deadline'):self.run_wait(75)

    def test_nonboolean_values_never_count_as_ready(self):
        for value in [None,1,{},'ready',[]]:
            self.values=[value]
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'boolean'):self.run_wait()

    def test_invalid_or_extended_deadline_refused(self):
        for value in [0,-1,True,15001,float('inf')]:
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'deadline'):self.run_wait(value)

    def test_dom_or_csp_error_is_not_swallowed(self):
        def fail(*args,**kwargs):raise RuntimeError('synthetic DOM/CSP error')
        self.locator.evaluate=fail
        with self.assertRaisesRegex(RuntimeError,'DOM/CSP'):self.run_wait()
        self.assertEqual(self.waits,[])

    def test_call_sites_and_five_second_list_expectation_not_replaced(self):
        source=Path(measurement.__file__).read_text();tree=ast.parse(source)
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
        self.assertFalse(any(isinstance(n.func,ast.Attribute) and n.func.attr=='wait_for_function' for n in calls))
        for name in ['open_article','image_sweep']:
            function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            observations=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='wait_for_observation']
            self.assertEqual(len(observations),2 if name=='open_article' else 1)
            if name=='open_article':
                self.assertEqual(observations[0].args[2].value,"e=>(e.innerText.length>100 && !e.getAttribute('aria-busy'))")
                self.assertEqual(observations[1].args[2].value,'e=>Boolean(window.__readerBodyObservation.opens.at(-1)?.first_prose && window.__readerBodySnapshot().ready)')
            for observation in observations:
                expression=observation.args[2].value
                self.assertTrue(expression.startswith('e=>'))
                self.assertNotIn('eval',expression);self.assertNotIn('Function(',expression)
        self.assertIn("expect(page.locator('.load-more-container')).to_have_attribute('data-loaded-count','24')",source)


if __name__=='__main__':unittest.main()
