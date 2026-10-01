"""Cache-hit reads must not request a SQLite writer, even with translation paused."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

import card_translation as cards
import core
import prepared_content


@contextmanager
def trace_sql(monkeypatch):
    statements = []
    original = core.connect

    @contextmanager
    def traced():
        with original() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    with monkeypatch.context() as patch:
        patch.setattr(core, 'connect', traced)
        yield statements


def assert_read_only(statements):
    assert not [sql for sql in statements if sql.lstrip().split()[0].upper() in {
        'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'BEGIN', 'COMMIT', 'ROLLBACK'
    }]


def snapshot():
    with core.connect() as connection:
        return tuple(tuple(tuple(row) for row in connection.execute('SELECT * FROM ' + table))
                     for table in ('card_translations', 'card_translation_versions'))


def done(entry, priority=30, *, archive=True):
    cards.enqueue([entry], priority=priority)
    with core.connect() as connection:
        connection.execute("""UPDATE card_translations SET status='done', title_zh='缓存中文标题',
            summary_zh='缓存中文摘要', translated_at=123, updated_at=456 WHERE entry_id=?""", (entry['id'],))
        if archive:
            cards.cache_version(connection, connection.execute(
                'SELECT * FROM card_translations WHERE entry_id=?', (entry['id'],)).fetchone())


@pytest.mark.parametrize('enabled', [True, False])
@pytest.mark.parametrize('status', ['done', 'native'])
@pytest.mark.parametrize('priority', [0, 30, 40])
def test_warm_card_read_only_or_single_real_promotion(db, entry, monkeypatch, enabled, status, priority):
    core.save_settings({'translation_enabled': enabled})
    if status == 'native':
        entry = {**entry, 'title': '中文技术实践', 'content': '<p>这里介绍中文技术实现方案。</p>'}
        cards.enqueue([entry], priority=priority)
    else:
        done(entry, priority)
    before = snapshot()
    with trace_sql(monkeypatch) as statements:
        cards.enqueue([entry], priority=30)
        attached = cards.attach(entry, 1)
    assert attached['card']['status'] == status
    assert attached['card']['enabled'] == enabled
    after = snapshot()
    if priority >= 30:
        assert_read_only(statements)
        assert after == before
    else:
        assert sum(sql.startswith('UPDATE') for sql in statements) == 1
        assert not any(sql.startswith(('INSERT', 'DELETE', 'REPLACE')) for sql in statements)
        assert after[1] == before[1]
        with core.connect() as connection:
            row = connection.execute('SELECT priority,updated_at FROM card_translations').fetchone()
        assert row['priority'] == 30
        assert row['updated_at'] == before[0][0][-2]
    with trace_sql(monkeypatch) as statements:
        cards.enqueue([entry], priority=30)
        cards.attach(entry, 1)
    assert_read_only(statements)
    assert snapshot() == after


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [False, True])
async def test_first_source_is_recorded_while_translation_disabled(db, entry, native):
    core.save_settings({'translation_enabled': False})
    if native:
        entry = {**entry, 'title': '中文技术实践', 'content': '<p>这里介绍中文技术实现方案。</p>'}
    cards.enqueue([entry], priority=30)
    with core.connect() as connection:
        row = connection.execute('SELECT * FROM card_translations').fetchone()
    assert row['status'] == ('native' if native else 'pending')
    assert row['source_hash'] == cards.source_card(entry, core.settings()['model'])[3]

    class NoModel:
        async def post(self, *args, **kwargs):
            pytest.fail('disabled translation must not call the model')

    await cards.run_once(NoModel())


@pytest.mark.parametrize('change', ['excerpt', 'model', 'prepared'])
def test_real_fingerprint_change_requeues_and_archives(db, entry, change):
    done(entry)
    before = snapshot()
    if change == 'excerpt':
        entry = {**entry, 'content': '<p>A different excerpt with the very same title.</p>'}
    elif change == 'model':
        core.save_settings({'model': 'isolated-different-model'})
    else:
        prepared_content.remember(entry, '<h2>产品介绍（Product Hunt）</h2><p>Prepared product features.</p>', 'product_page')
    cards.enqueue([entry], priority=30)
    with core.connect() as connection:
        row = connection.execute('SELECT * FROM card_translations').fetchone()
    applied = prepared_content.apply(entry)
    assert row['source_hash'] == cards.source_card(applied, core.settings()['model'])[3]
    assert row['source_hash'] != before[0][0][2]
    assert row['status'] == 'pending'
    assert snapshot()[1] == before[1]


def test_missing_archive_is_created_only_when_source_changes_then_restored(db, entry, monkeypatch):
    done(entry, archive=False)
    before = snapshot()
    with trace_sql(monkeypatch) as statements:
        cards.enqueue([entry], priority=30)
    assert_read_only(statements)
    assert snapshot() == before
    changed = {**entry, 'content': '<p>Temporarily different source version B.</p>'}
    cards.enqueue([changed], priority=30)
    with core.connect() as connection:
        archived = dict(connection.execute('SELECT * FROM card_translation_versions').fetchone())
    assert archived['translated_at'] == 123
    assert archived['source_hash'] == before[0][0][2]
    cards.enqueue([entry], priority=30)
    with core.connect() as connection:
        restored = dict(connection.execute('SELECT * FROM card_translations').fetchone())
    assert restored['status'] == 'done'
    assert restored['title_zh'] == archived['title_zh']
    assert restored['summary_zh'] == archived['summary_zh']
    assert restored['translated_at'] == archived['translated_at']
    assert restored['source_hash'] == archived['source_hash']
    with trace_sql(monkeypatch) as statements:
        cards.enqueue([entry], priority=30)
    assert_read_only(statements)


@pytest.mark.parametrize('status', ['pending', 'error'])
def test_same_hash_archive_restoration_precedes_unchanged_shortcut(db, entry, status):
    done(entry, priority=40)
    with core.connect() as connection:
        connection.execute("UPDATE card_translations SET status=?,title_zh=NULL,summary_zh=NULL,attempts=3,next_try=999", (status,))
    cards.enqueue([entry], priority=30)
    with core.connect() as connection:
        row = connection.execute('SELECT * FROM card_translations').fetchone()
    assert row['priority'] == 40
    assert (row['status'], row['title_zh'], row['summary_zh'], row['translated_at']) == (
        'done', '缓存中文标题', '缓存中文摘要', 123)


@pytest.mark.parametrize('status', ['pending', 'error'])
def test_unchanged_nonrestored_rows_keep_backoff(db, entry, monkeypatch, status):
    cards.enqueue([entry], priority=40)
    with core.connect() as connection:
        connection.execute("UPDATE card_translations SET status=?,attempts=3,next_try=999,error='retry later'", (status,))
    before = snapshot()
    with trace_sql(monkeypatch) as statements:
        cards.enqueue([entry], priority=30)
    assert_read_only(statements)
    assert snapshot() == before


def test_missing_identity_and_deferred_guards(db, entry, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('invalid or deferred content must not be parsed or enqueued')
    monkeypatch.setattr(cards, 'source_card', unexpected)
    cards.enqueue([{k: v for k, v in entry.items() if k != 'id'},
                   {k: v for k, v in entry.items() if k != 'user_id'},
                   {**entry, 'content_deferred': True}])
    assert snapshot() == ((), ())


def test_user_scope_does_not_reuse_other_users_current_translation(db, entry):
    done(entry)
    cards.enqueue([{**entry, 'user_id': 2}], priority=30)
    with core.connect() as connection:
        row = connection.execute('SELECT * FROM card_translations').fetchone()
    assert (row['user_id'], row['status']) == (2, 'pending')
    assert cards.attach(entry, 1)['card']['status'] == 'pending'
    assert cards.attach(entry, 2)['card'].get('title') is None


def test_concurrent_same_source_change_preserves_valid_current_and_archive(db, entry):
    done(entry, archive=False)
    changed = {**entry, 'content': '<p>A concurrently requested changed excerpt.</p>'}
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(lambda priority: cards.enqueue([changed], priority=priority), [30, 40] * 8))
    with core.connect() as connection:
        rows = connection.execute('SELECT * FROM card_translations').fetchall()
        archives = connection.execute('SELECT * FROM card_translation_versions').fetchall()
    assert len(rows) == len(archives) == 1
    assert (rows[0]['status'], rows[0]['priority']) == ('pending', 40)
    assert archives[0]['title_zh'] == '缓存中文标题'
    cards.enqueue([entry], priority=30)
    assert cards.attach(entry, 1)['card']['title'] == '缓存中文标题'


def test_valid_native_cache_ignores_redundant_archive(db, entry, monkeypatch):
    entry = {**entry, 'title': '中文技术实践', 'content': '<p>这里介绍中文技术实现方案。</p>'}
    cards.enqueue([entry], priority=30)
    with core.connect() as connection:
        cards.cache_version(connection, connection.execute('SELECT * FROM card_translations').fetchone())
    before = snapshot()
    with trace_sql(monkeypatch) as statements:
        cards.enqueue([entry], priority=30)
    assert_read_only(statements)
    assert snapshot() == before
