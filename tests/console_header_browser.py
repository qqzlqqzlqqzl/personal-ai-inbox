"""Real built-Reader console geometry, all tabs; APIs stay in the local harness.

Run with AI_NEWS_TEST_BUILD and the CI-pinned Playwright Chromium. The legacy
control restores F110255 CSS in this synthetic context, never the build files.
720px is a constrained viewport check, not a claim of native browser zoom.
Physical safe-area/200% acceptance remains with the native/device CI gates.
"""
import hashlib
import json
import os
from pathlib import Path

from playwright.sync_api import expect
from review_reader_harness import Harness
from console_link_contrast import verify_console_links

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'tests/fixtures/console-header-F110255.css'
OLD_SHA = 'd043b3b48c99076fa9cfdf83ee7c95509c5fa9ac3d75504414dd5c49a2e39686'
LEGACY_RESET = '''.ai-dialog{padding-block-start:22px}
.ai-dialog>header{margin-inline:0;padding:12px 0}
@media(max-width:600px){.ai-dialog{padding-block-start:14px}}'''
TABS = [('settings', '模型与偏好', '.ai-form'),
        ('status', '资源看板', '.ai-dashboard'),
        ('sources', '来源目录', '.review-source-filters')]

METRICS = """() => {
  const d=document.querySelector('.ai-dialog'),h=d.querySelector(':scope>header');
  const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}};
  const ds=getComputedStyle(d),hs=getComputedStyle(h),dr=rect(d),hr=rect(h);
  const top=dr.y+parseFloat(ds.borderTopWidth),left=dr.x+parseFloat(ds.borderLeftWidth);
  const right=left+d.clientWidth;
  const hit=(x,y)=>{const e=document.elementFromPoint(x,y);return e===h||h.contains(e)};
  const bg=hs.backgroundColor.match(/[\\d.]+/g).map(Number);
  return {dialog:dr,title:rect(h.querySelector('h2')),close:rect(h.querySelector('button')),
    nav:rect(d.querySelector(':scope>nav')),header:hr,footer:rect(d.querySelector(':scope>footer')),
    firstSection:rect(d.querySelector(':scope>section')),scrollHeight:d.scrollHeight,
    clientHeight:d.clientHeight,scrollTop:d.scrollTop,gap:hr.y-top,
    noOverflow:d.scrollWidth<=d.clientWidth,bodyOverflow:document.documentElement.scrollWidth>innerWidth,
    headerOpaque:bg.length===3||bg[3]===1,
    topCovered:hit((left+right)/2,top+2),
    sidesCovered:hit(left+18,hr.y+hr.height/2)&&hit(right-18,hr.y+hr.height/2),
    paddingTop:parseFloat(ds.paddingTop),font:getComputedStyle(h.querySelector('h2')).fontSize,
    titleText:h.querySelector('h2').textContent,
    theme:document.body.getAttribute('arco-theme')};
}"""


def frame(page):
    page.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')


def scroll(page, fraction):
    page.locator('.ai-dialog').evaluate('(e,f)=>{e.scrollTop=(e.scrollHeight-e.clientHeight)*f}', fraction)
    frame(page)


def same_initial(before, after):
    # Header's background box grows upward into the old inset. The actual title,
    # close button, body, scroll extent and outer dialog must not move or shrink.
    for field in ('dialog', 'title', 'close', 'nav', 'firstSection'):
        for key in ('x', 'y', 'width', 'height'):
            assert abs(before[field][key] - after[field][key]) <= 1, (field, key, before, after)
    assert abs(before['scrollHeight'] - after['scrollHeight']) <= 1, (before, after)
    assert before['font'] == after['font'] and before['titleText'] == after['titleText']


