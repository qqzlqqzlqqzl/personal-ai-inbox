"""Event-ordered lock contention, connection ownership and cancellation regressions."""
import asyncio
import contextvars
import gc
import sqlite3
import threading
from contextlib import closing, contextmanager

import httpx
import pytest

import api
import card_translation as cards
import core
from reader_work import ReaderWorkPool


@contextmanager
def held_writer(events):
    acquired = threading.Event()
    release = threading.Event()
    errors = []

    def hold():
        try:
            with closing(sqlite3.connect(core.DB, timeout=15)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                events.append('writer_acquired')
                acquired.set()
                # Only a deadlock watchdog, not a performance threshold.
                if not release.wait(10):
                    events.append('watchdog_released_writer')
                connection.rollback()
                events.append('writer_released')
        except (sqlite3.Error, AssertionError) as exc:
            errors.append(exc)
            acquired.set()

    thread = threading.Thread(target=hold)
    thread.start()
    assert acquired.wait(10)
    assert not errors
    try:
        yield release
    finally:
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert not errors
        assert 'watchdog_released_writer' not in events


def seed(entries, *, cached=True):
    core.discover(entries)
    core.save_settings({'translation_enabled': False})
    with core.connect() as connection:
        connection.execute("UPDATE analyses SET state='done',score=8,result='{}'")
        if cached:
            connection.execute("""UPDATE card_translations SET status='done',title_zh='缓存中文标题',
                summary_zh='缓存中文摘要',priority=30,translated_at=123""")
        else:
            connection.execute('DELETE FROM card_translations')
        for entry in entries:
            connection.execute('INSERT INTO entry_notes VALUES (1,?,?,0,1)', (entry['id'], 'test note'))


def reader_client(monkeypatch, entries, loop_thread):
    async def respond(request):
        assert threading.get_ident() == loop_thread, 'HTTP must remain async on the event loop'
        path = request.url.path
        if path.endswith('/v1/me'):
            return httpx.Response(200, json={'id': 1, 'is_admin': True})
        if path.endswith('/entries/ids'):
            ids = [entry['id'] for entry in entries] if request.url.params.get('status') == 'unread' else []
            return httpx.Response(200, json={'entry_ids': ids, 'total': len(ids)})
        if path.endswith('/v1/feeds'):
            return httpx.Response(200, json=[entries[0]['feed']])
        if path.endswith('/entries'):
            return httpx.Response(200, json={'entries': entries, 'total': len(entries)})
        if '/entries/' in path:
            return httpx.Response(200, json=next(entry for entry in entries if entry['id'] == int(path.rsplit('/', 1)[1])))
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(api.app.state, 'client', client, raising=False)
    monkeypatch.delenv('MINIFLUX_API_KEY', raising=False)
    return client


def trace_workers(monkeypatch, loop, *, writing=None):
    original = core.connect
    opened, closed, statements = [], [], []
    loop_thread = threading.get_ident()

    @contextmanager
    def connection():
        owner = threading.get_ident()
        assert owner != loop_thread, 'reader SQLite connection opened on event loop'
        opened.append(owner)
        try:
            with original() as db:
                def trace(sql):
                    assert threading.get_ident() == owner
                    statements.append(sql)
                    if writing and sql.startswith('BEGIN IMMEDIATE'):
                        loop.call_soon_threadsafe(writing.set)
                db.set_trace_callback(trace)
                yield db
        finally:
            assert threading.get_ident() == owner
            closed.append(owner)

    monkeypatch.setattr(core, 'connect', connection)
    monkeypatch.setattr(api, 'connect', connection)
    return opened, closed, statements


@pytest.mark.asyncio
@pytest.mark.parametrize('route', [
    '/mf/v1/entries?ai_view=recommended&limit=24',
    '/mf/v1/feeds/1/entries?ai_view=pending&ai_min=6',
    '/mf/v1/entries?ai_view=notes&limit=24',
    '/mf/v1/entries?limit=24',
    '/mf/v1/entries/1',
])
async def test_cached_page_completes_with_writer_held_and_no_dml(db, entry, monkeypatch, route):
    entries = [{**entry, 'id': i, 'title': f'Article {i}'} for i in range(1, 25)]
    seed(entries)
    if 'pending' in route:
        with core.connect() as connection:
            connection.execute("UPDATE analyses SET state='pending'")
    if route.endswith('/entries/1'):
        with core.connect() as connection:
            connection.execute('UPDATE card_translations SET priority=40')
    events = []
    loop = asyncio.get_running_loop()
    original_source = cards.source_card
    original_cover = api.first_image_src
    loop_thread = threading.get_ident()

    def parse(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        return original_source(*args, **kwargs)

    def cover(*args):
        assert threading.get_ident() != loop_thread
        return original_cover(*args)

    monkeypatch.setattr(cards, 'source_card', parse)
    monkeypatch.setattr(api, 'first_image_src', cover)
    async with reader_client(monkeypatch, entries, loop_thread), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url='http://reader.test',
        headers={'X-Auth-Token': 'isolated-session'},
    ) as client:
        opened, closed, statements = trace_workers(monkeypatch, loop)
        with held_writer(events) as release:
            response = await client.get(route)
            events.append('page_completed')
            assert response.status_code == 200
            if route.endswith('/entries/1'):
                assert response.json()['id'] == 1
            else:
                assert len(response.json()['entries']) == 24
            assert not release.is_set()
        assert events.index('page_completed') < events.index('writer_released')
    assert opened and opened == closed
    assert not [sql for sql in statements if sql.split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'BEGIN'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_real_write_wait_does_not_block_heartbeat_or_second_request(db, entry, monkeypatch, cancel):
    seed([entry], cached=False)
    events = []
    loop = asyncio.get_running_loop()
    writing = asyncio.Event()
    pool = ReaderWorkPool(limit=1)
    monkeypatch.setattr(api, 'reader_work', pool)
    loop_thread = threading.get_ident()
    async with reader_client(monkeypatch, [entry], loop_thread), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app), base_url='http://reader.test',
        headers={'X-Auth-Token': 'isolated-session'},
    ) as client:
        opened, closed, statements = trace_workers(monkeypatch, loop, writing=writing)
        with held_writer(events) as release:
            page = asyncio.create_task(client.get('/mf/v1/entries?ai_view=recommended'))
            await asyncio.wait_for(writing.wait(), 10)
            events.append('worker_waiting')

            async def heartbeat():
                events.append('heartbeat')

            await asyncio.create_task(heartbeat())
            assert (await client.get('/readyz')).status_code == 200
            events.append('other_request_completed')
            assert not page.done()
            assert not release.is_set()
            if cancel:
                page.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await page
                assert pool._gates[loop]()._value == 0, 'cancelled waiter must retain worker admission'
                events.append('request_cancelled')
            release.set()
        # Admission of this sentinel proves the earlier real worker has finished,
        # including connection closure, even when the HTTP waiter was cancelled.
        await pool.run(lambda: events.append('worker_drained'))
        if not cancel:
            assert (await page).status_code == 200
        assert opened == closed
    pool.executor.shutdown(wait=True)
    assert events.index('heartbeat') < events.index('writer_released')
    assert events.index('other_request_completed') < events.index('writer_released')
    assert any(sql.startswith('INSERT') for sql in statements), 'cancellation is not transaction rollback'


