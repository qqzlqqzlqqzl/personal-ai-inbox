"""Live UI regression with controlled card states/fetch failures; no direct model calls."""
import json, difflib
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from playwright.sync_api import sync_playwright, expect
from browser_env import launch
from ops_common import client, local_admin

ROOT = Path('/home/ubuntu/ai-news')
OUT = ROOT / 'artifacts/ui-review'
BASE = 'https://106.53.40.6'
report = {'at': time.time(), 'base': BASE, 'mode': 'live entries; controlled translation state and fetch failures', 'checks': []}
restores = {}
errors = []

def check(name, ok, detail=None):
    report['checks'].append({'name': name, 'passed': bool(ok), 'detail': detail})
    print(name, bool(ok), flush=True)
    if not ok:
        raise AssertionError(name)

def login(page):
    _, password = local_admin()
    page.goto(BASE + '/inbox/login', wait_until='domcontentloaded')
    page.locator('#password_input').fill(password)
    page.get_by_role('button', name='登录', exact=True).click()
    page.wait_for_url('**/today', timeout=30000)
    page.locator('.grid-card-content').first.wait_for(timeout=30000)

def entries_response(response, sort, direction):
    q = parse_qs(urlsplit(response.url).query)
    return urlsplit(response.url).path == '/mf/v1/entries' and q.get('ai_sort') == [sort] and q.get('direction') == [direction] and q.get('limit') == ['24'] and q.get('offset', ['0']) == ['0']

def validate_order(page, response, field, direction):
    data = response.json()['entries']
    values = [datetime.fromisoformat(e['published_at']).timestamp() if field == 'time' else e['ai'][field] for e in data]
    check('live_order_' + field + '_' + direction, values == sorted(values, reverse=direction == 'desc'), values[:5])
    page.wait_for_function('(id)=>document.querySelector(".article-entry")?.dataset.entryId === String(id)', arg=data[0]['id'], timeout=20000)
    check('dom_order_' + field + '_' + direction, page.locator('.article-entry').first.get_attribute('data-entry-id') == str(data[0]['id']))

