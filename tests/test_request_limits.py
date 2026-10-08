"""Real ASGI boundaries: fragmentation must not amplify retained memory."""
import tracemalloc
import unittest

from request_limits import RequestLimits


class RequestLimitsTests(unittest.IsolatedAsyncioTestCase):
    def scope(self, method='POST', path='/mf/v1/entries', **extra):
        return {'type': 'http', 'method': method, 'path': path,
                'client': ('test-peer', 1234), **extra}

    async def exercise(self, messages, *, limit=32, method='POST'):
        source = iter(messages)
        received, sent = [], []
        reads = 0

        async def receive():
            nonlocal reads
            reads += 1
            return next(source)

        async def send(message):
            sent.append(message)

        async def app(scope, receive, send):
            chunks = []
            while True:
                message = await receive()
                chunks.append(message.get('body', b''))
                if not message.get('more_body'):
                    break
            received.append({'type': 'http.request', 'body': b''.join(chunks), 'more_body': False})
            received.append(await receive())
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        await RequestLimits(app, max_body=limit)(self.scope(method), receive, send)
        return received, sent, reads

    async def test_fragments_and_empty_chunks_replay_exact_body_then_disconnect(self):
        messages = [
            {'type': 'http.request', 'body': b'ab', 'more_body': True},
            {'type': 'http.request', 'body': b'', 'more_body': True},
            {'type': 'http.request', 'body': b'cd', 'more_body': False},
            {'type': 'http.disconnect'},
        ]
        received, sent, reads = await self.exercise(messages, limit=4)
        self.assertEqual(received, [
            {'type': 'http.request', 'body': b'abcd', 'more_body': False},
            {'type': 'http.disconnect'},
        ])
        self.assertEqual(sent[0]['status'], 200)
        self.assertEqual(reads, 4)

    async def test_empty_body_is_replayed_before_disconnect(self):
        received, sent, _ = await self.exercise([
            {'type': 'http.request', 'more_body': False},
            {'type': 'http.disconnect'},
        ])
        self.assertEqual(received[0]['body'], b'')
        self.assertEqual(received[1]['type'], 'http.disconnect')
        self.assertEqual(sent[0]['status'], 200)

    async def test_limit_is_actual_bytes_and_stops_before_reading_more(self):
        received, sent, reads = await self.exercise([
            {'type': 'http.request', 'body': b'ab', 'more_body': True},
            {'type': 'http.request', 'body': b'cde', 'more_body': True},
            {'type': 'http.request', 'body': b'f', 'more_body': False},
        ], limit=4)
        self.assertEqual(received, [])
        self.assertEqual(sent[0]['status'], 413)
        self.assertEqual(reads, 2)

    async def test_disconnect_during_collection_never_calls_app(self):
        received, sent, reads = await self.exercise([
            {'type': 'http.request', 'body': b'ab', 'more_body': True},
            {'type': 'http.disconnect'},
        ])
        self.assertEqual((received, sent, reads), ([], [], 2))

    async def test_get_head_and_options_keep_receive_stream_unchanged(self):
        messages = [
            {'type': 'http.request', 'body': b'abcd', 'more_body': True},
            {'type': 'http.request', 'body': b'ef', 'more_body': False},
        ]
        for method in ('GET', 'HEAD', 'OPTIONS'):
            with self.subTest(method=method):
                received = []
                source = iter(messages)

                async def receive():
                    return next(source)

                async def app(scope, receive, send):
                    received.extend([await receive(), await receive()])

                async def send(message):
                    self.fail('No response is expected from the test app')

                await RequestLimits(app, max_body=1)(self.scope(method), receive, send)
                self.assertEqual(received, messages)

    async def test_fragment_count_does_not_amplify_retained_memory(self):
        # Generate transport messages lazily; measuring a prebuilt test list
        # would hide the middleware's own retention. No sockets or live server.
        count = 50_000
        index = 0
        retained = []
        seen = []

        async def receive():
            nonlocal index
            index += 1
            return {'type': 'http.request', 'body': b'x', 'more_body': index < count}

        async def app(scope, receive, send):
            retained.append(tracemalloc.get_traced_memory()[0])
            chunks = []
            while True:
                message = await receive()
                chunks.append(message['body'])
                if not message.get('more_body'):
                    break
            seen.append(b''.join(chunks))

        async def send(message):
            self.fail('No response is expected from the test app')

        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        try:
            await RequestLimits(app, max_body=count)(self.scope(), receive, send)
            growth = retained[0] - before
        finally:
            tracemalloc.stop()
        self.assertEqual(index, count)
        self.assertEqual(seen[0], b'x' * count)
        self.assertLess(growth, 512_000, f'50KB fragmented body retained {growth} bytes')

    async def test_auth_failure_throttle_still_observes_downstream_status(self):
        calls = 0
        sent = []

        async def app(scope, receive, send):
            nonlocal calls
            calls += 1
            await send({'type': 'http.response.start', 'status': 401, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        async def receive():
            self.fail('GET should not read the body')

        async def send(message):
            sent.append(message)

        limits = RequestLimits(app)
        for _ in range(31):
            await limits(self.scope('GET'), receive, send)
        self.assertEqual(calls, 30)
        self.assertEqual(sent[-2]['status'], 429)

    async def test_non_http_scope_passes_through(self):
        calls = []

        async def app(scope, receive, send):
            calls.append((scope, receive, send))

        async def receive():
            self.fail('The middleware must not consume non-HTTP messages')

        async def send(message):
            self.fail('The middleware must not send non-HTTP responses')

        scope = {'type': 'lifespan'}
        await RequestLimits(app)(scope, receive, send)
        self.assertEqual(calls, [(scope, receive, send)])
