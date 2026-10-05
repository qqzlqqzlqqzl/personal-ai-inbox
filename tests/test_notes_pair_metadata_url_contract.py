"""Synthetic-only controls for negative old-DTO injection in the paired harness.

No Miniflux, PostgreSQL, product process, external origin or credentials are used.
The local proxy tests check the harness itself and are not real paired acceptance.
"""
import copy
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import patch

import notes_pair_acceptance as pair

REAL_CONNECTION = http.client.HTTPConnection


def entry(number=1, url=None):
    return {'id': number, 'user_id': 1, 'feed_id': 1, 'title': 'Synthetic title',
            'published_at': '2026-10-04T00:00:00Z', 'changed_at': '2026-10-05T00:00:00Z',
            'url': url if url is not None else f'https://example.invalid/{number}'}


class LegacyDtoFaultContracts(unittest.TestCase):
    def setUp(self):
        self.headers = {'X-Reader-Entry-Metadata': '1', 'Cache-Control': 'no-store',
                        'Content-Type': 'application/json'}
        self.data = {'entries': [entry(), entry(2)]}

    def apply(self, *, status=200, headers=None, payload=None):
        return pair.legacy_url_omission_fault(status, self.headers if headers is None else headers,
                    json.dumps(self.data if payload is None else payload).encode())

    def test_old_five_fields_are_returned_and_original_headers_status_and_input_stay_unchanged(self):
        before = copy.deepcopy(self.data); before_headers = copy.deepcopy(self.headers)
        raw, proof = self.apply()
        actual = json.loads(raw)
        self.assertEqual(actual, {'entries': [{key: value for key, value in row.items() if key not in {'url', 'changed_at'}}
                                             for row in self.data['entries']]})
        self.assertEqual(self.data, before); self.assertEqual(self.headers, before_headers)
        self.assertEqual(proof['upstream_status'], 200)
        self.assertEqual(proof['upstream_capability'], '1')
        self.assertEqual(proof['entry_ids'], [1, 2])
        self.assertFalse(proof['actual_old_binary'])
        self.assertEqual(proof['kind'], 'old-five-field-protocol-injection')

    def test_original_url_is_not_trimmed_or_normalized_by_fault_precondition(self):
        self.data['entries'][0]['url'] = ' https://example.invalid/%2f?q=A#fragment '
        original = copy.deepcopy(self.data)
        self.apply()
        self.assertEqual(self.data, original)

    def test_missing_wrong_duplicate_or_nonstring_capability_is_refused(self):
        for headers in ({}, {'X-Reader-Entry-Metadata': '2'}, {'X-Reader-Entry-Metadata': 1},
                        {'X-Reader-Entry-Metadata': '1', 'x-reader-entry-metadata': '1'}):
            with self.subTest(headers=headers), self.assertRaisesRegex(AssertionError, 'capability 1'):
                self.apply(headers=headers)
        self.apply(headers={'x-reader-entry-metadata': '1'})

    def test_upstream_error_cannot_masquerade_as_old_dto(self):
        for status in (401, 403, 404, 500, 503):
            with self.subTest(status=status), self.assertRaisesRegex(AssertionError, 'successful capability'):
                self.apply(status=status)

    def test_empty_or_malformed_envelope_is_refused(self):
        for data in ({'entries': []}, {}, {'entries': None}, {'entries': {}, 'extra': 1},
                     {'entries': [entry()], 'extra': 1}, [], None):
            # None is passed directly here because apply(None) selects its default fixture.
            with self.subTest(data=data), self.assertRaises(AssertionError):
                pair.legacy_url_omission_fault(200, self.headers, json.dumps(data).encode())

    def test_incomplete_fields_are_not_accepted_as_seven_field_source(self):
        del self.data['entries'][0]['url']
        with self.assertRaisesRegex(AssertionError, 'seven-field'):
            self.apply()

    def test_old_six_fields_are_not_accepted_as_new_binary_source(self):
        del self.data['entries'][0]['changed_at']
        with self.assertRaisesRegex(AssertionError, 'seven-field'):
            self.apply()

    def test_empty_whitespace_null_and_nonstring_urls_are_refused(self):
        for value in ('', '  \t\r\n', None, 1, False, [], {}):
            self.data['entries'][0]['url'] = value
            with self.subTest(value=value), self.assertRaisesRegex(AssertionError, 'seven-field'):
                self.apply()

    def test_unexpected_dto_fields_are_refused(self):
        self.data['entries'][0]['content'] = 'must not be an accepted metadata field'
        with self.assertRaisesRegex(AssertionError, 'seven-field'):
            self.apply()


class ObservingProxyContracts(unittest.TestCase):
    def relay(self, fault):
        payload = {'entries': [entry()]}
        native = json.dumps(payload).encode()
        forwarded = []
        class UpstreamReply:
            status = 200
            def read(self): return native
            def getheaders(self):
                return [('X-Reader-Entry-Metadata', '1'), ('Cache-Control', 'no-store'),
                        ('Content-Type', 'application/json'), ('Content-Length', str(len(native)))]
        class SyntheticUpstream:
            def __init__(self, host, port, timeout):
                self.arguments = (host, port, timeout)
                if self.arguments != ('127.0.0.1', 8093, 20):
                    raise AssertionError('unexpected upstream fixture destination')
            def request(self, method, path, body, headers): forwarded.append((method, path, body))
            def getresponse(self): return UpstreamReply()
            def close(self): pass
        wire = pair.Wire(lambda query: self.fail('no fixture database writes expected'))
        wire.reset('synthetic harness contract', metadata_fault=fault)
        server = ThreadingHTTPServer(('127.0.0.1', 0), pair.proxy_handler(wire))
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(pair.http.client, 'HTTPConnection', SyntheticUpstream):
            worker.start()
            connection = REAL_CONNECTION('127.0.0.1', server.server_port, timeout=3)
            try:
                connection.request('POST', '/mf/v1/entries/metadata', body=b'{"entry_ids":[1]}',
                                   headers={'Content-Type': 'application/json'})
                response = connection.getresponse()
                result = response.status, dict(response.getheaders()), json.loads(response.read())
            finally:
                connection.close(); server.shutdown(); server.server_close(); worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(forwarded), 1)
        self.assertEqual(forwarded[0], ('POST', '/mf/v1/entries/metadata', b'{"entry_ids":[1]}'))
        return result, wire

    def test_normal_proxy_preserves_six_field_payload(self):
        (status, headers, payload), wire = self.relay(None)
        self.assertEqual(status, 200)
        self.assertEqual(headers['X-Reader-Entry-Metadata'], '1')
        self.assertEqual(set(payload['entries'][0]), pair.FIELDS)
        self.assertNotIn('legacy_dto_fault', wire.metadata[0])
        self.assertEqual(wire.bodies, [])

    def test_negative_proxy_preserves_real_capability_and_records_url_omission(self):
        (status, headers, payload), wire = self.relay('legacy-five-fields')
        self.assertEqual(status, 200)
        self.assertEqual(headers['X-Reader-Entry-Metadata'], '1')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(set(payload['entries'][0]), pair.FIELDS - {'url', 'changed_at'})
        self.assertEqual(wire.bodies, [])
        self.assertTrue(wire.metadata[0]['forwarded'])
        self.assertEqual(wire.metadata[0]['legacy_dto_fault']['upstream_fields'], sorted(pair.FIELDS))
        self.assertFalse(wire.metadata[0]['legacy_dto_fault']['actual_old_binary'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
