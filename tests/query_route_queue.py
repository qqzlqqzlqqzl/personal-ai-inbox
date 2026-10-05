"""Explicit route-handler acknowledgement for the isolated query browser suite."""
from urllib.parse import parse_qs, urlsplit

READY = """expected => (window.__queryRouteQueue || []).some(item =>
    item.sequence === expected.sequence && item.scope === expected.scope &&
    item.url === expected.url && item.method === expected.method &&
    item.minimum === expected.minimum && item.limit === expected.limit)"""


def query_identity(scope, request, origin):
    url = urlsplit(request.url)
    base = urlsplit(origin)
    query = parse_qs(url.query)
    assert scope in ('all', 'today'), 'unsupported query scope'
    assert request.method == 'GET' and (url.scheme, url.netloc) == (base.scheme, base.netloc), 'unexpected request origin/method'
    assert url.path == '/mf/v1/entries', 'unexpected entries path'
    assert query.get('ai_view') == ['recommended'] and query.get('ai_min') == ['8'], 'unexpected query lens/minimum'
    assert query.get('limit') == ['24'] and query.get('offset') in (None, ['0']), 'unexpected query page'
    assert ('today' if 'published_after' in query else 'all') == scope, 'unexpected query scope'
    # The full driver URL binds every remaining filter/order/date parameter.
    return {'scope': scope, 'url': request.url, 'method': request.method, 'minimum': '8', 'limit': '24'}


def matches_query(scope, request, origin):
    try:
        query_identity(scope, request, origin)
        return True
    except AssertionError:
        return False


class HeldQueryRoutes:
    def __init__(self, page, origin):
        self.page, self.origin = page, origin
        self.pending = []
        self.added = 0
        self.taken = 0
        page.add_init_script('window.__queryRouteQueue=[]')

    def hold(self, scope, route):
        identity = query_identity(scope, route.request, self.origin)
        self.added += 1
        self.pending.append((scope, route))
        # request events can precede this callback. Only the callback publishes
        # readiness, after the exact Route object has entered the Python queue.
        self.page.evaluate('(item)=>{window.__queryRouteQueue ??=[];window.__queryRouteQueue.push(item)}',
                           {'sequence': self.added, **identity})

    def take(self, scope, expected_request):
        identity = query_identity(scope, expected_request, self.origin)
        self.page.wait_for_function(READY, arg={'sequence': self.taken + 1, **identity}, timeout=5000)
        assert self.pending and self.pending[0][0] == scope, 'held route order/scope mismatch'
        route = self.pending[0][1]
        # Playwright maps one protocol Request to one cached public API object.
        # The URL handshake alone cannot authorize a different duplicate request.
        assert route.request is expected_request, 'held route request identity mismatch'
        assert query_identity(scope, route.request, self.origin) == identity, 'held query identity changed'
        assert route.request.failure is None, 'held route was cancelled before consumption'
        self.pending.pop(0)
        self.taken += 1
        return route
