"""Synthetic handler tests: no sockets, credentials, provider or model calls."""
import collections
from contextlib import redirect_stdout
from email.message import Message
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('relay_guard', Path(__file__).with_name('guard.py'))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.catalog = list(guard.MODEL_TARGETS.values())
        self.catalog_status = 200
        self.clients = {}
        self.reply = {'model':'native-private-name', 'internal':'drop-me',
                      'choices':[{'message':{'role':'assistant','content':'synthetic reply'},
                                  'finish_reason':'stop'}]}
        self.server = SimpleNamespace(work=threading.BoundedSemaphore(2),
            rates=collections.defaultdict(collections.deque), lock=threading.Lock())

    def perform(self, body=None, *, method='POST', token='synthetic-public-token', extra_headers=()):
        request = {'model':'public-fast', 'messages':[{'role':'user','content':'synthetic input'}]}
        if body is not None: request = body
        raw = json.dumps(request).encode()
        handler = object.__new__(guard.Handler)
        handler.path = '/v1/models' if method == 'GET' else '/v1/chat/completions'
        handler.server = self.server
        handler.headers = Message()
        if token is not None: handler.headers['Authorization'] = 'Bearer '+token
        handler.headers['Content-Type'] = 'application/json'
        handler.headers['Content-Length'] = str(len(raw))
        for name, value in extra_headers: handler.headers[name] = value
        handler.rfile = io.BytesIO(raw)
        replies = []
        handler.answer = lambda status, obj: replies.append((status, obj))
        owner = self

        class Connection:
            def __init__(self, host, port, timeout):
                assert (host, port) == ('127.0.0.1', 18767)
            def request(self, method, path, body=None, headers=None):
                owner.calls.append({'method':method, 'path':path, 'body':body, 'headers':headers})
                self.method = method
            def getresponse(self):
                data = {'data':[{'id':name} for name in owner.catalog]} if self.method == 'GET' else owner.reply
                raw = json.dumps(data).encode()
                return SimpleNamespace(status=owner.catalog_status if self.method == 'GET' else 200,
                    read=lambda limit:raw[:limit], getheader=lambda name, default='':'application/json')
            def close(self): pass

        with patch.object(guard.http.client, 'HTTPConnection', Connection), \
             patch.object(guard.pathlib.Path, 'read_text', return_value=json.dumps(self.clients)) as read, \
             redirect_stdout(io.StringIO()):
            getattr(handler, 'do_'+method)()
        self.assertEqual(len(replies), 1)
        if token is None or len(handler.headers.get_all('Authorization', [])) != 1:
            read.assert_not_called()
        return replies[0]

    def posts(self):
        return [call for call in self.calls if call['method'] == 'POST']

    def test_target_only_catalog_maps_both_aliases_and_preserves_public_response(self):
        for alias, target in guard.MODEL_TARGETS.items():
            with self.subTest(alias=alias):
                status, reply = self.perform({'model':alias,'messages':[{'role':'user','content':'text'}]})
                self.assertEqual(status, 200)
                upstream = json.loads(self.posts()[-1]['body'])
                self.assertEqual(upstream['model'], target)
                self.assertEqual(upstream['messages'], [{'role':'user','content':'text'}])
                self.assertEqual(upstream['max_tokens'], 2048)
                self.assertEqual(reply['model'], alias)
                self.assertNotIn('internal', reply)

    def test_same_name_native_alias_wins_even_when_target_is_present(self):
        for alias, target in guard.MODEL_TARGETS.items():
            for catalog in ([alias], [alias, target]):
                with self.subTest(alias=alias, catalog=catalog):
                    self.catalog = catalog
                    status, reply = self.perform({'model':alias,'messages':[{'role':'user','content':'text'}]})
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(self.posts()[-1]['body'])['model'], alias)
                    self.assertEqual(reply['model'], alias)

    def test_models_lists_only_aliases_supported_by_this_authenticated_catalog(self):
        self.catalog = [guard.MODEL_TARGETS['public-fast'], 'unrelated-native-model']
        status, reply = self.perform(method='GET')
        self.assertEqual(status, 200)
        self.assertEqual([row['id'] for row in reply['data']], ['public-fast'])
        self.catalog = []
        self.assertEqual(self.perform(method='GET')[1]['data'], [])
        self.assertEqual(self.posts(), [])

    def test_absent_target_does_not_reuse_another_request_catalog_or_client_hint(self):
        digest = hashlib.sha256(b'synthetic-public-token').hexdigest()
        self.clients = {digest:{'name':'fixture', 'backend_key':'synthetic-native-token',
                                'models':['public-fast','public-smart']}}
        self.assertEqual(self.perform()[0], 200)
        self.assertEqual(self.calls[-1]['headers']['Authorization'], 'Bearer synthetic-native-token')
        before = len(self.posts())
        self.catalog = [guard.MODEL_TARGETS['public-smart']]
        status, reply = self.perform()
        self.assertEqual((status, reply['error']['code']), (503, 'no_configured_provider'))
        self.assertEqual(len(self.posts()), before)
        self.assertEqual(self.calls[-1]['headers']['Authorization'], 'Bearer synthetic-native-token')

    def test_native_model_names_remain_rejected_as_public_input(self):
        for model in [*guard.MODEL_TARGETS.values(), 'unrelated-native-model']:
            with self.subTest(model=model):
                status, reply = self.perform({'model':model,'messages':[{'role':'user','content':'text'}]})
                self.assertEqual((status, reply['error']['code']), (400, 'model_not_allowed'))
        self.assertEqual(self.posts(), [])

    def test_missing_duplicate_and_native_rejected_auth_never_infer(self):
        self.assertEqual(self.perform(token=None)[0], 401)
        self.assertEqual(self.perform(extra_headers=[('Authorization','Bearer duplicate')])[0], 401)
        self.assertEqual(self.calls, [])
        for status in (401, 403):
            self.catalog_status = status
            self.assertEqual(self.perform()[0], 401)
        self.assertEqual(self.posts(), [])

    def test_tools_multimodal_and_nontext_requests_still_refuse_before_inference(self):
        base = {'model':'public-fast','messages':[{'role':'user','content':'text'}]}
        cases = [dict(base, tools=[]), dict(base, tool_choice='auto'), dict(base, stream=True),
                 dict(base, messages=[{'role':'user','content':[{'type':'text','text':'text'}]}]),
                 dict(base, messages=[{'role':'tool','content':'text'}]),
                 dict(base, messages=[{'role':'user','content':'text','name':'extra'}]),
                 dict(base, messages=[{'role':'user','content':None}])]
        for body in cases:
            with self.subTest(body=body): self.assertEqual(self.perform(body)[0], 400)
        self.assertEqual(self.posts(), [])

    def test_output_filter_rate_concurrency_and_size_limits_remain_enforced(self):
        self.reply['choices'][0]['message']['tool_calls'] = []
        self.assertEqual(self.perform()[1]['error']['code'], 'upstream_unsafe_contract')
        self.reply['choices'][0]['message'].pop('tool_calls')
        self.server.work.acquire(); self.server.work.acquire()
        before = len(self.posts())
        self.assertEqual(self.perform()[1]['error']['code'], 'concurrency_limit')
        self.server.work.release(); self.server.work.release()
        self.assertEqual(len(self.posts()), before)
        self.assertEqual(self.perform({'model':'public-fast','messages':[{'role':'user','content':'x'*guard.MAX_INPUT}]})[0], 413)
        digest = hashlib.sha256(b'synthetic-public-token').hexdigest()
        self.server.rates[digest] = collections.deque([guard.time.monotonic()]*30)
        self.assertEqual(self.perform()[1]['error']['code'], 'request_rate_limit')
        self.assertEqual(len(self.posts()), before)


if __name__ == '__main__': unittest.main()
