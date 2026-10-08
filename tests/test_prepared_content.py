import core
import card_translation as cards
import prepared_content as prepared
import hashlib
import pytest


def product(entry):
    return {**entry,'url':'https://www.producthunt.com/products/example'}


def test_feed_poll_cannot_erase_product_preparation_or_translation(db,entry):
    entry = product(entry)
    body = '<h2>产品介绍（Product Hunt）</h2><p>Real source description.</p>'
    body += '<img src="https://ph-files.imgix.net/large.jpg?w=1200">'
    body += '<h3>原始 RSS 简介</h3>' + entry['content']
    prepared.remember(entry,body,'product_page')
    full = {**entry,'content':body}
    cards.enqueue([full])
    with core.connect() as c:
        c.execute("UPDATE card_translations SET status='done',title_zh='示例产品',summary_zh='真实介绍的中文译文。',translated_at=123")
        before = tuple(c.execute('SELECT source_hash,title_zh,translated_at FROM card_translations').fetchone())
    # Simulate the reader returning the original RSS again after its next poll.
    cards.enqueue([entry])
    shown = core.decorate(entry,entry['user_id'])
    assert shown['content'] == body
    assert shown['card']['title'] == '示例产品'
    with core.connect() as c:
        assert tuple(c.execute('SELECT source_hash,title_zh,translated_at FROM card_translations').fetchone()) == before
        assert c.execute('SELECT COUNT(*) FROM usage').fetchone()[0] == 0


def test_changed_product_identity_and_long_new_source_are_not_hidden(db,entry):
    entry = product(entry)
    prepared.remember(entry,'<p>Enriched product body</p>','product_page')
    for changed in ({**entry,'title':'Changed title'}, {**entry,'url':entry['url']+'-different'},
                    {**entry,'user_id':2}, {**entry,'content':'<p>'+('new text '*150)+'</p>'}):
        assert prepared.apply(changed) == changed


def test_image_repair_is_durable_but_does_not_mask_new_article_text(db,entry):
    original = {**entry,'content':'<p>Original text.</p><img alt="Diagram" src="data:image/svg+xml,%3Csvg%3E">'}
    repaired = '<p>Original text.</p><img alt="Diagram" src="https://example.org/figure.png">'
    prepared.remember(original,repaired,'body_images_repaired')
    assert prepared.apply(original)['content'] == repaired
    changed = {**original,'content':original['content'].replace('Original text.','New article text.')}
    assert prepared.apply(changed)['content'] == changed['content']


def test_image_repair_media_only_change_invalidates_bound_body(db,entry):
    from media_repair import needs_repair
    raw = ('<p>Same current article text.</p><img alt="Diagram" '
           'src="data:image/svg+xml,%3Csvg%3E" data-src="https://example.org/a.png">')
    original = {**entry, 'content': raw}
    repaired = '<p>Same current article text.</p><img alt="Diagram" src="https://example.org/a.png">'
    prepared.remember(original,repaired,'body_images_repaired')
    assert prepared.apply(dict(original))['content'] == repaired
    # Matching raw HTML never overrides the existing identity boundaries.
    for changes in ({'user_id': original['user_id'] + 1}, {'url': original['url'] + '/other'},
                    {'title': original['title'] + ' changed'}):
        changed_identity = {**original, **changes}
        assert prepared.apply(changed_identity) == changed_identity
    changed = {**original, 'content': raw.replace('https://example.org/a.png', 'https://example.org/b.png')}
    assert needs_repair(raw) and needs_repair(changed['content'])
    assert prepared.text_hash(raw) == prepared.text_hash(changed['content'])
    assert prepared.apply(changed) == changed
    with core.connect() as connection:
        stored = connection.execute('SELECT input_text_hash FROM prepared_articles WHERE entry_id=? AND user_id=?',
                                    (original['id'], original['user_id'])).fetchone()
    assert stored['input_text_hash'] == hashlib.sha256(raw.encode()).hexdigest()


