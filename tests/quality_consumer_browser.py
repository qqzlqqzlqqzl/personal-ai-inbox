"""Refs #105/#106: real built consumer, synthetic APIs; never production history.

AI_NEWS_TEST_BUILD=runtime/browser-build python tests/quality_consumer_browser.py
Fresh outputs: runtime/quality-consumer/run-<ns>/<width>x<height>[-dark]/.
Both original light flows run first; then the same seven cases run in dark mode.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect
from review_reader_harness import Harness
from quality_consumer_fixture import CASES, MINIMUM, RECOMMENDED_IDS, QualityConsumerFixture, make_entries, validate_badge, validate_capture_png
from pending_label_contrast import verify_pending_labels

ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = ((1440, 960), (390, 844))


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def snapshot(page, locator):
    return locator.evaluate("""e=>{const r=e.getBoundingClientRect(),v=visualViewport;
      let opacity=1;for(let n=e;n;n=n.parentElement)opacity*=Number(getComputedStyle(n).opacity);
      return {url:location.pathname,rect:{x:r.x,y:r.y,width:r.width,height:r.height},opacity,
      text:e.innerText,fonts:document.fonts.status,viewport:[innerWidth,innerHeight,devicePixelRatio],
      visual:v?[v.width,v.height,v.offsetLeft,v.offsetTop,v.scale]:null,
      horizontalOverflow:document.documentElement.scrollWidth>innerWidth,
      mode:document.querySelector('.ai-modes [aria-pressed="true"]')?.textContent,
      loaded:document.querySelector('.load-more-container')?.getAttribute('data-loaded-count')}}""")


def capture(h, target, name, captures):
    p = h.page
    p.evaluate("document.fonts.ready")
    deadline = time.monotonic() + 5
    while True:
        before = snapshot(p, target)
        began = time.monotonic()
        p.wait_for_timeout(300)
        after = snapshot(p, target)
        if before == after and before["fonts"] == "loaded" and before["opacity"] == 1:
            break
        if time.monotonic() >= deadline:
            raise AssertionError("normal-animation geometry did not stabilize: " + name)
    sample_interval_ms = (time.monotonic() - began) * 1000
    assert not after["horizontalOverflow"], name
    assert after["rect"]["width"] > 0 and after["rect"]["height"] > 0, name
    assert after["rect"]["y"] < after["viewport"][1] and after["rect"]["y"] + after["rect"]["height"] > 0, name
    path = h.out / (name + ".png")
    if path.exists():
        raise AssertionError("capture must not replace retained evidence")
    p.screenshot(path=str(path))
    pixel_check = validate_capture_png(path.read_bytes(), [round(after["viewport"][0] * after["viewport"][2]), round(after["viewport"][1] * after["viewport"][2])])
    final = snapshot(p, target)
    assert final == after, "capture changed state: " + name
    captures.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "bytes": path.stat().st_size, "stable_ms": sample_interval_ms,
                     "before": before, "after": final, "normal_animations": True, "pixel_check": pixel_check})


def card_for(h, entry_id):
    """Scroll the actual list, rather than wait forever for an unmounted virtual row."""
    p = h.page
    root = p.locator('.entry-list')
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        card = p.locator(f'.entry-list [data-entry-id="{entry_id}"]').first
        if card.count():
            card.scroll_into_view_if_needed()
            expect(card).to_be_visible()
            return card
        bounds = root.bounding_box()
        assert bounds and bounds["height"] > 0, "missing list viewport"
        p.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
        mounted = list(map(int, p.locator('.entry-list [data-entry-id]').evaluate_all('(es)=>es.map(e=>e.dataset.entryId)')))
        direction = -1 if mounted and entry_id < min(mounted) else 1
        p.mouse.wheel(0, direction * max(100, bounds["height"] * .7))
        p.wait_for_timeout(50)
    raise AssertionError("virtual row did not become available: " + str(entry_id))


def run_case(width, height, run_name, identity, theme="light"):
    suffix = "" if theme == "light" else "-dark"
    h = Harness(f"{run_name}/{width}x{height}{suffix}", viewport={"width": width, "height": height})
    p = h.page
    h.settings["minimum_score"] = MINIMUM
    h.feeds[0]["icon"] = {"feed_id": 7, "icon_id": 0}
    h.entries = make_entries(h.feeds[0])
    fixture = QualityConsumerFixture(h.entries)
    captures, views, contrasts = [], [], []
    initial = json.dumps(h.entries, sort_keys=True)
    delegated_gets = {"/mf/v1/version", "/mf/version", "/mf/v1/me", "/mf/v1/ai/settings",
                      "/mf/v1/ai/status", "/mf/v1/feeds", "/mf/v1/categories", "/mf/v1/feeds/counters"}

    def intercept(route, path, method):
        query = parse_qs(urlsplit(route.request.url).query, keep_blank_values=True)
        body = route.request.post_data_json if method not in ("GET", "HEAD", "OPTIONS") else None
        try:
            reply = fixture.respond(path, method, query, body)
        except (ValueError, TypeError) as exc:
            fixture.violations.append({"path": path, "method": method, "reason": str(exc)})
            route.fulfill(status=400, json={"error_message": "invalid synthetic contract"})
            return True
        if reply is not None:
            status, value = reply
            route.fulfill(status=status, json=value, headers={"Cache-Control": "no-store"})
            return True
        if method == "GET" and (path in delegated_gets or re.fullmatch(r"/mf/v1/ai/notes/70[1-7]", path)):
            return False
        fixture.violations.append({"path": path, "method": method, "reason": "unexpected API"})
        route.fulfill(status=501, json={"error_message": "unsupported quality fixture API"})
        return True

    h.custom = intercept
    p.add_init_script("localStorage.setItem('settings',JSON.stringify({articleListLayout:'card',showStatus:'all',"
                      "markReadBy:'manually',markReadOnScroll:false,openSourceOnCardClick:false,themeMode:" + json.dumps(theme) + ",pageSize:24}));"
                      "localStorage.setItem('ai-view-state',JSON.stringify({mode:'all',auxiliary:'none',minimum:8,sort:'score',direction:'desc'}));")
    try:
        h.goto('/inbox/all')
        expect(p.locator('.load-more-container')).to_have_attribute('data-loaded-count', str(len(CASES)))
        expect(p.locator('.page-info')).to_contain_text('(7)')
        raw = fixture.principal_requests('all')
        h.check('raw_request_identity_and_total', bool(raw) and raw[-1]['ids'] == list(CASES) and raw[-1]['total'] == 7)
        h.check('raw_mode_is_selected', p.locator('.ai-modes button').first.get_attribute('aria-pressed') == 'true')

        for entry_id, case in CASES.items():
            card = card_for(h, entry_id)
            text = validate_badge(entry_id, card.inner_text())
            views.append({"view": "raw", "entry_id": entry_id, "text": text})
            h.check(f'{case["key"]}_raw_badge', True)
            capture(h, card, case['key'] + '-raw', captures)
            if entry_id in (702, 703, 705):
                verify_pending_labels(h, card, entry_id, 'raw', theme, contrasts,
                    (lambda: capture(h, card, case['key'] + '-raw-legacy', captures)) if entry_id == 702 else None)
            card.click()
            detail = p.locator('.article-content')
            expect(detail).to_be_visible()
            expect(detail.locator('.article-title')).to_contain_text(case['title'])
            expected_entry = next(e for e in h.entries if e['id'] == entry_id)
            expect(detail.locator('.article-body')).to_contain_text(re.sub('<[^>]+>', '', expected_entry['content']))
            h.check(f'{case["key"]}_original_body_retained', True)
            badge = detail.locator('.ai-verdict-detail, .ai-pending').first
            expect(badge).to_be_visible()
            text = validate_badge(entry_id, badge.inner_text())
            h.check(f'{case["key"]}_detail_badge', True)
            assert any(r.get('detail_id') == entry_id for r in fixture.requests), 'detail must exercise the point API'
            source = detail.locator('.article-source-footer a')
            expected_url = next(e['url'] for e in h.entries if e['id'] == entry_id)
            h.check(f'{case["key"]}_original_link_retained', source.get_attribute('href') == expected_url)
            views.append({"view": "detail", "entry_id": entry_id, "text": text, "original_url": expected_url})
            badge.scroll_into_view_if_needed()
            capture(h, badge, case['key'] + '-detail', captures)
            if entry_id in (702, 703, 705):
                verify_pending_labels(h, detail, entry_id, 'detail', theme, contrasts,
                    (lambda: capture(h, badge, case['key'] + '-detail-legacy', captures)) if entry_id == 702 else None)
            p.get_by_role('button', name='关闭文章', exact=True).click()
            expect(p.locator('.article-body')).to_have_count(0)

        p.get_by_role('button', name='AI 精选', exact=True).click()
        expect(p.locator('.load-more-container')).to_have_attribute('data-loaded-count', '2')
        expect(p.locator('.page-info')).to_contain_text('(2)')
        recommended = fixture.principal_requests('recommended')
        h.check('recommended_request_identity_total_and_exclusions', bool(recommended) and
                recommended[-1]['query'].get('ai_min') == ['8'] and recommended[-1]['total'] == 2 and
                recommended[-1]['ids'] == RECOMMENDED_IDS)
        seen_recommended = []
        for entry_id in CASES:
            card = p.locator(f'.entry-list [data-entry-id="{entry_id}"]')
            if entry_id in RECOMMENDED_IDS:
                card = card_for(h, entry_id)
                validate_badge(entry_id, card.inner_text())
                seen_recommended.append(entry_id)
            else:
                expect(card).to_have_count(0)
        h.check('recommended_dom_has_only_substantive_short_and_legacy',
                seen_recommended == RECOMMENDED_IDS)
        capture(h, p.locator('.entry-list'), 'recommended-filtered', captures)
        p.locator('.ai-modes button').first.click()
        expect(p.locator('.load-more-container')).to_have_attribute('data-loaded-count', '7')
        expect(p.locator('.page-info')).to_contain_text('(7)')
        h.check('raw_roundtrip_retains_all_original_entries', fixture.principal_requests('all')[-1]['ids'] == list(CASES))
        h.check('no_read_favorite_note_or_settings_mutation', not h.note_writes and
                all(method == 'POST' and path == '/mf/v1/ai/reading-session' for method, path, _ in h.writes))
        h.check('fixture_entries_unchanged', json.dumps(h.entries, sort_keys=True) == initial and
                json.dumps(fixture.entries, sort_keys=True) == initial)
        h.check('no_unsupported_api_or_hidden_fixture_errors', not fixture.violations)
    except Exception as exc:
        h.errors.append(type(exc).__name__ + ': ' + str(exc))
        try:
            p.screenshot(path=str(h.out / 'failure.png'))
        except Exception as capture_error:
            h.errors.append('failure capture: ' + type(capture_error).__name__)
        raise
    finally:
        save(h.out / 'consumer-evidence.json', {"identity": identity, "viewport": [width, height], "theme": theme, "contrasts": contrasts,
             "synthetic_api": True, "real_backend_classifier_tested": False, "private_history_used": False,
             "public_metadata_visible_paywall_observed": False, "views": views, "captures": captures,
             "requests": fixture.requests, "violations": fixture.violations, "telemetry_count": len(fixture.telemetry),
             "recommended_ids": RECOMMENDED_IDS, "all_ids": list(CASES)})
        h.close()


def main():
    if os.environ.get('CHROMIUM_EXECUTABLE'):
        raise RuntimeError('use the CI-pinned browser, no executable override')
    completed = subprocess.run(['git', 'rev-parse', 'HEAD', 'HEAD^{tree}'], cwd=ROOT, capture_output=True,
                               text=True, timeout=5, check=True).stdout.splitlines()
    identity = {"head": completed[0], "tree": completed[1]}
    run_name = 'quality-consumer/run-' + str(time.time_ns())
    out = ROOT / 'runtime' / run_name
    out.mkdir(parents=True, exist_ok=False)
    build = Path(os.environ['AI_NEWS_TEST_BUILD']).resolve()
    build_files = [{"path": str(p.relative_to(build)), "bytes": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                   for p in sorted(build.rglob('*')) if p.is_file()]
    save(out / 'build-input.json', {"identity": identity, "files": build_files})
    outcomes = []
    for theme in ('light', 'dark'):
        for width, height in VIEWPORTS:
            try:
                run_case(width, height, run_name, identity, theme)
                outcomes.append({"viewport": [width, height], "theme": theme, "status": "PASSED"})
            except Exception as exc:
                outcomes.append({"viewport": [width, height], "theme": theme, "status": "FAILED_OR_BLOCKED",
                                 "error": type(exc).__name__ + ': ' + str(exc)})
    save(out / 'execution.json', {"identity": identity, "outcomes": outcomes})
    if any(item['status'] != 'PASSED' for item in outcomes):
        raise AssertionError(outcomes)


if __name__ == '__main__':
    main()