with client() as api:
    api.base_url = BASE + '/mf/'
    before_settings = api.get('/v1/ai/settings').json()
    try:
        with sync_playwright() as pw:
            browser = launch(pw)
            ctx = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN', service_workers='block')
            ctx.add_init_script("localStorage.setItem('settings',JSON.stringify({...JSON.parse(localStorage.getItem('settings')||'{}'),showStatus:'all',markReadOnScroll:false,pageSize:20}))")
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            login(page)
            check('disabled_translation_has_no_waiting_stripe', page.locator('.card-language-status').count() == 0)
            check('active_kaggle_pipeline_explained_once_in_toolbar', 'Kaggle 持续增量处理' in page.locator('.ai-progress').inner_text())
            for option, field, direction in [
                ('published_at_asc', 'time', 'asc'),
                ('published_at_desc', 'time', 'desc'),
                ('score_desc', 'score', 'desc'),
                ('score_asc', 'score', 'asc'),
            ]:
                with page.expect_response(lambda r: entries_response(r, field, direction), timeout=30000) as pending:
                    page.get_by_label('文章排序', exact=True).select_option(option)
                validate_order(page, pending.value, field, direction)
            check('single_sort_surface', page.get_by_label('AI 排序', exact=True).count() == 0)

            # Force presentation states only; keep real IDs, images, links and source copy.
            state = {'value': 'pending'}
            def card_state(route):
                r = route.fetch()
                data = r.json()
                for e in data.get('entries', []):
                    if state['value']:
                        e['card'] = {'status': state['value'], 'enabled': state['value'] != 'disabled'}
                route.fulfill(response=r, json=data)
            page.route('**/mf/v1/entries?*', card_state)
            for status in ['pending', 'processing', 'error', 'budget_paused', 'disabled']:
                state['value'] = status
                page.reload(wait_until='domcontentloaded')
                page.locator('.grid-card-content').first.wait_for(timeout=30000)
                metrics = page.locator('.grid-card-wrapper').evaluate_all("cards=>cards.map(c=>({id:c.dataset.entryId,w:c.getBoundingClientRect().width,cw:c.querySelector('.grid-card-content').getBoundingClientRect().width,side:!!c.querySelector(':scope > .card-language-status'),statusInside:!!c.querySelector('.grid-card-content .card-language-status')}))")
                check('full_card_width_' + status, all(m['cw'] >= m['w'] - 3 and not m['side'] for m in metrics), metrics[:2])
                check('status_placement_' + status, all(m['statusInside'] == (status != 'disabled') for m in metrics))
                if status == 'pending':
                    page.screenshot(path=str(OUT / 'cards-pending-desktop.png'))
            state['value'] = 'pending'
            for layout, selector in [('column', '.card-content'), ('list', '.list-entry-content')]:
                page.evaluate("layout=>localStorage.setItem('settings',JSON.stringify({...JSON.parse(localStorage.getItem('settings')),articleListLayout:layout,showListSummary:true}))", layout)
                page.reload(wait_until='domcontentloaded')
                page.locator(selector).first.wait_for(timeout=30000)
                check(layout + '_no_status_sibling', page.locator('.article-entry > .card-language-status').count() == 0)
                check(layout + '_no_overflow', page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                if layout == 'list':
                    check('compact_ai_list_keeps_summary', bool(page.locator('.list-entry-summary').first.inner_text().strip()))
            page.evaluate("localStorage.setItem('settings',JSON.stringify({...JSON.parse(localStorage.getItem('settings')),articleListLayout:'card'}))")
            page.set_viewport_size({'width': 390, 'height': 844})
            page.reload(wait_until='domcontentloaded')
            page.locator('.grid-card-content').first.wait_for(timeout=30000)
            check('mobile_no_horizontal_overflow', page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
            check('mobile_no_status_sibling', page.locator('.grid-card-wrapper > .card-language-status').count() == 0)
            page.screenshot(path=str(OUT / 'cards-pending-mobile.png'))
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.unroute('**/mf/v1/entries?*', card_state)

            # A real source article; all simulated body changes stay in browser memory.
            page.evaluate("localStorage.setItem('settings',JSON.stringify({...JSON.parse(localStorage.getItem('settings')),orderDirection:'desc'}))")
            candidates = api.get('/v1/entries?ai_view=recommended&ai_min=6&globally_visible=true&limit=24').json()['entries']
            target = candidates[0]
            eid = target['id']
            original = api.get('/v1/entries/' + str(eid)).json()
            restores[eid] = (original['status'], original['starred'])
            page.goto(BASE + f'/inbox/all/entry/{eid}', wait_until='domcontentloaded')
            page.locator('.article-body').wait_for(timeout=30000)
            page.locator('.article-source-footer a').wait_for(timeout=30000)
            title = page.locator('.article-title a')
            footer = page.locator('.article-source-footer a')
            check('title_and_footer_same_original_url', title.get_attribute('href') == original['url'] == footer.get_attribute('href'), original['url'])
            check('both_source_links_new_tab', title.get_attribute('target') == '_blank' and footer.get_attribute('target') == '_blank')
            def source_stub(route):
                route.fulfill(status=200, content_type='text/html', body='<p>Source navigation verified.</p>')
            ctx.route(original['url'], source_stub)
            for name, link in [('title', title), ('footer', footer)]:
                link.scroll_into_view_if_needed()
                with page.expect_popup() as popup:
                    link.click()
                popup.value.wait_for_load_state('domcontentloaded')
                check(name + '_opens_original_site', popup.value.url == original['url'])
                popup.value.close()
            page.screenshot(path=str(OUT / 'source-footer.png'))
            ctx.unroute(original['url'], source_stub)
            article_core = lambda: page.locator('.article-body').evaluate("node => { const clone=node.cloneNode(true); clone.querySelectorAll('.article-note,.article-source-footer,.deferred-code-block,button').forEach(x=>x.remove()); return clone.innerText; }")
            body_before = article_core()
            mode = {'value': 'error', 'count': 0}
            def fetch_stub(route):
                mode['count'] += 1
                if mode['value'] == 'error':
                    route.fulfill(status=500, json={'error_message': 'controlled extraction failure'})
                else:
                    route.fulfill(status=200, json={'content': original['content'] + '<p>受控正文重试验证</p>', 'reading_time': original['reading_time']})
            page.route(f'**/mf/v1/entries/{eid}/fetch-content*', fetch_stub)
            button = page.get_by_role('button', name='重新抓取正文', exact=True)
            button.click()
            expect(page.get_by_text('原文获取失败', exact=False)).to_be_visible(timeout=15000)
            expect(button).to_be_enabled(timeout=15000)
            body_after = article_core()
            mismatch = None
            if body_after != body_before:
                for tag,a0,a1,b0,b1 in difflib.SequenceMatcher(None, body_before, body_after).get_opcodes():
                    if tag != 'equal':
                        mismatch = {'tag':tag,'before':body_before[max(0,a0-80):min(len(body_before),a1+80)],
                                    'after':body_after[max(0,b0-80):min(len(body_after),b1+80)]}
                        break
            check('failed_fetch_preserves_body', body_after == body_before, {
                'before_len': len(body_before), 'after_len': len(body_after), 'mismatch': mismatch,
            })
            check('failed_fetch_allows_retry', button.is_enabled(), {'requests': mode['count']})
            mode['value'] = 'ok'
            button.click()
            expect(page.locator('.article-body')).to_contain_text('受控正文重试验证', timeout=15000)
            expect(button).to_be_disabled(timeout=15000)
            check('successful_retry_shows_body', True)
            page.unroute(f'**/mf/v1/entries/{eid}/fetch-content*', fetch_stub)
            page.reload(wait_until='domcontentloaded')
            page.locator('.article-source-footer a').wait_for(timeout=30000)
            check('fetch_without_save_not_persisted', '受控正文重试验证' not in page.locator('.article-body').inner_text())
            held = {}
            def hold_fetch(route):
                held['route'] = route
            page.route(f'**/mf/v1/entries/{eid}/fetch-content*', hold_fetch)
            button.click()
            page.wait_for_timeout(350)
            check('old_article_fetch_in_flight', 'route' in held)
            page.get_by_role('button', name='下一篇文章', exact=True).click()
            page.wait_for_function('(id)=>!location.pathname.endsWith("/entry/"+id)', arg=eid, timeout=20000)
            next_id = int(page.url.rsplit('/', 1)[-1])
            next_entry = api.get('/v1/entries/' + str(next_id)).json()
            restores[next_id] = (next_entry['status'], next_entry['starred'])
            page.locator('.article-source-footer a').wait_for(timeout=30000)
            next_title = page.locator('.article-title').inner_text()
            held['route'].fulfill(status=200, json={'content':'<p>STALE OLD ARTICLE RESPONSE</p>', 'reading_time':1})
            page.wait_for_timeout(600)
            check('stale_fetch_does_not_replace_new_article', page.locator('.article-title').inner_text() == next_title and 'STALE OLD ARTICLE RESPONSE' not in page.locator('.article-body').inner_text())
            check('stale_fetch_does_not_disable_new_button', page.get_by_role('button', name='重新抓取正文', exact=True).is_enabled())
            page.unroute(f'**/mf/v1/entries/{eid}/fetch-content*', hold_fetch)
            check('no_fatal_javascript', not errors, errors)
            browser.close()
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)[:1800]
        report['traceback'] = traceback.format_exc(limit=3)[:2000]
    finally:
        for eid, (status, starred) in restores.items():
            api.put('/v1/entries', json={'entry_ids': [eid], 'status': status, 'starred': starred}).raise_for_status()
        current = api.get('/v1/ai/settings').json()
        report['settings_unchanged'] = current == before_settings
        report['test_entry_states_restored'] = True
report['passed'] = not report.get('error') and report['settings_unchanged'] and all(c['passed'] for c in report['checks'])
(OUT / 'browser.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'passed':report['passed'],'checks':len(report['checks']),'error':(report.get('error') or '').splitlines()[0:2]}, ensure_ascii=False))
raise SystemExit(0 if report['passed'] else 1)