def test_image_repair_legacy_text_hash_is_not_current_html_proof(db,entry):
    raw = ('<p>Same current article text.</p><img alt="Diagram" '
           'src="data:image/svg+xml,%3Csvg%3E" data-src="https://example.org/a.png">')
    original = {**entry, 'content': raw}
    repaired = '<p>Same current article text.</p><img alt="Diagram" src="https://example.org/a.png">'
    prepared.remember(original,repaired,'body_images_repaired')
    legacy_hash = prepared.text_hash(raw)
    with core.connect() as connection:
        connection.execute('UPDATE prepared_articles SET input_text_hash=? WHERE entry_id=? AND user_id=?',
                           (legacy_hash, original['id'], original['user_id']))
        before = dict(connection.execute('SELECT * FROM prepared_articles WHERE entry_id=? AND user_id=?',
                                         (original['id'], original['user_id'])).fetchone())
    assert prepared.apply(original) == original
    with core.connect() as connection:
        after = dict(connection.execute('SELECT * FROM prepared_articles WHERE entry_id=? AND user_id=?',
                                        (original['id'], original['user_id'])).fetchone())
    assert after == before, 'legacy proof is not migrated, refilled, or deleted during a read'


def original_result(entry):
    return {'html': '<article><p id="opening">Original opening paragraph.</p>'
            '<p>Read <a href="/details">the report</a><img src="/chart.png" onerror="bad()"></p>'
            '<p id="ending">Original final paragraph.</p></article>',
            'source_text': 'Original opening paragraph. Read the report Original final paragraph.',
            'content_quality': {'access': 'unknown', 'information': 'substantive', 'reason_codes': []},
            'receipt': {'url': entry['url'], 'requested_url': entry['url'],
                        'selector': '.entry-content.wp-block-post-content', 'page_sha256': 'synthetic-page'}}


@pytest.mark.asyncio
async def test_original_html_repair_preserves_structure_and_reuses_bound_cache(db, entry, monkeypatch):
    from kaggle_batch import fulltext_source
    from content_quality import body_completeness
    entry = {**entry, 'url': 'https://techcrunch.com/2026/10/07/synthetic/'}
    result = original_result(entry)
    calls = []

    async def fetch(url, **options):
        calls.append((url, options))
        return result

    async def current():
        return dict(entry)

    monkeypatch.setattr(fulltext_source, 'fetch', fetch)
    state = await prepared.verify_original_body(entry, current, analysis_text=result['source_text'],
        previous_translation={'blocks_done': 2, 'source_hash': 'existing-done-hash'})
    assert state['status'] == 'verified'
    shown = prepared.apply(entry)
    assert shown['content'].count('<p') == 3
    assert 'https://techcrunch.com/details' in shown['content']
    assert 'https://techcrunch.com/chart.png' in shown['content']
    assert 'onerror' not in shown['content']
    assert body_completeness(shown)['analysis_text_matches'] is True
    assert shown['fulltext_receipt']['previous_translation_source_hash'] == 'existing-done-hash'
    assert calls == [(entry['url'], {'direct_on_connect_error': True})]
    await prepared.verify_original_body(entry, current)
    assert len(calls) == 1
    for changed in ({**entry, 'user_id': 2}, {**entry, 'url': entry['url']+'other'},
                    {**entry, 'title': 'Changed'}, {**entry, 'content': '<p>New upstream body</p>'}):
        assert prepared.apply(changed) == changed


