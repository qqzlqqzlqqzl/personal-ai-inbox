"""No external requests: exact synthetic Miniflux DTOs and local SQLite only."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import core
import preview_worker
import worker

FEEDS = (
    'https://www.kicktraq.com/categories/technology/latest.rss',
    'https://www.kicktraq.com/categories/design/latest.rss',
)


def saved(entry_id=1):
    with core.connect() as db:
        return dict(db.execute('SELECT * FROM analyses WHERE entry_id=?', (entry_id,)).fetchone())


def summary_entry(entry, feed=FEEDS[0], url='https://example.org/synthetic-project'):
    return {**entry, 'url': url, 'content': '<p>Synthetic RSS project description.</p>',
            'feed': {**entry['feed'], 'feed_url': feed}}


@pytest.mark.parametrize('feed', FEEDS)
@pytest.mark.asyncio
async def test_preview_summary_never_discovers_or_repairs(db, entry, monkeypatch, feed):
    entry = summary_entry(entry, feed)
    entry['content'] += '<img src="https://example.org/rss-cover.jpg">'
    core.discover([entry])
    core.update(1, state='budget_paused', attempts=2, next_try=1234)
    cover = AsyncMock(return_value=(None, None))
    repair = AsyncMock(return_value={'repaired': 1})
    monkeypatch.setattr(preview_worker, 'discover_original_cover', cover)
    monkeypatch.setattr(preview_worker, 'needs_repair', lambda _: True)
    monkeypatch.setattr(preview_worker, 'repair_entry', repair)
    calls = []
    def respond(req):
        calls.append((req.method, req.url.path))
        return httpx.Response(200, json=entry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await preview_worker.prepare_one(client, {'entry_id': 1})
    cover.assert_not_awaited()
    repair.assert_not_awaited()
    assert calls == [('GET', '/mf/v1/entries/1')]
    assert result['result'] == 'updated'
    row = saved()
    assert row['cover_url'] == 'https://example.org/rss-cover.jpg'
    assert row['cover_source'] == 'cached_entry_image'
    assert (row['state'], row['attempts'], row['next_try']) == ('budget_paused', 2, 1234)


@pytest.mark.parametrize('feed', FEEDS)
@pytest.mark.parametrize('url', ['https://example.org/synthetic-project',
                                'https://www.producthunt.com/products/synthetic',
                                'https://blog.adafruit.com/synthetic',
                                'https://arxiv.org/abs/0000.00000'])
@pytest.mark.asyncio
async def test_analysis_summary_never_fetches_or_models(db, entry, monkeypatch, feed, url):
    entry = summary_entry(entry, feed, url)
    core.discover([entry])
    # Preserve a prior conservative quality exclusion; this guard does not score.
    quality = '{"recommendation_eligible":false,"reason_codes":["explicit_paid_gate"]}'
    core.update(1, content_quality=quality, content_source='previously_bound_source')
    import product_source, adafruit_source
    cover = AsyncMock(return_value=(None, None))
    add = AsyncMock(return_value=entry['content'])
    enrich = AsyncMock(return_value={'content': entry['content']})
    resolve = AsyncMock(return_value={'html': entry['content'], 'receipt': {'source': 'wrong'}})
    monkeypatch.setattr(worker, 'discover_original_cover', cover)
    monkeypatch.setattr(worker, 'add_original_cover', add)
    monkeypatch.setattr(product_source, 'enrich_product_entry', enrich)
    monkeypatch.setattr(adafruit_source, 'resolve', resolve)
    monkeypatch.setenv('ARK_API_KEY', 'synthetic-only-never-sent')
    calls = []
    def respond(req):
        calls.append((req.method, req.url.path))
        return httpx.Response(200, json=entry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await worker.process_one(client, saved(), core.settings())
    cover.assert_not_awaited(); add.assert_not_awaited()
    enrich.assert_not_awaited(); resolve.assert_not_awaited()
    assert calls == [('GET', '/mf/v1/entries/1')]
    after = saved()
    assert after['state'] == 'requires_fulltext_adapter'
    assert after['content_source'] == 'previously_bound_source'
    assert after['content_quality'] == quality
    assert after['result'] is None and after['score'] is None
    with core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM usage').fetchone()[0] == 0


@pytest.mark.parametrize('feed', FEEDS)
@pytest.mark.asyncio
async def test_bridge_summary_never_enters_fulltext_or_prepared(db, entry, monkeypatch, feed):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/kaggle_batch'))
    import cloud_bridge, fulltext_source, prepared_content, card_translation
    entry = summary_entry(entry, feed)
    core.discover([entry])
    card_translation.migrate()
    fetch = AsyncMock(side_effect=AssertionError('No publisher body request'))
    prepared = Mock(side_effect=AssertionError('No cached publisher substitution'))
    monkeypatch.setattr(fulltext_source, 'fetch', fetch)
    monkeypatch.setattr(prepared_content, 'apply', prepared)
    monkeypatch.setattr(cloud_bridge, 'load_inbox', lambda *a, **kw: (core, worker, card_translation))
    monkeypatch.setattr(worker, 'mf_get', AsyncMock(return_value=entry))
    result = await cloud_bridge.prepare_sample('.', 1, analysis_only=True)
    fetch.assert_not_awaited(); prepared.assert_not_called()
    assert result['samples'] == []
    assert result['skipped'][0]['state'] == 'requires_fulltext_adapter'
    assert saved()['state'] == 'requires_fulltext_adapter'
    assert saved()['content_source'] is None


def test_feed_identity_is_exact_and_not_entry_domain(entry):
    from feed_consumption import is_summary_only_feed, POLICY_VERSION
    assert POLICY_VERSION == "kicktraq-rss-preview-only-v1"
    for feed in FEEDS:
        assert is_summary_only_feed(summary_entry(entry, feed, 'https://unrelated.example/item'))
    for feed in ('https://www.kicktraq.com/categories/technology/ending.rss',
                 'https://www.kicktraq.com/categories/technology/latest.rss?x=1',
                 'https://www.kicktraq.com.evil.example/categories/technology/latest.rss',
                 'http://www.kicktraq.com/categories/technology/latest.rss',
                 FEEDS[0] + '/', ' ' + FEEDS[0], None, ''):
        assert not is_summary_only_feed(summary_entry(entry, feed, 'https://www.kicktraq.com/projects/synthetic'))
    for feed in (None, [], 'not-a-feed', {}):
        assert not is_summary_only_feed({**entry, 'feed': feed})


@pytest.mark.parametrize('feed', [url.replace('https://', 'http://', 1) for url in FEEDS])
@pytest.mark.asyncio
async def test_observed_http_self_is_unverified_not_generic_fetch(db, entry, monkeypatch, feed):
    entry = summary_entry(entry, feed)
    core.discover([entry])
    cover = AsyncMock(side_effect=AssertionError('No HTML for unverified feed identity'))
    monkeypatch.setattr(preview_worker, 'discover_original_cover', cover)
    monkeypatch.setattr(worker, 'discover_original_cover', cover)
    calls = []
    def respond(req):
        calls.append((req.method, req.url.path))
        return httpx.Response(200, json=entry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        preview = await preview_worker.prepare_one(client, {'entry_id': 1})
        await worker.process_one(client, saved(), core.settings())
    cover.assert_not_awaited()
    assert preview['error'] == 'UnverifiedFeedIdentity'
    assert saved()['state'] == 'requires_source_review'
    assert saved()['content_source'] is None
    assert calls == [('GET', '/mf/v1/entries/1')] * 2


@pytest.mark.parametrize('feed', FEEDS)
@pytest.mark.asyncio
async def test_preview_does_not_keep_old_non_rss_cover(db, entry, monkeypatch, feed):
    entry = summary_entry(entry, feed)
    core.discover([entry])
    core.update(1, cover_url='https://example.org/old-page.jpg', cover_source='page_hero')
    cover = AsyncMock(side_effect=AssertionError('No HTML'))
    monkeypatch.setattr(preview_worker, 'discover_original_cover', cover)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=entry))) as client:
        result = await preview_worker.prepare_one(client, {'entry_id': 1})
    assert result['result'] == 'text_only'
    assert saved()['cover_url'] is None and saved()['cover_source'] is None
    cover.assert_not_awaited()


@pytest.mark.asyncio
async def test_summary_analysis_rechecks_stop_before_mutation(db, entry, monkeypatch):
    from work_admission import AdmissionStopped
    entry = summary_entry(entry)
    core.discover([entry])
    stopped = False
    def admission():
        if stopped:
            raise AdmissionStopped('synthetic stop')
    def respond(req):
        nonlocal stopped
        stopped = True
        return httpx.Response(200, json=entry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AdmissionStopped):
            await worker.process_one(client, saved(), core.settings(), admission=admission)
    assert saved()['state'] == 'pending' and saved()['content_source'] is None


@pytest.mark.parametrize('mode', ['normal', 'http_self', 'owner_changed', 'url_changed', 'id_changed',
                                 'source_hash_changed', 'source_text_changed', 'transaction_failure', 'done_card'])
@pytest.mark.asyncio
async def test_bridge_source_boundary_and_cas(db, entry, monkeypatch, mode):
    import cloud_bridge, fulltext_source, prepared_content, card_translation
    entry = summary_entry(entry, FEEDS[1])
    core.discover([entry]); card_translation.migrate()
    if mode == 'done_card':
        core.update(1, state='done', score=8.2, result='{"kept":true}')
        with core.connect() as conn:
            conn.execute("INSERT INTO card_translations(entry_id,user_id,source_hash,original_title,excerpt,source_kind,status,next_try,attempts) VALUES(1,1,'synthetic-hash','Synthetic title','Synthetic RSS summary','rss','pending',0,0) ON CONFLICT(entry_id) DO UPDATE SET status='pending',attempts=0,next_try=0")
    if mode == 'transaction_failure':
        with core.connect() as conn:
            conn.execute("CREATE TRIGGER fail_feed_review BEFORE UPDATE OF state ON analyses WHEN NEW.state='requires_fulltext_adapter' BEGIN SELECT RAISE(ABORT,'synthetic transaction failure'); END")
    if mode == 'http_self': entry['feed']['feed_url'] = FEEDS[1].replace('https://', 'http://', 1)
    if mode == 'owner_changed': entry['user_id'] = 2
    if mode == 'id_changed': entry['id'] = 2
    if mode == 'url_changed': entry['url'] = 'https://example.org/changed'
    async def read(*args, **kwargs):
        if mode in ('source_hash_changed', 'source_text_changed'):
            field = 'content_hash' if mode == 'source_hash_changed' else 'source_text'
            core.update(1, **{field: 'concurrent winner'})
        return entry
    fetch = AsyncMock(side_effect=AssertionError('No project HTML'))
    prepared = Mock(side_effect=AssertionError('No publisher substitution'))
    enqueue = Mock(side_effect=AssertionError('No excerpt queued as complete input'))
    monkeypatch.setattr(fulltext_source, 'fetch', fetch)
    monkeypatch.setattr(prepared_content, 'apply', prepared)
    monkeypatch.setattr(card_translation, 'enqueue', enqueue)
    monkeypatch.setattr(cloud_bridge, 'load_inbox', lambda *a, **kw: (core, worker, card_translation))
    monkeypatch.setattr(worker, 'mf_get', AsyncMock(side_effect=read))
    if mode == 'transaction_failure':
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            await cloud_bridge.prepare_sample('.', 1, independent_cards=True)
        assert saved()['state'] == 'pending' and saved()['content_source'] is None
    else:
        result = await cloud_bridge.prepare_sample('.', 1, independent_cards=True)
        assert result['samples'] == []
        row = saved()
        if mode.startswith('source_'):
            assert row['state'] == 'pending' and row['content_source'] is None
            assert result['skipped'][0]['state'] == 'source_changed_during_feed_review'
        elif mode in ('owner_changed', 'url_changed', 'id_changed', 'http_self'):
            assert row['state'] == 'requires_source_review'
        elif mode == 'done_card':
            assert row['state'] == 'done' and row['score'] == 8.2 and row['result'] == '{"kept":true}'
        else:
            assert row['state'] == 'requires_fulltext_adapter'
    fetch.assert_not_awaited(); prepared.assert_not_called(); enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_other_feed_keeps_fulltext_pipeline(db, entry, monkeypatch):
    # An entry URL on Kicktraq does not establish either of the fixed feed identities.
    entry = summary_entry(entry, 'https://example.org/feed', 'https://www.kicktraq.com/projects/synthetic')
    core.discover([entry])
    monkeypatch.delenv('ARK_API_KEY', raising=False)
    cover = AsyncMock(return_value=(None, None))
    monkeypatch.setattr(worker, 'discover_original_cover', cover)
    monkeypatch.setattr(worker, 'add_original_cover', AsyncMock(side_effect=lambda client, entry, current, *a, **kw: current))
    calls = []
    html = '<p>' + 'Synthetic complete source text with technical details. ' * 10 + '</p>'
    def respond(req):
        calls.append(req.url.path)
        return httpx.Response(200, json={'content': html} if req.url.path.endswith('/fetch-content') else entry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await worker.process_one(client, saved(), core.settings())
    assert calls == ['/mf/v1/entries/1', '/mf/v1/entries/1/fetch-content']
    cover.assert_awaited_once()
    assert saved()['state'] == 'waiting_model' and saved()['content_source'] == 'original_url'


@pytest.mark.parametrize('feed', FEEDS)
@pytest.mark.asyncio
async def test_bridge_fulltext_entrypoint_is_closed(db, entry, monkeypatch, feed):
    import cloud_bridge, fulltext_source, prepared_content, card_translation
    entry = summary_entry(entry, feed)
    core.discover([entry]); card_translation.migrate()
    fetch = AsyncMock(side_effect=AssertionError('Observed forbidden fulltext entrypoint'))
    monkeypatch.setattr(fulltext_source, 'fetch', fetch)
    monkeypatch.setattr(prepared_content, 'apply', lambda e, **kw: e)
    monkeypatch.setattr(cloud_bridge, 'load_inbox', lambda *a, **kw: (core, worker, card_translation))
    monkeypatch.setattr(worker, 'mf_get', AsyncMock(return_value=entry))
    result = await cloud_bridge.prepare_sample('.', 1, analysis_only=True)
    fetch.assert_not_awaited()
    assert not result['samples']
    assert saved()['state'] == 'requires_fulltext_adapter'