def run_case(width, height, theme, legacy_css):
    h = Harness(f'console-header/{width}x{height}-{theme}', viewport={'width': width, 'height': height})
    p = h.page
    measurements = []
    h.settings['minimum_score'] = 8
    p.add_init_script("localStorage.setItem('settings',JSON.stringify({themeMode:" + json.dumps(theme) +
                      ",showStatus:'all'}));localStorage.setItem('ai-view-state',JSON.stringify({mode:'recommended',minimum:8,sort:'score',direction:'desc',auxiliary:'none'}))")
    try:
        h.goto()
        expect(p.locator('body')).to_have_attribute('arco-theme', theme)
        h.panel()
        for key, label, ready in TABS:
            scroll(p, 0)
            p.locator('.ai-dialog>nav').get_by_role('button', name=label, exact=True).click()
            expect(p.locator(ready)).to_be_visible()
            # This one scoped control reconstitutes the reviewed old CSS. It is
            # removed from the document before candidate checks, not from disk.
            legacy = p.add_style_tag(content=legacy_css + '\n' + LEGACY_RESET)
            scroll(p, 0)
            before = p.evaluate(METRICS)
            scroll(p, .5)
            old_mid = p.evaluate(METRICS)
            measurements.append({'tab': key, 'legacy_initial': before, 'legacy_mid': old_mid})
            assert old_mid['scrollTop'] > 0, 'fixture must really scroll'
            expected_gap = 14 if width <= 600 else 22
            h.check(key + '_old_gap_negative', abs(old_mid['gap'] - expected_gap) <= 1 and not old_mid['topCovered'])
            p.screenshot(path=str(h.out / f'{key}-legacy-mid.png'))
            legacy.evaluate('(e)=>e.remove()')
            scroll(p, 0)
            after = p.evaluate(METRICS)
            measurements[-1]['candidate_initial'] = after
            same_initial(before, after)
            h.check(key + '_initial_title_body_geometry_preserved')
            p.screenshot(path=str(h.out / f'{key}-top.png'))
            for name, fraction in [('mid', .5), ('bottom', 1)]:
                scroll(p, fraction)
                current = p.evaluate(METRICS)
                measurements[-1][name] = current
                h.check(key + '_' + name + '_opaque_header_covers_top',
                        abs(current['gap']) <= 1 and current['topCovered'] and current['sidesCovered'] and current['headerOpaque'])
                h.check(key + '_' + name + '_no_overflow_theme', current['noOverflow'] and not current['bodyOverflow'] and current['theme'] == theme)
                p.locator('.ai-dialog>header').get_by_role('button', name='关闭', exact=True).click(trial=True)
                if name == 'bottom':
                    h.check(key + '_footer_reachable', current['footer']['y'] >= current['header']['bottom'] and
                            current['footer']['bottom'] <= current['dialog']['bottom'] + 1)
                p.screenshot(path=str(h.out / f'{key}-{name}.png'))
            if width in (1440, 390):
                verify_console_links(h, key, theme)
        # Actual click closes the unmodified product dialog and restores its opener.
        p.locator('.ai-dialog>header').get_by_role('button', name='关闭', exact=True).click()
        expect(p.locator('.ai-dialog')).to_have_count(0)
        opener = '.ai-status-trigger' if width <= 768 else '.ai-settings-button'
        expect(p.locator('.ai-toolbar>' + opener)).to_be_focused()
        h.check('close_focus_restored')
        h.check('zero_api_writes', not h.writes)
    except Exception as exc:
        h.errors.append(type(exc).__name__ + ': ' + str(exc)[:500])
        if not p.is_closed():
            p.screenshot(path=str(h.out / 'failure.png'))
        raise
    finally:
        (h.out / 'geometry.json').write_text(json.dumps({'legacy_css_sha256': OLD_SHA,
            'measurements': measurements, 'real_browser': True, 'native_zoom_tested': False,
            'physical_safe_area_tested': False, 'calls': h.calls}, ensure_ascii=False, indent=2))
        h.close()


def main():
    if os.environ.get('CHROMIUM_EXECUTABLE'):
        raise RuntimeError('This acceptance uses the CI-pinned Playwright browser, no executable override')
    legacy_css = OLD.read_bytes()
    assert hashlib.sha256(legacy_css).hexdigest() == OLD_SHA
    for width, height in ((1440, 900), (390, 844), (844, 390), (720, 480)):
        for theme in ('light', 'dark'):
            run_case(width, height, theme, legacy_css.decode())


if __name__ == '__main__':
    main()
