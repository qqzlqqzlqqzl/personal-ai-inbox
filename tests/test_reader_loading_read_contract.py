"""Exact synthetic GET contracts observed in the fixed 879 real-browser trace.

Matches Miniflux c4d54f8 entry ID DESC/filter/count-before-pagination semantics.
Malformed numeric inputs are deliberately refused by this bounded fixture,
instead of emulating the native API's permissive fallback to its maximum limit.
"""
import itertools
from urllib.parse import parse_qs
import unittest

from reader_loading_fixture import Fixture


class ReadContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture=object.__new__(Fixture)
        self.fixture.base='http://127.0.0.1:31415'

    def ids(self, **query):
        return self.fixture.api('/mf/v1/entries/ids','GET',{k:[str(v)] for k,v in query.items()},None)

    def entries(self, **query):
        return self.fixture.api('/mf/v1/entries','GET',{k:[str(v)] for k,v in query.items()},None)

    def test_actual_candidate_initial_AI_request_returns_24_with_revision(self):
        query = parse_qs('order=created_at&direction=desc&limit=24&globally_visible=true&'
                         'ai_view=recommended&ai_min=6&ai_sort=score&ai_revision=initial')
        status, body, label, delay = self.fixture.api('/mf/v1/entries','GET',query,None)
        self.assertEqual((status, body['total'], label, delay), (200, 72, 'list:0:24', 150))
        self.assertEqual([row['id'] for row in body['entries']], list(range(1, 25)))
        self.assertRegex(body['ai_revision'], r'^[0-9a-f]{64}$')
        self.assertNotIn('ai_result_changed', body)

    def test_AI_revision_is_stable_across_pages_and_limits_without_changing_rows(self):
        scope = {'ai_view':'recommended','ai_min':6,'ai_sort':'score'}
        revision = self.entries(**scope,ai_revision='initial')[1]['ai_revision']
        for offset, limit in [(0,24),(24,24),(48,24),(72,24),(0,1)]:
            with self.subTest(offset=offset,limit=limit):
                old = self.entries(**scope,offset=offset,limit=limit)
                current = self.entries(**scope,offset=offset,limit=limit,ai_revision=revision)
                self.assertEqual(current, (old[0],{**old[1],'ai_revision':revision},old[2],old[3]))
                self.assertEqual(set(old[1]), {'total','entries'})

    def test_stale_AI_revision_returns_changed_empty_page_with_current_revision(self):
        revision = self.entries(ai_revision='initial')[1]['ai_revision']
        stale = ('0' if revision[0] != '0' else '1') + revision[1:]
        self.assertEqual(self.entries(offset=24,ai_revision=stale),
            (200,{'total':72,'entries':[],'ai_revision':revision,'ai_result_changed':True},'list:24:24',150))

    def test_AI_revision_binds_scope_and_all_scores_not_just_page(self):
        revision = self.entries(ai_min=6,ai_revision='initial')[1]['ai_revision']
        changed = self.entries(ai_min=7,ai_revision=revision)[1]
        self.assertTrue(changed['ai_result_changed'])
        self.assertEqual(changed['entries'], [])
        original = self.fixture.entry
        for key in ('score','technical_score','business_score'):
            def rows(n,deferred=False):
                row=original(n,deferred)
                if n==72: row['ai'][key]-=1
                return row
            self.fixture.entry=rows
            with self.subTest(score=key):
                self.assertTrue(self.entries(ai_min=6,ai_revision=revision)[1]['ai_result_changed'])
        self.fixture.entry=original

    def test_AI_revision_empty_scope_and_legacy_counts_remain_consistent(self):
        old = self.entries(status='unread',limit=1)
        self.assertEqual(old,(200,{'total':0,'entries':[]},'unread-count',0))
        current = self.entries(status='unread',limit=1,ai_revision='initial')
        revision = current[1]['ai_revision']
        self.assertEqual(current,(old[0],{**old[1],'ai_revision':revision},old[2],old[3]))
        self.assertEqual(self.entries(status='unread',limit=1,ai_revision=revision),current)

    def test_malformed_AI_revisions_and_existing_bounds_remain_rejected(self):
        for revision in ('','INITIAL','0'*63,'0'*65,'A'*64,'g'*64,'../initial'):
            with self.subTest(revision=revision),self.assertRaisesRegex(ValueError,'invalid AI list revision'):
                self.entries(ai_revision=revision)
        for query in ({'limit':25},{'offset':73},{'unexpected':'value'}):
            with self.subTest(query=query),self.assertRaises(ValueError):
                self.entries(ai_revision='initial',**query)

    def test_actual_G_integration_request_returns_explicit_false(self):
        self.assertEqual(self.fixture.api('/mf/v1/integrations/status','GET',{},None),
                         (200,{'has_integrations':False},'integrations-status',0))

    def test_actual_G_starred_count_requests_match_no_starred_scenario(self):
        for query in ({'offset':0,'limit':1,'starred':'true'},
                      {'offset':0,'limit':1,'starred':'true','status':'unread'}):
            self.assertEqual(self.ids(**query),(200,{'total':0,'entry_ids':[]},'entry-ids',0))

    def test_all_scope_is_id_descending_without_entry_bodies(self):
        status,body,label,delay=self.ids()
        self.assertEqual((status,label,delay),(200,'entry-ids',0))
        self.assertEqual(body,{'total':72,'entry_ids':list(range(72,0,-1))})

    def test_pagination_total_is_filtered_total_not_page_length(self):
        for offset,limit in [(0,1),(0,24),(24,24),(48,24),(71,24),(72,24),(73,24),(9999,10000)]:
            with self.subTest(offset=offset,limit=limit):
                self.assertEqual(self.ids(offset=offset,limit=limit)[1],
                    {'total':72,'entry_ids':list(range(72,0,-1))[offset:offset+limit]})

    def test_read_unread_starred_filters_use_scenario_rows(self):
        for status,starred in itertools.product([None,'read','unread'],[None,'true','false']):
            query={k:v for k,v in [('status',status),('starred',starred)] if v is not None}
            expected=[] if status=='unread' or starred=='true' else list(range(72,0,-1))
            with self.subTest(query=query):self.assertEqual(self.ids(**query)[1],{'total':len(expected),'entry_ids':expected})

    def test_filter_derivation_is_not_a_constant_empty_response(self):
        original=self.fixture.entry
        def rows(n,deferred=False):
            row=original(n,deferred);row['status']='unread' if n in (2,5) else 'read';row['starred']=n in (5,9);return row
        self.fixture.entry=rows
        self.assertEqual(self.ids(starred='true')[1],{'total':2,'entry_ids':[9,5]})
        self.assertEqual(self.ids(status='unread')[1],{'total':2,'entry_ids':[5,2]})
        self.assertEqual(self.ids(starred='true',status='unread')[1],{'total':1,'entry_ids':[5]})
        self.assertEqual(self.ids(starred='true',offset=1,limit=1)[1],{'total':2,'entry_ids':[5]})

    def test_invalid_filters_and_parameters_refused(self):
        for query in [{'status':'removed'},{'starred':'1'},{'starred':'True'},{'order':'asc'},
                      {'direction':'asc'},{'feed_id':7},{'category_id':1},{'limit':0},{'limit':10001},
                      {'limit':'bad'},{'offset':-1},{'offset':2147483648}]:
            with self.subTest(query=query),self.assertRaises(ValueError):self.ids(**query)
        with self.assertRaises(ValueError):self.fixture.api('/mf/v1/entries/ids','GET',{'status':['read','unread']},None)
        with self.assertRaises(ValueError):self.fixture.api('/mf/v1/entries/ids','GET',{}, {'unexpected':True})
        for query,body in [({'anything':['x']},None),({}, {})]:
            with self.assertRaises(ValueError):self.fixture.api('/mf/v1/integrations/status','GET',query,body)

    def test_methods_nearby_and_unknown_paths_remain_unsupported(self):
        paths=['/mf/v1/entries/ids','/mf/v1/integrations/status']
        for path,method in itertools.product(paths,['POST','PUT','HEAD','DELETE']):
            with self.subTest(path=path,method=method):self.assertEqual(self.fixture.api(path,method,{},None)[0],501)
        for path in ['/mf/v1/entries/ids/','/mf/v1/integrations/status/','/mf/v1/integrations','/mf/v1/unrecognized']:
            with self.subTest(path=path):self.assertEqual(self.fixture.api(path,'GET',{},None),
                (501,{'error_message':'unsupported_fixture_api'},'unsupported-api',0))


if __name__=='__main__':unittest.main()
