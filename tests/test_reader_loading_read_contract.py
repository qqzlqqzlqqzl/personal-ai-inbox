"""Exact synthetic GET contracts observed in the fixed 879 real-browser trace.

Matches Miniflux c4d54f8 entry ID DESC/filter/count-before-pagination semantics.
Malformed numeric inputs are deliberately refused by this bounded fixture,
instead of emulating the native API's permissive fallback to its maximum limit.
"""
import itertools
import unittest

from reader_loading_fixture import Fixture


class ReadContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture=object.__new__(Fixture)
        self.fixture.base='http://127.0.0.1:31415'

    def ids(self, **query):
        return self.fixture.api('/mf/v1/entries/ids','GET',{k:[str(v)] for k,v in query.items()},None)

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
