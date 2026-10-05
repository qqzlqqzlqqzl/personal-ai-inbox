"""Virtual-row dispatcher model, not a claim of real Virtua/browser geometry."""
import ast
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import reader_loading_performance as measurement


class VirtualPage:
    def __init__(self,kind='native',loaded=24,top=0):
        self.kind=kind;self.loaded=loaded;self.top=top;self.now=0.0;self.calls=[]
        self.columns=1 if kind=='native' else 4
        self.height=700 if kind=='native' else 800
        self.row_height=500 if kind=='native' else 400
        self.root_count=1;self.stalled=False;self.pending_top=None
        self.root=VirtualRoot(self)
        self.mounted=self.ids()
    def ids(self):
        start=int(self.top//self.row_height)*self.columns+1
        count=math.ceil(self.height/self.row_height)*self.columns
        return list(range(start,min(self.loaded,start+count-1)+1))
    def locator(self,selector):
        if selector==measurement.LIST_SCROLL_ROOT:return self.root
        target=int(selector.split('data-entry-id="',1)[1].split('"',1)[0])
        return VirtualEntry(self,target)


class VirtualRoot:
    def __init__(self,page):self.page=page
    def wait_for(self,*,state,timeout):self.page.calls.append(('root-visible',timeout))
    def count(self):return self.page.root_count
    def evaluate(self,expression,arg=None,*,timeout):
        p=self.page;p.calls.append(('root-evaluate',expression,timeout))
        if expression==measurement.LIST_GEOMETRY:
            return {'kind':p.kind,'scrollTop':p.top,'scrollHeight':math.ceil(p.loaded/p.columns)*p.row_height,
                'clientHeight':p.height,'rect':{'top':100,'bottom':100+p.height,'left':0,'right':390,'width':390,'height':p.height},
                'rows':[{'id':n,'top':100,'bottom':200,'visible':True} for n in p.mounted],
                'loadedCount':p.loaded,'at':p.now*1000}
        if 'scrollBy' in expression:
            if not p.stalled:p.pending_top=max(0,min(p.top+arg,math.ceil(p.loaded/p.columns)*p.row_height-p.height))
            p.calls.append(('real-root-scroll',arg));return
        if 'requestAnimationFrame' in expression:
            p.now+=min(.032,arg/1000)
            if p.pending_top is not None:p.top=p.pending_top;p.pending_top=None;p.mounted=p.ids()
            return True
        raise AssertionError('Unexpected root operation')


class VirtualEntry:
    def __init__(self,page,target):self.page=page;self.target=target
    @property
    def first(self):return self
    def evaluate(self,expression,*,timeout):
        p=self.page
        if self.target not in p.mounted:raise TimeoutError('offscreen row is not mounted')
        assert expression=="e=>e.scrollIntoView({block:'start',behavior:'instant'})"
        p.calls.append(('align-same-row',self.target,timeout))
        p.top=((self.target-1)//p.columns)*p.row_height;p.mounted=p.ids()


class VirtualScrollTests(unittest.TestCase):
    def seek(self,page,target,timeout=15000):
        trace=[]
        with patch.object(measurement.time,'monotonic',lambda:page.now):
            result=measurement.seek_list_entry(page,target,trace,timeout)
        return result,trace

    def test_old_touch_lookup_fails_before_any_scroll_and_new_root_seek_materializes_7(self):
        page=VirtualPage();self.assertEqual(page.mounted,[1,2])
        with self.assertRaisesRegex(TimeoutError,'not mounted'):
            page.locator('.entry-list [data-entry-id="7"]').first.evaluate(
                "e=>e.scrollIntoView({block:'start',behavior:'instant'})",timeout=15000)
        self.assertFalse(any(c[0]=='real-root-scroll' for c in page.calls))
        result,trace=self.seek(page,7)
        self.assertIs(result,page.root);self.assertEqual(trace[0]['rows'][0]['id'],1)
        self.assertTrue(any(c[0]=='real-root-scroll' for c in page.calls))
        self.assertEqual([c[1] for c in page.calls if c[0]=='align-same-row'],[7])
        self.assertLess(page.now,15)

    def test_desktop_after_first_append_retains_position_then_seeks_31(self):
        page=VirtualPage('simplebar',48,1600);self.assertEqual(page.mounted,list(range(17,25)))
        _,trace=self.seek(page,31)
        self.assertEqual(trace[0]['scrollTop'],1600);self.assertEqual(trace[0]['loadedCount'],48)
        self.assertEqual([c[1] for c in page.calls if c[0]=='align-same-row'],[31])
        self.assertTrue(all(c[1]>0 for c in page.calls if c[0]=='real-root-scroll'))

    def test_preserved_position_below_target_scrolls_back_without_reset_to_zero(self):
        page=VirtualPage('simplebar',48,4000);_,trace=self.seek(page,7)
        self.assertEqual(trace[0]['scrollTop'],4000)
        self.assertTrue(all(c[1]<0 for c in page.calls if c[0]=='real-root-scroll'))
        self.assertEqual([c[1] for c in page.calls if c[0]=='align-same-row'],[7])

    def test_already_mounted_identity_needs_no_seeking_scroll(self):
        page=VirtualPage('simplebar',24,0);self.seek(page,7)
        self.assertFalse(any(c[0]=='real-root-scroll' for c in page.calls))
        self.assertEqual([c[1] for c in page.calls if c[0]=='align-same-row'],[7])

    def test_stalled_root_times_out_without_increasing_shared_deadline(self):
        page=VirtualPage();page.stalled=True;trace=[]
        with patch.object(measurement.time,'monotonic',lambda:page.now),self.assertRaisesRegex(TimeoutError,'deadline'):
            measurement.seek_list_entry(page,7,trace,100)
        # Millisecond CDP timeout rounding can overshoot by one millisecond;
        # readiness must still fail, never receive another 15s budget.
        self.assertGreaterEqual(page.now,.1);self.assertLessEqual(page.now,.101)
        self.assertFalse(any(c[0]=='align-same-row' for c in page.calls))
        self.assertGreater(len(trace),1)

    def test_late_mount_cannot_pass_after_the_absolute_deadline(self):
        page=VirtualPage();evaluate=page.root.evaluate
        def late(expression,arg=None,*,timeout):
            result=evaluate(expression,arg,timeout=timeout)
            if expression==measurement.LIST_GEOMETRY:page.now=.101
            return result
        page.root.evaluate=late
        with self.assertRaisesRegex(TimeoutError,'deadline'):self.seek(page,1,100)
        self.assertFalse(any(c[0]=='align-same-row' for c in page.calls))

    def test_alignment_must_still_show_the_same_target(self):
        for vanished in (False,True):
            page=VirtualPage('simplebar');original=page.root.evaluate
            def changed(expression,arg=None,*,timeout):
                result=original(expression,arg,timeout=timeout)
                if expression==measurement.LIST_GEOMETRY and any(c[0]=='align-same-row' for c in page.calls):
                    result['rows']=[r for r in result['rows'] if r['id']!=7] if vanished else [dict(r,visible=False) for r in result['rows']]
                return result
            page.root.evaluate=changed
            with self.subTest(vanished=vanished),self.assertRaisesRegex(ValueError,'visibly mounted'):
                self.seek(page,7)

    def test_unknown_or_ambiguous_roots_refused(self):
        for kind,count in [('unknown',1),('native',2),('native',0)]:
            page=VirtualPage(kind);page.root_count=count
            with self.subTest(kind=kind,count=count),self.assertRaises(ValueError):self.seek(page,7)
            self.assertFalse(any(c[0]=='real-root-scroll' for c in page.calls))

    def test_wrong_row_identity_is_not_silently_skipped(self):
        page=VirtualPage();page.mounted=[1,8]
        with self.assertRaisesRegex(ValueError,'missing target within'):self.seek(page,7)
        page=VirtualPage();page.mounted=[True,2]
        with self.assertRaisesRegex(ValueError,'identity'):self.seek(page,7)

    def test_page_or_store_shortcuts_are_absent_and_original_targets_remain(self):
        tree=ast.parse(Path(measurement.__file__).read_text());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='seek_list_entry')
        text=ast.unparse(function)
        for forbidden in ['goto(', 'scrollToIndex', 'fetch(', 'handleLoadMore', 'wait_for_timeout', 'sleep(']:self.assertNotIn(forbidden,text)
        pair=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='one_pair');text=ast.unparse(pair)
        self.assertIn('[(7, 48), (31, 72)]',text)
        self.assertIn("['list:0:24', 'list:24:24', 'list:48:24']",text)
        self.assertLess(text.index('start = time.monotonic()'),text.index('seek_list_entry('))
        self.assertIn("root=>{root.scrollTop=root.scrollHeight}",text)


if __name__=='__main__':unittest.main()
