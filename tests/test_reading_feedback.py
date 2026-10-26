"""Actual source/version and deletion regressions; no provider or paid calls."""
import hashlib
import json
import pytest
import core
import prepared_content as prepared
import bilingual_translation as bilingual
from content_quality import BODY_POLICY


def verified(entry, html):
    return {**entry, 'content': html, 'prepared_source': 'reader_original_html',
            'fulltext_receipt': {'body_policy': BODY_POLICY, 'requested_url': entry['url'],
                                'html_sha256': hashlib.sha256(html.encode()).hexdigest(), 'checked_at': 1}}


def test_current_subscription_summary_excludes_deleted_sources_without_erasing_history(db, entry):
    core.discover([entry, {**entry, 'id': 2, 'feed_id': 2, 'url': entry['url']+'/deleted'}])
    for eid in (1, 2):
        core.update(eid, state='done', score=8, result='{}')
    assert core.status_summary(1, [1])['counts'] == {'done': 1}
    assert core.status_summary(1, [])['counts'] == {}
    assert core.status_summary(1)['counts'] == {'done': 2}
    with core.connect() as db:
        assert db.execute('SELECT count(*) FROM analyses').fetchone()[0] == 2


@pytest.mark.asyncio
async def test_structured_native_body_is_verified_before_automatic_translation(db, entry, monkeypatch):
    from kaggle_batch import fulltext_source
    bilingual.migrate()
    core.discover([entry])
    core.update(1, state='done', score=8, result='{}')
    calls = []
    monkeypatch.setattr(fulltext_source, 'rule_for', lambda _: ('article', '', False))
    html = '<article><p>A complete source opening.</p><p>The source ending.</p><img data-src="/real.png" src="data:image/svg+xml,placeholder"></article>'
    async def fetch(*args, **kwargs):
        calls.append(True)
        return {'html': html, 'source_text': 'A complete source opening. The source ending.',
                'receipt': {'url': entry['url'], 'requested_url': entry['url'], 'selector': 'article', 'page_sha256': 'test'},
                'content_quality': {}}
    async def current():
        return dict(entry)
    monkeypatch.setattr(fulltext_source, 'fetch', fetch)
    first = await prepared.prepare_translation_body(entry, current)
    assert first['ai']['body_completeness']['status'] == 'verified'
    assert 'real.png' in first['content'] and 'data-src' not in first['content']
    second = await prepared.prepare_translation_body(entry, current)
    assert second['content'] == first['content'] and len(calls) == 1
    assert bilingual.enqueue(first)
    assert not bilingual.enqueue(second)


@pytest.mark.asyncio
async def test_body_check_failures_survive_restart_and_stop_after_two_retries(db, entry, monkeypatch):
    from kaggle_batch import fulltext_source
    clock = [1000.0]
    calls = []
    monkeypatch.setattr(prepared.time, 'time', lambda: clock[0])
    monkeypatch.setattr(fulltext_source, 'rule_for', lambda _: ('article', '', False))
    async def failed(*args, **kwargs):
        calls.append(True)
        raise fulltext_source.FulltextUnavailable('body_selector_missing_or_ambiguous')
    async def current(): return entry
    monkeypatch.setattr(fulltext_source, 'fetch', failed)
    for attempt in (1, 2, 3):
        result = await prepared.verify_original_body(entry, current)
        assert result['attempts'] == attempt
        prepared._BODY_CHECKS.clear()  # Restart loses memory, not the receipt.
        assert await prepared.verify_original_body(entry, current) == result
        clock[0] += 1000
    assert result['retry_exhausted'] and len(calls) == 3
    assert await prepared.verify_original_body(entry, current) == result
    assert len(calls) == 3


@pytest.mark.parametrize('change', ['containers', 'text', 'link'])
def test_only_verified_identical_translation_blocks_reuse_completed_cache(db, entry, change):
    bilingual.migrate()
    core.discover([entry])
    core.update(1, state='done', score=8, result='{}')
    original = '<div><p>Read <a href="https://example.org/a">the detailed report</a> now.</p></div>'
    old = {**entry, 'content': original, 'language': 'en'}
    assert bilingual.enqueue(old)
    with core.connect() as db:
        db.execute("UPDATE bilingual_articles SET status='done'")
        db.execute("UPDATE bilingual_blocks SET translated='阅读 [[t1]]详细报告[[/t1]]。'")
    html = original.replace('<div>', '<article>').replace('</div>', '</article>')
    if change == 'text': html = html.replace(' now.', ' tomorrow.')
    if change == 'link': html = html.replace('/a"', '/different"')
    current = verified({**entry, 'language': 'en'}, html)
    attached = bilingual.attach(current, 1)['translation']
    assert (attached['status'] == 'done') == (change == 'containers')
    if change == 'containers':
        assert not bilingual.enqueue(current)
        assert '<article>' in attached['bilingual_html']
        with core.connect() as db:
            assert db.execute('SELECT count(*) FROM bilingual_articles').fetchone()[0] == 1
            assert db.execute('SELECT count(*) FROM bilingual_usage').fetchone()[0] == 0


def test_restored_body_images_use_shared_proxy_without_mutating_source_or_receipt(db, entry):
    from reader_cover_proxy import reader_body_images, verified_proxy_target
    html = '<p>Current prose.</p><img src="https://example.org/figure.png">'
    item = verified(entry, html)
    item['translation'] = {'status': 'done', 'bilingual_html': html, 'chinese_html': html}
    before = json.dumps(item, sort_keys=True)
    shown = reader_body_images(item, key='test-only-fixed-key')
    from bs4 import BeautifulSoup
    for value in [shown['content'], shown['translation']['bilingual_html'], shown['translation']['chinese_html']]:
        path = BeautifulSoup(value, 'html.parser').img['src']
        assert verified_proxy_target(path.removeprefix('/mf/'), 'test-only-fixed-key') == 'https://example.org/figure.png'
    assert json.dumps(item, sort_keys=True) == before
    assert shown['fulltext_receipt'] == item['fulltext_receipt']
