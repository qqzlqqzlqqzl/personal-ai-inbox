"""Only synthetic subscription transport is used; no feed or account requests."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from test_vendor_catalog import functions, ROOT
from vendor_catalog import (SUMMARY_FEEDS, SUMMARY_POLICY_VERSION, annotate_subscription_support,
                            summary_policy_ready)


def policy(version=SUMMARY_POLICY_VERSION, feeds=SUMMARY_FEEDS):
    module = types.ModuleType('feed_consumption')
    module.POLICY_VERSION = version; module.SUMMARY_FEEDS = feeds
    module.summary_feed_policy = lambda entry: 'summary_only' if entry['feed']['feed_url'] in SUMMARY_FEEDS else None
    return module


class KicktraqCatalogTests(unittest.TestCase):
    def test_missing_wrong_version_set_or_result_keeps_guard_closed(self):
        for module in (None, policy('unknown'), policy(feeds=set(SUMMARY_FEEDS)), policy(feeds=SUMMARY_FEEDS | {'https://example.test/feed'})):
            with self.subTest(module=module), patch.dict(sys.modules, {'feed_consumption': module}):
                self.assertFalse(summary_policy_ready())
        module = policy(); module.summary_feed_policy = lambda _: True
        with patch.dict(sys.modules, {'feed_consumption': module}): self.assertFalse(summary_policy_ready())
        with patch.dict(sys.modules, {'feed_consumption': policy()}): self.assertTrue(summary_policy_ready())

    def test_only_exact_https_with_policy_is_addable_but_never_analysis_supported(self):
        for ready in (False, True):
            rows = [{'url': u, 'status': 'ok', 'analysis_supported': True} for u in SUMMARY_FEEDS]
            annotate_subscription_support(rows, summary_ready=ready)
            for row in rows:
                self.assertEqual(row['subscription_supported'], ready)
                self.assertFalse(row['analysis_supported'])
                self.assertTrue(row['rss_summary_only'])
                self.assertFalse(row['direct_kickstarter_url_available'])

    def test_alias_query_project_and_unknown_catalog_rows_are_not_addable(self):
        url = sorted(SUMMARY_FEEDS)[0]
        rows = [{'url': value, 'status': 'ok', 'analysis_supported': True} for value in
                (url.replace('https:', 'http:'), url + '?redirect=https://example.test',
                 url + '/', 'https://www.kicktraq.com/projects/example/project/',
                 url.replace('www.kicktraq.com', 'kicktraq.com'),
                 url.replace('www.kicktraq.com', 'www.kicktraq.com.'),
                 url.replace('www.kicktraq.com', '%77ww.kicktraq.com'),
                 url.replace('www.kicktraq.com', 'www.kicktraq.com:443'))]
        rows += [{'url': 'https://example.test/feed'}, {'url': 'https://example.test/paper', 'status': 'ok', 'analysis_supported': False}]
        annotate_subscription_support(rows, summary_ready=True)
        self.assertTrue(all(x['subscription_supported'] is False for x in rows))
        self.assertEqual(rows[2]['source_kind'], 'unverified_source')
        self.assertFalse(rows[2]['rss_summary_only'])

    def call_subscribe(self, body, module, resolved_x_url=None):
        calls = []
        class HTTPException(Exception):
            def __init__(self, status_code, detail): self.status_code=status_code; self.detail=detail
        async def authorize(request, **kwargs): calls.append(('authorize', kwargs))
        class Client:
            async def post(self, url, **kwargs):
                calls.append(('POST', url, kwargs['json']))
                self.assert_never_provider = url == 'http://mf.test/v1/feeds'
                assert self.assert_never_provider
                return types.SimpleNamespace(content=b'{"feed_id":7}', status_code=201)
        class Request:
            async def json(self): return body
        ns={'Request':Request,'authorize':authorize,'HTTPException':HTTPException,
            'Response':lambda *a,**kw:kw,'MF':'http://mf.test','auth_headers':lambda _: {},
            'app':types.SimpleNamespace(state=types.SimpleNamespace(client=Client()))}
        functions('api.py',['subscribe'],ns)
        x = types.ModuleType('x_source')
        async def probe(handle):
            calls.append(('x_probe', handle))
            return {'feed_ready':True,'profile_valid':True,'feed_url':resolved_x_url}
        x.probe=probe
        with patch.dict(sys.modules, {'feed_consumption':module,'x_source':x}):
            try: result=asyncio.run(ns['subscribe'](Request())); error=None
            except HTTPException as exc: result=None; error=exc.status_code
        return calls,result,error

    def test_explicit_crawler_true_is_forced_false_for_both_exact_sources(self):
        for url in SUMMARY_FEEDS:
            calls,result,error=self.call_subscribe({'url':url,'category_id':2,'crawler':True},policy())
            self.assertIsNone(error); self.assertEqual(result['status_code'],201)
            self.assertEqual(calls[1],('POST','http://mf.test/v1/feeds',{'feed_url':url,'category_id':2,'crawler':False}))

    def test_missing_policy_http_and_redirect_like_aliases_never_post(self):
        url=sorted(SUMMARY_FEEDS)[0]
        for target,module in ((url,None),(url.replace('https:', 'http:'),policy()),
                              (url+'?redirect=https://example.test',policy()),
                              (url.replace('www.', 'name:password@www.'),policy()),
                              (url.replace('www.kicktraq.com', 'www.kicktraq.com.'),policy()),
                              (url.replace('www.kicktraq.com', '%77ww.kicktraq.com'),policy()),
                              (url.replace('www.kicktraq.com', 'www.kicktraq.com:443'),policy())):
            calls,_,error=self.call_subscribe({'url':target,'category_id':2,'crawler':True},module)
            self.assertEqual(error,409); self.assertEqual([c[0] for c in calls],['authorize'])

    def test_extra_identity_flags_and_x_probe_cannot_bypass(self):
        url=sorted(SUMMARY_FEEDS)[0]
        for extra in ({'summary_policy_ready':True},{'provider':'Kicktraq'},{'redirect':'https://example.test'}, {'x_handle':'synthetic'}):
            calls,_,error=self.call_subscribe({'url':url,'category_id':2,**extra},policy())
            self.assertEqual(error,400);self.assertEqual([c[0] for c in calls],['authorize'])
        calls,_,error=self.call_subscribe({'url':'https://example.test/feed','category_id':2,'x_handle':'synthetic'},policy(),resolved_x_url=url)
        self.assertEqual(error,409);self.assertEqual([c[0] for c in calls],['authorize','x_probe'])

    def test_other_sources_keep_original_crawler_behavior(self):
        calls,_,error=self.call_subscribe({'url':'https://example.test/feed','category_id':2,'crawler':True},None)
        self.assertIsNone(error);self.assertIs(calls[1][2]['crawler'],True)


if __name__ == '__main__': unittest.main()
