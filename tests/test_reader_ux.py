"""Regression tests for the personal overlay / native-reader boundary."""
import json
from datetime import datetime
import httpx
import pytest
import pytest_asyncio
import api
import core
import card_translation


@pytest_asyncio.fixture
async def multi_reader(db, entry, model_result, monkeypatch):
    monkeypatch.delenv('MINIFLUX_API_KEY', raising=False)
    records = {}
    dates = ['2026-09-01T08:00:00+08:00', '2026-09-01T01:00:00Z',
             '2026-09-01T00:00:00Z', '2026-08-31T23:00:00Z']
    for i in range(1, 5):
        e = {**entry, 'id': i, 'title': f'Article {i}', 'published_at': dates[i-1]}
        records[i] = e
        core.discover([e])
        core.update(i, state='done', score=6 + i % 3, technical_score=5 + i,
                    business_score=10 - i, result=json.dumps(model_result))
    feeds = [{'id': 1, 'hide_globally': False, 'category': {'hide_globally': False}}]
    def upstream(req):
        path = req.url.path
        if path.endswith('/v1/me'):
            return httpx.Response(200, json={'id': 1})
        if path.endswith('/entries/ids'):
            return httpx.Response(200, json={'entry_ids': list(records), 'total': len(records)})
        if path.endswith('/v1/feeds'):
            return httpx.Response(200, json=feeds)
        if '/entries/' in path:
            return httpx.Response(200, json=records[int(path.rsplit('/', 1)[-1])])
        return httpx.Response(404)
    async with api.lifespan(api.app):
        await api.app.state.client.aclose()
        api.app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),
            base_url='http://testserver', headers={'X-Auth-Token': 'isolated'}) as c:
            yield c, records, feeds


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['time', 'score', 'technical', 'business'])
@pytest.mark.parametrize('direction', ['asc', 'desc'])
async def test_ai_direction_and_pagination(multi_reader, field, direction):
    c, records, _ = multi_reader
    values = {
        'time': lambda i: datetime.fromisoformat(records[i]['published_at']).timestamp(),
        'score': lambda i: 6 + i % 3,
        'technical': lambda i: 5 + i,
        'business': lambda i: 10 - i,
    }
    expected = sorted(records, key=lambda i: (values[field](i), i), reverse=direction == 'desc')
    actual = []
    for offset in (0, 2):
        r = await c.get('/mf/v1/entries', params={'ai_view': 'recommended', 'ai_sort': field,
            'direction': direction, 'ai_min': 0, 'limit': 2, 'offset': offset})
        assert r.status_code == 200
        actual.extend(e['id'] for e in r.json()['entries'])
    assert actual == expected
    assert len(actual) == len(set(actual))


@pytest.mark.asyncio
@pytest.mark.parametrize('params', [{'direction': 'sideways'}, {'ai_sort': 'bad'},
                                    {'direction': 'ASC;DROP TABLE analyses'}])
async def test_invalid_ai_sort_rejected(multi_reader, params):
    c, _, _ = multi_reader
    r = await c.get('/mf/v1/entries', params={'ai_view': 'recommended', **params})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_hidden_category_obeyed(multi_reader):
    c, _, feeds = multi_reader
    feeds[0]['category']['hide_globally'] = True
    r = await c.get('/mf/v1/entries?ai_view=recommended&globally_visible=true')
    assert r.json()['total'] == 0
    r = await c.get('/mf/v1/entries?ai_view=recommended&globally_visible=false')
    assert r.json()['total'] == 4


@pytest.mark.parametrize('state', ['pending', 'processing', 'error', 'budget_paused', 'waiting_model'])
def test_disabled_translation_never_claims_waiting(db, entry, state):
    core.discover([entry])
    with core.connect() as c:
        c.execute('UPDATE card_translations SET status=?', (state,))
    core.save_settings({'translation_enabled': False})
    result = card_translation.attach(entry, 1)
    assert result['title'] == entry['title']
    assert result['card']['status'] == 'disabled'
    assert result['card']['enabled'] is False
    # Disabling presentation must not destroy queue state or cached content.
    core.save_settings({'translation_enabled': True})
    assert card_translation.attach(entry, 1)['card']['status'] == state


def test_disabled_translation_preserves_completed_cache(db, entry):
    core.discover([entry])
    with core.connect() as c:
        c.execute("UPDATE card_translations SET status='done',title_zh='工程案例',summary_zh='缓存降低了延迟'")
    core.save_settings({'translation_enabled': False})
    card = card_translation.attach(entry, 1)['card']
    assert card['status'] == 'done' and card['language'] == 'zh-CN'
    assert card['title'] == '工程案例'


def test_changed_title_cannot_claim_translated(db, entry):
    core.discover([entry])
    with core.connect() as c:
        c.execute("UPDATE card_translations SET status='done',title_zh='旧标题',summary_zh='旧简介'")
    card = card_translation.attach({**entry, 'title': 'A different title'}, 1)['card']
    assert card['status'] == 'pending' and not card.get('title')
