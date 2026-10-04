"""Deterministic dispatcher-order controls, not a real-browser substitute."""
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from query_route_queue import HeldQueryRoutes, READY, matches_query

ORIGIN = 'http://127.0.0.1:12345'


def request(scope='today', **changes):
    query = {'ai_view': 'recommended', 'ai_min': '8', 'limit': '24', 'ai_sort': 'score', 'direction': 'desc'}
    if scope == 'today':
        query['published_after'] = '1791043200'
    query.update(changes)
    return SimpleNamespace(method='GET', url=ORIGIN + '/mf/v1/entries?' + urlencode(query), failure=None)


class DispatcherPage:
    """Simulate only when queued route callbacks are serviced by an API wait."""
    def __init__(self):
        self.markers = []
        self.callbacks = []
        self.events = []

    def add_init_script(self, script):
        assert script == 'window.__queryRouteQueue=[]'

    def evaluate(self, script, item):
        assert '__queryRouteQueue.push(item)' in script
        self.markers.append(item)
        self.events.append('route_enqueued_acknowledged')

    def wait_for_function(self, script, *, arg, timeout):
        assert script == READY and timeout == 5000
        self.events.append('wait_for_exact_held_route')
        def ready():
            return any(all(marker[key] == value for key, value in arg.items()) for marker in self.markers)
        while not ready() and self.callbacks:
            self.callbacks.pop(0)()
        if not ready():
            raise TimeoutError('synthetic: matching route acknowledgement never arrived')


def route(req):
    return SimpleNamespace(request=req)


def test_request_event_before_route_callback_waits_for_exact_enqueue():
    page = DispatcherPage()
    queue = HeldQueryRoutes(page, ORIGIN)
    req = request()
    held = route(req)
    page.events.append('expect_request_resolved')
    page.callbacks.append(lambda: queue.hold('today', held))
    # The old immediate assertion fails at exactly this point in the same order.
    with pytest.raises(AssertionError):
        assert queue.pending and queue.pending[0][0] == 'today', []
    assert queue.take('today', req) is held
    assert page.events == ['expect_request_resolved', 'wait_for_exact_held_route', 'route_enqueued_acknowledged']
    assert queue.pending == [] and queue.taken == 1


def test_route_callback_before_request_event_also_works():
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    req = request();held = route(req)
    queue.hold('today', held)
    assert queue.take('today', req) is held


@pytest.mark.parametrize('changes', [{'ai_min': '6'}, {'limit': '48'}, {'offset': '24'}, {'ai_view': 'raw'}])
def test_wrong_filter_or_page_never_acknowledges(changes):
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    req = request(**changes)
    assert not matches_query('today', req, ORIGIN)
    with pytest.raises(AssertionError):
        queue.hold('today', route(req))
    assert queue.added == 0 and page.markers == [] and queue.pending == []


def test_scope_and_full_query_identity_are_required():
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    req = request();different = request(ai_sort='date')
    queue.hold('today', route(different))
    with pytest.raises(TimeoutError):
        queue.take('today', req)
    assert queue.taken == 0 and len(queue.pending) == 1
    assert not matches_query('all', req, ORIGIN)


def test_identical_url_from_another_request_is_not_the_expected_object():
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    req = request();duplicate = request()
    queue.hold('today', route(duplicate))
    with pytest.raises(AssertionError, match='request identity'):
        queue.take('today', req)
    assert queue.taken == 0 and len(queue.pending) == 1


def test_old_marker_never_releases_a_later_request():
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    old = request();queue.hold('today', route(old));queue.take('today', old)
    with pytest.raises(TimeoutError):
        queue.take('today', request())
    assert queue.taken == 1 and queue.pending == []


def test_cancel_before_consumption_refuses_without_removing_route():
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    req = request();queue.hold('today', route(req));req.failure = 'net::ERR_ABORTED'
    with pytest.raises(AssertionError, match='cancelled'):
        queue.take('today', req)
    assert queue.taken == 0 and len(queue.pending) == 1


def test_cancelled_previous_route_does_not_acknowledge_reversed_scope():
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    old = request();queue.hold('today', route(old));queue.take('today', old)
    old.failure = 'net::ERR_ABORTED'
    current = request('all');held = route(current)
    page.callbacks.append(lambda: queue.hold('all', held))
    assert queue.take('all', current) is held
    assert queue.taken == 2 and queue.pending == []


def test_reordered_pending_routes_cannot_be_silently_skipped():
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    earlier = request('all');current = request()
    queue.hold('all', route(earlier));queue.hold('today', route(current))
    with pytest.raises(TimeoutError):
        queue.take('today', current)
    assert queue.taken == 0 and len(queue.pending) == 2


def test_arbitrary_ack_without_python_route_never_releases():
    page = DispatcherPage();queue = HeldQueryRoutes(page, ORIGIN)
    req = request()
    page.markers.append({'sequence': 1, 'scope': 'today', 'url': req.url, 'method': 'GET', 'minimum': '8', 'limit': '24'})
    with pytest.raises(AssertionError, match='order/scope'):
        queue.take('today', req)
    assert queue.taken == 0


def test_foreign_origin_and_non_get_requests_do_not_match():
    req = request();req.method = 'POST'
    assert not matches_query('today', req, ORIGIN)
    req = request();req.url = req.url.replace(ORIGIN, 'https://example.test')
    assert not matches_query('today', req, ORIGIN)
