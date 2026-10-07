"""Bounded, owner-scoped excerpt memoization preserves the legacy card source."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json

from bs4 import BeautifulSoup
import pytest

import card_translation as cards
import core
import prepared_content


def legacy_source(entry, model):
    soup = BeautifulSoup((entry.get('content') or '')[:50000], 'html.parser')
    for node in soup(['script', 'style', 'noscript', 'code', 'pre']):
        node.decompose()
    paragraphs = [p.get_text(' ', strip=True) for p in soup.find_all('p')]
    excerpt = ' '.join(p for p in paragraphs if p)[:900] or soup.get_text(' ', strip=True)[:900]
    kind = 'product_page' if soup.find(['h2', 'h3'], string='产品介绍（Product Hunt）') else 'source_excerpt'
    title = str(entry.get('title') or '')[:600]
    fingerprint = hashlib.sha256(json.dumps(
        [cards.VERSION, model, title, excerpt], ensure_ascii=False).encode()).hexdigest()
    return title, excerpt, kind, fingerprint


@pytest.mark.parametrize('body', [
    None, '', '<p>One &amp; two.</p><p>Three <b>four</b>.</p>',
    '<p>  </p>Text without useful paragraphs',
    '<p>A<script>bad()</script><style>hidden</style><noscript>hidden</noscript>'
    '<code>code</code><pre>preformatted</pre>B</p>',
    '<h2>产品介绍（Product Hunt）</h2><p>介绍中文与emoji 🧪</p>',
    '<h3>产品介绍（Product Hunt）</h3><div>Fallback description</div>',
    '<p>' + '文🧪A' * 18000 + '</p><h2>产品介绍（Product Hunt）</h2>',
    ' ' * 49999 + '<p>A boundary-cut tag</p>',
])
def test_cold_and_warm_source_exactly_match_legacy(entry, body):
    entry = {**entry, 'content': body, 'title': 'Title 🧪' * 150}
    cache = cards.SourceExcerptCache()
    expected = legacy_source(entry, 'synthetic-model')
    assert cards.source_card(entry, 'synthetic-model') == expected
    assert cards.source_card(entry, 'synthetic-model', excerpt_cache=cache) == expected
    assert cards.source_card(entry, 'synthetic-model', excerpt_cache=cache) == expected


def test_unique_request_has_no_hits_but_next_request_reuses_excerpts(entry, monkeypatch):
    calls = []
    original = cards._source_excerpt

    def parse(html):
        calls.append(html)
        return original(html)

    monkeypatch.setattr(cards, '_source_excerpt', parse)
    entries = [{**entry, 'id': i, 'content': f'<p>Unique source {i}</p>'} for i in range(30)]
    cache = cards.SourceExcerptCache()
    cold = [cards.source_card(item, 'm', excerpt_cache=cache) for item in entries]
    assert len(calls) == 30
    warm = [cards.source_card(item, 'm', excerpt_cache=cache) for item in entries]
    assert warm == cold and len(calls) == 30
    fresh_request_cache = cards.SourceExcerptCache()
    assert [cards.source_card(item, 'm', excerpt_cache=fresh_request_cache) for item in entries] == cold
    assert len(calls) == 60


def test_current_owner_source_title_model_and_version_boundaries(entry, monkeypatch):
    cache = cards.SourceExcerptCache()
    calls = []
    original = cards._source_excerpt
    monkeypatch.setattr(cards, '_source_excerpt', lambda html: (calls.append(html), original(html))[1])
    first = cards.source_card(entry, 'm1', excerpt_cache=cache)
    for changed, model in [({**entry, 'title': 'Changed title'}, 'm1'), (entry, 'm2')]:
        value = cards.source_card(changed, model, excerpt_cache=cache)
        assert value == legacy_source(changed, model) and value[3] != first[3]
    assert len(calls) == 1
    for changed in ({**entry, 'content': '<p>Changed source.</p>'}, {**entry, 'user_id': 2}):
        assert cards.source_card(changed, 'm1', excerpt_cache=cache) == legacy_source(changed, 'm1')
    assert len(calls) == 3
    monkeypatch.setattr(cards, 'VERSION', 'synthetic-new-version')
    assert cards.source_card(entry, 'm1', excerpt_cache=cache) == legacy_source(entry, 'm1')
    assert len(calls) == 4
    invalid_owner = {**entry, 'user_id': True}
    cards.source_card(invalid_owner, 'm1', excerpt_cache=cache)
    cards.source_card(invalid_owner, 'm1', excerpt_cache=cache)
    assert len(calls) == 6


def test_trailing_input_outside_legacy_limit_does_not_change_source(entry, monkeypatch):
    cache = cards.SourceExcerptCache()
    prefix = '<p>Shared source</p>' + ' ' * 50000
    a = {**entry, 'content': prefix + 'tail A'}
    b = {**entry, 'content': prefix + 'tail B'}
    assert cards.source_card(a, 'm', excerpt_cache=cache) == legacy_source(b, 'm')
    monkeypatch.setattr(cards, '_source_excerpt', lambda html: pytest.fail('Expected exact-prefix hit'))
    assert cards.source_card(b, 'm', excerpt_cache=cache) == legacy_source(b, 'm')


def test_bounded_storage_keeps_only_digests_excerpt_and_kind(entry):
    cache = cards.SourceExcerptCache(max_entries=3, max_bytes=4096)
    for i in range(20):
        html = f'<p>Excerpt {i}</p><script>' + ('NEVER_STORE_COMPLETE_HTML' * 1500) + '</script>'
        cards.source_card({**entry, 'content': html}, 'm', excerpt_cache=cache)
        assert len(cache._items) <= 3 and cache._bytes <= 4096
    assert cache._items
    for key, (value, _) in cache._items.items():
        assert len(key[-1]) == 32
        assert key[2] == entry['user_id']
        assert all('NEVER_STORE_COMPLETE_HTML' not in part for part in key if isinstance(part, str))
        assert len(value[0]) <= 900 and value[1] == 'source_excerpt'
    tiny = cards.SourceExcerptCache(max_entries=3, max_bytes=1)
    assert cards.source_card(entry, 'm', excerpt_cache=tiny) == legacy_source(entry, 'm')
    assert tiny._bytes == 0 and not tiny._items
    cache.clear()
    assert cache._bytes == 0 and not cache._items


def test_concurrent_reads_remain_equivalent_and_bounded(entry):
    cache = cards.SourceExcerptCache(max_entries=8, max_bytes=16384)
    entries = [{**entry, 'content': f'<p>Concurrent source {i % 12}</p>'} for i in range(80)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        actual = list(pool.map(lambda item: cards.source_card(item, 'm', excerpt_cache=cache), entries))
    assert actual == [legacy_source(item, 'm') for item in entries]
    assert len(cache._items) <= 8 and cache._bytes <= 16384


@pytest.mark.parametrize('change', ['source', 'model', 'prepared'])
def test_memo_never_bypasses_real_enqueue_change(db, entry, change):
    cache = cards.SourceExcerptCache()
    cards.enqueue([entry], priority=30, excerpt_cache=cache)
    with core.connect() as connection:
        connection.execute("UPDATE card_translations SET status='done',title_zh='缓存中文标题',summary_zh='缓存摘要'")
        original = dict(connection.execute('SELECT * FROM card_translations').fetchone())
    if change == 'source':
        entry = {**entry, 'content': '<p>A genuinely different source.</p>'}
    elif change == 'model':
        core.save_settings({'model': 'changed-model'})
    else:
        prepared_content.remember(entry, '<h2>产品介绍（Product Hunt）</h2><p>Prepared description.</p>', 'product_page')
    batch = prepared_content.prepare_many([entry])
    cards.enqueue([entry], priority=30, prepared_batch=batch,
                  settings_snapshot=core.settings(), excerpt_cache=cache)
    with core.connect() as connection:
        current = dict(connection.execute('SELECT * FROM card_translations').fetchone())
        assert current['status'] == 'pending'
        assert current['source_hash'] != original['source_hash']
        assert current['source_hash'] == legacy_source(batch.apply(entry), core.settings()['model'])[3]
        assert connection.execute('SELECT COUNT(*) FROM card_translation_versions').fetchone()[0] == 1