@pytest.mark.asyncio
async def test_failed_original_checks_are_bounded_and_do_not_claim_missing_text(db, entry, monkeypatch):
    from kaggle_batch import fulltext_source
    from content_quality import body_completeness
    entry = {**entry, 'url': 'https://techcrunch.com/2026/10/07/unavailable/'}
    calls = []

    async def failed(*args, **kwargs):
        calls.append(True)
        raise fulltext_source.FulltextUnavailable('original_http_403')

    async def current():
        return entry

    monkeypatch.setattr(fulltext_source, 'fetch', failed)
    first = await prepared.verify_original_body(entry, current)
    assert first['status'] == 'unverified'
    assert await prepared.verify_original_body(entry, current) == first
    assert len(calls) == 1 and prepared.apply(entry) == entry
    assert body_completeness({**entry, 'content': '<p>' + 'Long summary ' * 1000 + '</p>'})['status'] == 'unverified'
    fallback = body_completeness({**entry, 'prepared_source': 'analysis_source_fallback'})
    assert fallback['status'] == 'unverified' and fallback['structure'] == 'lost'


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['paywall', 'changed', 'different_article'])
async def test_original_repair_cannot_bind_restricted_or_changed_sources(db, entry, monkeypatch, case):
    from kaggle_batch import fulltext_source
    entry = {**entry, 'url': 'https://techcrunch.com/2026/10/07/source-identity/'}
    result = original_result(entry)
    if case == 'paywall':
        result['content_quality']['access'] = 'paid_subscription'
    elif case == 'different_article':
        result['receipt']['url'] = entry['url'] + 'another'

    async def fetch(*args, **kwargs):
        return result

    async def current():
        return {**entry, 'content': 'Changed current body'} if case == 'changed' else entry

    monkeypatch.setattr(fulltext_source, 'fetch', fetch)
    assert (await prepared.verify_original_body(entry, current))['status'] == 'unverified'
    with core.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM prepared_articles').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_translation_preparation_recovers_plaintext_fallback_and_reuses_it(db, entry, monkeypatch):
    import bilingual_translation
    from kaggle_batch import fulltext_source
    bilingual_translation.migrate()
    entry = {**entry, 'url': 'https://techcrunch.com/2026/10/07/translation-source/'}
    core.discover([entry])
    core.update(entry['id'], state='done', score=8.2, source_text='Publisher full text ' * 80,
        content_source='original_url_site_rule', result='{}')
    assert core.decorate(entry, entry['user_id'], include_source_fallback=True)['prepared_source'] == 'analysis_source_fallback'
    calls = []

    async def fetch(*args, **kwargs):
        calls.append(True)
        return original_result(entry)

    async def current():
        return dict(entry)

    monkeypatch.setattr(fulltext_source, 'fetch', fetch)
    shown = await prepared.prepare_translation_body(entry, current)
    assert shown['prepared_source'] == 'reader_original_html'
    assert shown['content'].count('<p') == 3 and shown['content'].count('<img') == 1
    assert shown['ai']['body_completeness']['status'] == 'verified'
    assert (await prepared.prepare_translation_body(entry, current))['content'] == shown['content']
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_translation_preparation_leaves_native_structured_body_unchanged(db, entry, monkeypatch):
    from kaggle_batch import fulltext_source

    async def unexpected(*args, **kwargs):
        pytest.fail('Native structured body must not trigger a publisher fetch')

    async def current():
        pytest.fail('Native structured body needs no recovery re-read')

    monkeypatch.setattr(fulltext_source, 'fetch', unexpected)
    shown = await prepared.prepare_translation_body(entry, current)
    assert shown['content'] == entry['content']


@pytest.mark.asyncio
async def test_cancelled_body_recovery_cannot_write_prepared_source(db, entry, monkeypatch):
    from work_admission import AdmissionStopped
    from kaggle_batch import fulltext_source
    entry = {**entry, 'url': 'https://techcrunch.com/2026/10/07/cancelled-source/'}
    stopped = False

    async def fetch(*args, **kwargs):
        return original_result(entry)

    async def current():
        nonlocal stopped
        stopped = True
        return dict(entry)

    def admission():
        if stopped:
            raise AdmissionStopped('discovery_stopped')

    monkeypatch.setattr(fulltext_source, 'fetch', fetch)
    with pytest.raises(AdmissionStopped):
        await prepared.verify_original_body(entry, current, admission=admission)
    with core.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM prepared_articles').fetchone()[0] == 0
