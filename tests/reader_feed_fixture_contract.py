"""Extract real synthetic producers without starting HTTP, Playwright or browsers."""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def seed_harness():
    tree = ast.parse((ROOT / 'review_reader_harness.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Harness')
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    assignments = [n for n in init.body if isinstance(n, ast.Assign) and len(n.targets) == 1 and
                   ast.unparse(n.targets[0]) in ('self.categories', 'self.feeds')]
    assert len(assignments) == 2
    h = SimpleNamespace(calls=[], writes=[], custom=None, catalog=[])
    exec(compile(ast.Module(body=assignments, type_ignores=[]), '<real-harness-seed>', 'exec'), {'self': h})
    return h, cls


def producers():
    h, cls = seed_harness()
    result = {'harness-default': copy.deepcopy(h.feeds)}
    route = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'route')
    ns = {'urlsplit': urlsplit}
    exec(compile(ast.Module(body=[route], type_ignores=[]), '<real-harness-route>', 'exec'), ns)
    replies = []
    for _ in range(2):
        request = SimpleNamespace(url='http://127.0.0.1/mf/v1/ai/subscribe', method='POST',
                                  post_data_json={'url': 'https://example.test/synthetic.xml', 'category_id': 1})
        ns['route'](h, SimpleNamespace(request=request, fulfill=lambda **kw: replies.append(kw['json'])))
    result['harness-subscribe'] = copy.deepcopy(replies)
    h, _ = seed_harness()
    query = ast.parse((ROOT / 'query_result_ownership_browser.py').read_text())
    function = next(n for n in query.body if isinstance(n, ast.FunctionDef) and n.name == 'entries')
    icons = [n for n in query.body if isinstance(n, ast.Assign) and len(n.targets) == 1 and
             ast.unparse(n.targets[0]) == "h.feeds[0]['icon']"]
    ns = {'h': h}
    exec(compile(ast.Module(body=icons+[function], type_ignores=[]), '<real-query-producer>', 'exec'), ns)
    result['query'] = [entry['feed'] for entry in ns['entries'](101, 24)]
    h, _ = seed_harness()
    reading = ast.parse((ROOT / 'reading_focus_browser.py').read_text())
    statements = [n for n in ast.walk(reading) if isinstance(n, ast.Assign) and len(n.targets) == 1 and
                  ast.unparse(n.targets[0]) in ("h.feeds[0]['icon']", 'h.entries')]
    exec(compile(ast.Module(body=statements, type_ignores=[]), '<real-reading-producer>', 'exec'), {'h': h})
    result['reading'] = [entry['feed'] for entry in h.entries]
    from reader_loading_fixture import Fixture
    fixture = Fixture.__new__(Fixture)
    fixture.base = 'http://127.0.0.1:43210'
    result['performance'] = [fixture.entry(i, deferred=deferred)['feed'] for deferred in (False, True) for i in (1, 7, 31, 72)]
    from quality_consumer_fixture import make_entries
    h, _ = seed_harness()
    result['quality'] = [entry['feed'] for entry in make_entries(h.feeds[0])]
    return result


def validate(feed, *, full_native_icon=False):
    assert type(feed.get('id')) is int and feed['id'] > 0
    icon = feed.get('icon')
    assert type(icon) is dict
    assert type(icon.get('feed_id')) is int and icon['feed_id'] == feed['id']
    assert type(icon.get('icon_id')) is int and icon['icon_id'] == 0
    if full_native_icon:
        assert set(icon) == {'feed_id', 'icon_id', 'external_icon_id'} and icon['external_icon_id'] == ''
    elif 'external_icon_id' in icon:
        assert type(icon['external_icon_id']) is str


if __name__ == '__main__':
    print(json.dumps(producers()))