@pytest.mark.asyncio
async def test_cancelled_waiters_do_not_over_admit_workers_or_leak_late_errors():
    loop = asyncio.get_running_loop()
    pool = ReaderWorkPool(limit=2)
    release = threading.Event()
    started = asyncio.Event()
    counts = {'submitted': 0, 'active': 0, 'maximum': 0, 'finished': 0}
    lock = threading.Lock()
    errors = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    actual_submit = pool.executor.submit
    marker = contextvars.ContextVar('request-marker')
    marker.set('copied-request-context')

    def submit(*args, **kwargs):
        counts['submitted'] += 1
        return actual_submit(*args, **kwargs)

    pool.executor.submit = submit

    def blocked():
        assert marker.get() == 'copied-request-context'
        with lock:
            counts['active'] += 1
            counts['maximum'] = max(counts['maximum'], counts['active'])
            if counts['active'] == 2:
                loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(10)
            raise ValueError('late worker error after cancelled HTTP waiter')
        finally:
            with lock:
                counts['active'] -= 1
                counts['finished'] += 1

    tasks = []
    try:
        tasks = [asyncio.create_task(pool.run(blocked)) for _ in range(2)]
        await asyncio.wait_for(started.wait(), 10)
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(value, asyncio.CancelledError) for value in results)
        more = [asyncio.create_task(pool.run(blocked)) for _ in range(20)]
        tasks.extend(more)
        await asyncio.sleep(0)  # Allow every new coroutine to reach its admission gate.
        assert counts['submitted'] == 2
        assert pool._gates[loop]()._value == 0
        for task in more:
            task.cancel()
        await asyncio.gather(*more, return_exceptions=True)
        assert counts['submitted'] == 2
        release.set()
        await pool.run(lambda: None)
        await pool.run(lambda: None)
    finally:
        release.set()
        pool.executor.shutdown(wait=True)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
        gc.collect()
        loop.set_exception_handler(previous_handler)
    assert counts['maximum'] == counts['finished'] == 2
    assert counts['active'] == 0
    assert not errors
