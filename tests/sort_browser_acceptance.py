"""Actual built Reader sorting with synthetic APIs only, in hosted Chromium.

Native select events and a 390px viewport are automated browser coverage, not
physical phone-picker or hardware-keyboard certification. CSS zoom is explicit.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
from review_reader_harness import Harness

RANKS = {
    101: {'score': 9, 'technical': 6, 'business': 7, 'time': 3, 'created_at': 2, 'note_updated': 1},
    102: {'score': 6, 'technical': 9, 'business': 8, 'time': 1, 'created_at': 3, 'note_updated': 3},
    103: {'score': 8, 'technical': 7, 'business': 9, 'time': 2, 'created_at': 1, 'note_updated': 2},
}
failures = []

for name, width, zoom in [('desktop', 1440, 1), ('mobile', 390, 1), ('desktop-css-zoom-200', 1440, 2)]:
    h = Harness('sort-' + name)
    p = h.page
    p.set_viewport_size({'width': width, 'height': 900 if width > 390 else 844})
    p.add_init_script("if (!localStorage.getItem('settings')) localStorage.setItem('settings', JSON.stringify({orderBy:'published_at',orderDirection:'desc',articleListLayout:'list',showStatus:'all'}))")
    requests, observations = [], []
    h.feeds[0].update(icon={'feed_id': 7, 'icon_id': 0}, checked_at='2026-10-01T08:00:00Z',
                      parsing_error_count=0, parsing_error_message='', disabled=False, hide_globally=False)

    def entry(eid):
        ranks = RANKS[eid]
        return {'id': eid, 'user_id': 1, 'feed_id': 7, 'title': f'排序验证文章 {eid}',
                'url': f'https://example.test/sort/{eid}', 'comments_url': '', 'author': '测试作者',
                'content': '<p>隔离排序测试正文</p>', 'hash': str(eid),
                'published_at': f'2026-10-01T0{ranks["time"]}:00:00Z',
                'created_at': f'2026-10-01T0{ranks["created_at"]}:00:00Z',
                'changed_at': '2026-10-01T08:00:00Z', 'status': 'read', 'starred': False,
                'reading_time': 1, 'enclosures': [], 'feed': h.feeds[0],
                'ai': {'status': 'done', 'score': ranks['score'], 'technical_value': ranks['technical'],
                       'business_value': ranks['business'], 'has_note': True}}

    def intercept(route, path, method):
        if path.endswith('/entries') and method == 'GET':
            query = parse_qs(urlsplit(route.request.url).query)
            field = query.get('ai_sort', query.get('order', ['published_at']))[0]
            field = 'time' if field in ('published_at', 'changed_at') else field
            direction = query.get('direction', ['desc'])[0]
            ids = sorted(RANKS, key=lambda eid: RANKS[eid][field], reverse=direction == 'desc')
            if query.get('limit') != ['1']:
                requests.append({'url': route.request.url, 'query': query, 'ids': ids})
            route.fulfill(json={'total': len(ids), 'entries': [entry(eid) for eid in ids]})
            return True
        return False

    h.custom = intercept

    def displayed():
        return p.locator('.entry-list [data-entry-id]').evaluate_all('(nodes)=>[...new Set(nodes.map(n=>Number(n.dataset.entryId)))]')

    def record(key, value):
        h.checks[key] = bool(value)
        print(('PASS ' if value else 'FAIL ') + name + ': ' + key, flush=True)
        if not value:
            failures.append(name + ': ' + key)

    def await_order(ids):
        expect(p.locator('.entry-list [data-entry-id]')).to_have_count(3)
        p.wait_for_function('(ids)=>JSON.stringify([...new Set([...document.querySelectorAll(".entry-list [data-entry-id]")].map(n=>Number(n.dataset.entryId)))])===JSON.stringify(ids)', arg=ids)

    def choose(value, expected, ai_sort=None, auxiliary=None):
        before, errors = len(requests), len(h.errors)
        select = p.get_by_role('combobox', name='排序方式', exact=True)
        select.select_option(value)
        # Collect a failed baseline without aborting the remaining lens/reload cases.
        try:
            await_order(expected)
        except Exception:
            pass
        p.wait_for_timeout(150)
        observed = {'value': value, 'requests': requests[before:], 'displayed': displayed(),
                    'expected': expected, 'errors': h.errors[errors:],
                    'ai_state': p.evaluate('JSON.parse(localStorage.getItem("ai-view-state"))'),
                    'native_settings': p.evaluate('JSON.parse(localStorage.getItem("settings"))')}
        observations.append(observed)
        if observed['errors'] or observed['displayed'] != expected:
            p.screenshot(path=str(h.out / (value + '-failure.png')), full_page=True)
        record(value + ' selector matches choice', select.input_value() == value)
        record(value + ' requests new sorted list', bool(observed['requests']))
        record(value + ' displayed order matches choice', observed['displayed'] == expected)
        record(value + ' has no unhandled exception', not observed['errors'])
        if observed['requests']:
            q = observed['requests'][-1]['query']
            record(value + ' requested direction matches choice', q.get('direction') == [value.rsplit('_', 1)[1]])
            record(value + ' retains Today request scope', q.get('published_after') == [str(today)])
            record(value + ' requested sort matches choice', q.get('ai_sort') == [ai_sort] if ai_sort else q.get('order') == [value.rsplit('_', 1)[0]] and 'ai_view' not in q)
            if auxiliary:
                record(value + ' retains auxiliary scope', q.get('ai_view') == [auxiliary] or q.get('has_note') == ['true'])
        record(value + ' retains Today route', p.url.endswith('/today'))

    try:
        h.goto()
        today = int(datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=0,minute=0,second=0,microsecond=0).timestamp())
        if zoom == 2:
            p.evaluate("document.documentElement.style.zoom='2'")
        await_order([101, 103, 102])
        record('initial Today AI score order', p.url.endswith('/today') and 'AI精选' in p.locator('.page-info').inner_text())
        choose('score_asc', [102, 103, 101], 'score')
        record('AI direction persisted', observations[-1]['ai_state']['direction'] == 'asc')
        p.reload()
        await_order([102, 103, 101])
        expect(p.get_by_role('combobox', name='排序方式', exact=True)).to_have_value('score_asc')
        record('reload consumes persisted AI order', True)
        if zoom == 2:
            p.evaluate("document.documentElement.style.zoom='2'")
        p.get_by_role('button', name='全部原始', exact=True).click()
        await_order([101, 103, 102])
        choose('created_at_asc', [103, 101, 102])
        record('native sort persisted', observations[-1]['native_settings']['orderBy'] == 'created_at' and observations[-1]['native_settings']['orderDirection'] == 'asc')
        p.get_by_role('button', name='AI 精选', exact=True).click()
        await_order([102, 103, 101])
        record('native lens does not overwrite AI preference', p.get_by_role('combobox', name='排序方式', exact=True).input_value() == 'score_asc')
        choose('technical_asc', [101, 103, 102], 'technical')
        choose('business_desc', [103, 102, 101], 'business')
        choose('published_at_asc', [102, 103, 101], 'time')
        p.get_by_role('button', name='有笔记', exact=True).click()
        choose('note_updated_asc', [101, 103, 102], 'note_updated', 'notes')
        p.get_by_role('button', name='有笔记', exact=True).click()
        p.get_by_role('button', name='待处理 / 异常', exact=True).click()
        choose('published_at_desc', [101, 103, 102], 'time', 'pending')
        p.get_by_role('button', name='全部原始', exact=True).click()
        await_order([103, 101, 102])
        record('AI lens does not overwrite native preference', p.get_by_role('combobox', name='排序方式', exact=True).input_value() == 'created_at_asc')
        record('no horizontal overflow', p.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        record('sort control remains visible', p.get_by_role('combobox', name='排序方式', exact=True).is_visible())
        record('zero API writes', not h.writes)
        p.screenshot(path=str(h.out / 'complete.png'), full_page=True)
    except Exception as exc:
        h.errors.append(str(exc))
        failures.append(name + ': ' + str(exc))
        p.screenshot(path=str(h.out / 'failure.png'), full_page=True)
    finally:
        (h.out / 'observations.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2))
        (h.out / 'requests.json').write_text(json.dumps(requests, ensure_ascii=False, indent=2))
        try:
            h.close()
        except AssertionError as exc:
            failures.append(name + ': harness result ' + str(exc))

if failures:
    raise AssertionError(failures)
