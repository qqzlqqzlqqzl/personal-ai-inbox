"""Synthetic compatibility-gate regression against the actual built reader.

Uses localhost and fake auth only. No live account, credentials or read-state writes.
"""
from urllib.parse import urlsplit
from pathlib import Path
from playwright.sync_api import expect


def verify_compatibility_gate(browser, base):
    results = []
    local_origin = (urlsplit(base).scheme, urlsplit(base).netloc)
    cases = ('outage_retry', 'malformed_json', 'html_response', 'redirect',
             'unauthorized', 'unsupported', 'minimum_supported')
    for scenario in cases:
        context = browser.new_context(viewport={'width': 1280, 'height': 900},
                                      locale='zh-CN', service_workers='block')
        context.add_init_script("localStorage.setItem('auth', JSON.stringify({server: location.origin + '/mf', token: 'synthetic-compatibility-token', username:'', password:''}))")
        state = {'recover': False, 'version_calls': 0}
        calls, writes, leaks = [], [], []

        def intercept(route, request):
            url = urlsplit(request.url)
            path = url.path
            if (url.scheme, url.netloc) != local_origin:
                if request.headers.get('x-auth-token') or request.headers.get('authorization'):
                    leaks.append(request.url)
                route.abort()
                return
            if path == '/credential-leak':
                leaks.append(path)
                route.fulfill(status=200, json={'version': '2.3.3'})
                return
            if not path.startswith('/mf/'):
                route.continue_()
                return
            calls.append(path)
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                writes.append((request.method, path))
            if path.endswith('/version'):
                state['version_calls'] += 1
                assert request.headers.get('x-auth-token') == 'synthetic-compatibility-token'
                if state['recover']:
                    route.fulfill(json={'version': '2.3.3'})
                elif scenario == 'outage_retry':
                    route.fulfill(status=503, json={'error_message': 'synthetic outage'})
                elif scenario == 'malformed_json':
                    route.fulfill(json={'version': 233})
                elif scenario == 'html_response':
                    route.fulfill(status=200, content_type='text/html', body='<html>Not a version endpoint</html>')
                elif scenario == 'redirect':
                    route.fulfill(status=302, headers={'Location': base + '/credential-leak'})
                elif scenario == 'unauthorized':
                    route.fulfill(status=401, json={'error_message': 'synthetic unauthorized'})
                elif scenario == 'unsupported':
                    route.fulfill(json={'version': '2.3.1'})
                else:
                    route.fulfill(json={'version': '2.3.2'})
                return
            if path.endswith('/ai/status'):
                body = {'counts': {}, 'coverage': {}, 'usage': [], 'events': [], 'resources': {}}
            elif path.endswith('/me'):
                body = {'id': 1, 'username': 'synthetic', 'is_admin': True}
            elif path.endswith('/feeds/counters'):
                body = {'reads': {}, 'unreads': {}}
            elif path.endswith('/feeds') or path.endswith('/categories'):
                body = []
            elif path.endswith('/entries'):
                body = {'total': 0, 'entries': []}
            else:
                body = {}
            route.fulfill(json=body)

        context.route('**/*', intercept)
        page = context.new_page()
        page.set_default_timeout(15000)
        try:
            page.goto(base + '/inbox/today')
            if scenario in ('unauthorized', 'unsupported'):
                expect(page).to_have_url(base + '/inbox/login')
                assert not any(path.endswith('/entries') for path in calls)
                # The app must clear invalid auth rather than enter the reader.
                auth = page.evaluate("JSON.parse(localStorage.getItem('auth') || '{}')")
                assert not auth.get('token'), scenario
            elif scenario == 'minimum_supported':
                expect(page.get_by_role('button', name='全部原始', exact=True)).to_be_visible()
                expect(page).to_have_url(base + '/inbox/today')
            else:
                alert = page.get_by_role('alert')
                expect(alert).to_be_visible()
                assert not any(path.endswith('/entries') for path in calls), 'Gate must block reader bootstrap'
                before = state['version_calls']
                state['recover'] = True
                page.get_by_role('button', name='重试', exact=True).click()
                expect(page.get_by_role('button', name='全部原始', exact=True)).to_be_visible()
                expect(page).to_have_url(base + '/inbox/today')
                assert state['version_calls'] > before
                before_reload = state['version_calls']
                page.reload()
                expect(page.get_by_role('button', name='全部原始', exact=True)).to_be_visible()
                assert state['version_calls'] > before_reload, 'Reload must reverify, not bypass the version check'
            assert not writes, f'Compatibility checks must not mutate reader data: {writes}'
            assert not leaks, f'Redirect/external destination must not receive authentication: {leaks}'
            results.append({'compatibility': scenario, 'passed': True,
                            'version_requests': state['version_calls'], 'writes': 0, 'redirect_leaks': 0})
        except Exception as exc:
            try:
                page.screenshot(path=str(Path(__file__).resolve().parents[1] / 'runtime/browser-failure.png'), full_page=True)
            except Exception:
                pass  # Preserve original failure if screenshot capture also fails.
            raise AssertionError(f'Compatibility scenario failed: {scenario}') from exc
        finally:
            context.close()
    return results
