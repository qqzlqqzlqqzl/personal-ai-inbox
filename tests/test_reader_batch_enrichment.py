"""Request-local batch reads retain single-entry identity, notes and source rules."""
from contextlib import contextmanager
from types import SimpleNamespace
import copy

import pytest

import api
import card_translation as cards
import core
import prepared_content as prepared
import processing_status


@contextmanager
def trace_connections(monkeypatch):
    original = core.connect
    connections, statements = [], []

    @contextmanager
    def traced():
        connections.append(True)
        with original() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    with monkeypatch.context() as patch:
        patch.setattr(core, 'connect', traced)
        yield connections, statements


def warm(entries):
    core.discover(entries)
    with core.connect() as connection:
        connection.execute("UPDATE analyses SET state='done',result='{}',score=8")
        connection.execute("""UPDATE card_translations SET status='done',priority=40,
            title_zh='缓存中文标题',summary_zh='缓存中文摘要',translated_at=123""")


@pytest.mark.parametrize('count', [1, 30, 100])
def test_warm_enrichment_has_four_connections_and_bounded_metadata_reads(db, entry, monkeypatch, count):
    entries = [{**entry, 'id': i, 'title': f'Article {i}', 'content': f'<p>Source {i}</p>'}
               for i in range(1, count + 1)]
    warm(entries)
    monkeypatch.setattr(processing_status, 'observe', lambda *args, **kwargs: {})
    with trace_connections(monkeypatch) as (connections, statements):
        result = api.enrich_reader_entries(entries, 1, recommended=True)
    assert len(result) == count
    assert len(connections) == 4
    selects = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
    assert len(selects) == count + 4  # Per-card transactional checks remain intentional.
    assert sum('FROM prepared_articles' in sql for sql in selects) == 1
    assert sum('FROM settings' in sql for sql in selects) == 1
    assert not [sql for sql in statements if sql.split()[0].upper() in {
        'BEGIN', 'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'COMMIT', 'ROLLBACK'}]
    assert all(item['content_deferred'] and item['content'] == '' for item in result)
    assert all(item['card']['status'] == 'done' for item in result)


def test_batch_decoration_equals_single_entries_and_preserves_missing_analysis_notes(db, entry, monkeypatch):
    entries = [{**entry, 'id': i, 'title': f'Article {i}'} for i in range(1, 5)]
    warm(entries[:2])
    with core.connect() as connection:
        connection.executemany('INSERT INTO entry_notes VALUES (?,?,?,?,?)', [
            (1, 1, 'Attached note', 1, 11), (1, 3, 'Standalone note', 1, 33), (1, 4, '   ', 1, 44)])
    monkeypatch.setattr(processing_status, 'time', SimpleNamespace(time=lambda: 12345))
    before = copy.deepcopy(entries)
    expected = [core.decorate(item, 1, processing_evidence={}) for item in entries]
    prepared_batch = prepared.prepare_many(entries)
    batch = core.load_reader_batch(entries, 1, prepared_batch=prepared_batch, settings_snapshot=core.settings())
    with trace_connections(monkeypatch) as (connections, statements):
        actual = [core.decorate(item, 1, processing_evidence={}, batch=batch) for item in entries]
    assert actual == expected and entries == before
    assert not connections and not statements
    assert actual[2]['ai']['has_note'] and actual[2]['ai']['note_updated_at'] == 33
    assert not actual[3]['ai']['has_note'] and actual[3]['ai']['note_updated_at'] == 44


def test_batch_reads_never_reuse_other_owner_metadata(db, entry, monkeypatch):
    warm([entry])
    with core.connect() as connection:
        connection.execute('INSERT INTO entry_notes VALUES (1,1,?,?,?)', ('Private owner-one note', 1, 2))
    second = {**entry, 'user_id': 2}
    prepared_batch = prepared.prepare_many([entry, second])
    batch = core.load_reader_batch([entry, second], prepared_batch=prepared_batch, settings_snapshot=core.settings())
    first = core.decorate(entry, 1, batch=batch)
    other = core.decorate(second, 2, batch=batch)
    assert first['ai']['has_note'] and first['card']['status'] == 'done'
    assert other['ai']['state'] == 'pending' and not other['ai']['has_note']
    assert other['card']['status'] == 'pending' and 'title' not in other['card']


def test_prepare_once_keeps_source_checks_and_only_request_local_reuse(db, entry, monkeypatch):
    prepared.remember(entry, '<h2>产品介绍（Product Hunt）</h2><p>Product details.</p>', 'product_page')
    original = prepared._apply_row
    calls = []
    monkeypatch.setattr(prepared, '_apply_row', lambda item, row: (calls.append(item['id']), original(item, row))[1])
    batch = prepared.prepare_many([entry])
    assert len(calls) == 1
    assert batch.apply(entry)['prepared_source'] == 'product_page'
    cards.enqueue([entry], prepared_batch=batch, settings_snapshot=core.settings())
    metadata = core.load_reader_batch([entry], 1, prepared_batch=batch, settings_snapshot=core.settings())
    assert core.decorate(entry, 1, batch=metadata)['prepared_source'] == 'product_page'
    assert len(calls) == 1
    entry['content'] = '<p>' + 'New longer source. ' * 100 + '</p>'
    assert batch.apply(entry)['content'] == entry['content']
    assert len(calls) == 2
    fresh = prepared.prepare_many([entry])
    assert fresh.apply(entry)['content'] == entry['content'] and len(calls) == 3


@pytest.mark.parametrize('change', ['owner', 'url', 'title', 'text', 'attribution'])
def test_prepared_batch_respects_linked_original_identity_and_html_hash(db, entry, change):
    entry = {**entry, 'content': '<p>Follow <a href="https://example.org/original">source</a></p>'}
    body = '<p>Linked original full text.</p>'
    prepared.remember(entry, body, 'adafruit_linked_original', receipt={'url': 'https://example.org/original'})
    changed = dict(entry)
    if change == 'owner':
        changed['user_id'] = 2
    elif change == 'url':
        changed['url'] += '/changed'
    elif change == 'title':
        changed['title'] = 'New title'
    elif change == 'text':
        changed['content'] = '<p>New article</p>'
    else:
        changed['content'] = entry['content'].replace('/original', '/other')
    batch = prepared.prepare_many([entry, changed])
    assert batch.apply(entry) == prepared.apply(entry)
    assert batch.apply(entry)['content'] == body
    assert batch.apply(changed) == changed == prepared.apply(changed)


def test_prepared_batch_preserves_image_repair_invalidations(db, entry):
    original = {**entry, 'content': '<p>Original text.</p><img alt="Diagram" src="data:image/svg+xml,%3Csvg%3E">'}
    repaired = '<p>Original text.</p><img src="https://example.org/figure.png">'
    prepared.remember(original, repaired, 'body_images_repaired')
    changed = {**original, 'content': original['content'].replace('Original text.', 'Changed text.')}
    batch = prepared.prepare_many([original, changed])
    assert batch.apply(original)['content'] == repaired
    assert batch.apply(changed) == changed


def test_current_source_quality_binding_is_retained_in_batch(db, entry):
    from content_quality import assess
    warm([entry])
    core.update(entry['id'], source_text='Captured source', content_hash='original-hash')
    with core.connect() as connection:
        row = dict(connection.execute('SELECT * FROM analyses').fetchone())
    quality = assess(url=entry['url'], html_body='<div class="paywall">This article is for paid subscribers only.</div>',
                     extraction_state='available', observed_at=123)
    assert core.record_content_quality(row, quality)
    changed = {**entry, 'url': 'https://example.org/changed-current-source'}
    prepared_batch = prepared.prepare_many([entry, changed])
    batch = core.load_reader_batch([entry, changed], 1, prepared_batch=prepared_batch, settings_snapshot=core.settings())
    assert core.decorate(entry, 1, batch=batch)['ai']['content_quality']['recommendation_eligible'] is False
    assert core.decorate(changed, 1, batch=batch)['ai']['content_quality']['recommendation_eligible'] is None


def test_batch_reads_chunk_large_inputs_without_owner_cross_product(db, entry, monkeypatch):
    entries = [{**entry, 'id': i} for i in range(1, 802)]
    prepared_batch = prepared.prepare_many(entries)
    with trace_connections(monkeypatch) as (connections, statements):
        batch = core.load_reader_batch(entries, 1, prepared_batch=prepared_batch, settings_snapshot=core.settings())
    assert len(connections) == 2  # Explicit settings read plus one batch connection.
    assert len(batch['analyses']) == len(batch['notes']) == len(batch['cards']) == 0
    assert sum('FROM analyses a' in sql for sql in statements) == 3


def test_prepared_reuse_does_not_skip_admission(db, entry):
    from work_admission import AdmissionStopped
    batch = prepared.prepare_many([entry])

    def stopped():
        raise AdmissionStopped('synthetic stop')

    with pytest.raises(AdmissionStopped):
        batch.apply(entry, admission=stopped)
    with pytest.raises(AdmissionStopped):
        cards.enqueue([entry], prepared_batch=batch, settings_snapshot=core.settings(), admission=stopped)


def test_mutable_prepared_receipt_cannot_poison_duplicate_entry(db, entry):
    receipt = {'url': 'https://example.org/original', 'nested': {'evidence': ['original']}}
    prepared.remember(entry, '<p>Full original body.</p>', 'adafruit_linked_original', receipt=receipt)
    batch = prepared.prepare_many([entry])
    first = batch.apply(entry)
    first['fulltext_receipt']['url'] = 'https://example.org/mutated'
    first['fulltext_receipt']['nested']['evidence'].append('mutated')
    assert batch.apply(entry) == prepared.apply(entry)
    assert batch.apply(entry)['fulltext_receipt'] == receipt


@pytest.mark.parametrize('changes', [{'id': True}, {'id': '1'}, {'user_id': True}, {'user_id': '1'}])
def test_unexpected_identity_types_keep_single_entry_fallback(db, entry, changes, monkeypatch):
    warm([entry])
    changed = {**entry, **changes}
    monkeypatch.setattr(processing_status, 'time', SimpleNamespace(time=lambda: 12345))
    prepared_batch = prepared.prepare_many([changed])
    assert prepared_batch.apply(changed) == prepared.apply(changed)
    batch = core.load_reader_batch([changed], prepared_batch=prepared_batch, settings_snapshot=core.settings())
    assert core.decorate(changed, changed['user_id'], batch=batch) == core.decorate(changed, changed['user_id'])


@pytest.mark.parametrize('analysis_exists', [True, False])
@pytest.mark.parametrize('note', ['', '   ', '\t', '\n', ' actual note '])
def test_whitespace_note_semantics_match_each_original_path(db, entry, monkeypatch, analysis_exists, note):
    if analysis_exists:
        warm([entry])
    with core.connect() as connection:
        connection.execute('INSERT INTO entry_notes VALUES (1,1,?,?,?)', (note, 1, 2))
    monkeypatch.setattr(processing_status, 'time', SimpleNamespace(time=lambda: 12345))
    prepared_batch = prepared.prepare_many([entry])
    batch = core.load_reader_batch([entry], 1, prepared_batch=prepared_batch, settings_snapshot=core.settings())
    actual = core.decorate(entry, 1, batch=batch)
    expected = core.decorate(entry, 1)
    assert actual == expected
    assert actual['ai']['has_note'] == bool(note.strip(' ') if analysis_exists else note.strip())
