"""Refs #110: real built layout/scroll/focus controls in fresh loopback contexts."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

from playwright.sync_api import expect
from review_reader_harness import Harness
from quality_consumer_fixture import validate_capture_png

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = dict(zip(('head', 'tree'), subprocess.run(
    ['git', 'rev-parse', 'HEAD', 'HEAD^{tree}'], cwd=ROOT, capture_output=True,
    text=True, check=True, timeout=5).stdout.splitlines()))


def visual_state(page):
    return page.evaluate("""() => {
      const article=document.querySelector('.article-content'),body=article?.querySelector('.article-body'),
        bar=article?.querySelector('.review-reading-bar');
      if(!article||!body||!bar)throw Error('Missing actual reading surface');
      const rect=e=>e?e.getBoundingClientRect().toJSON():null;
      const info=e=>{if(!e)return null;const c=getComputedStyle(e);let effectiveOpacity=1;for(let n=e;n;n=n.parentElement)effectiveOpacity*=Number(getComputedStyle(n).opacity);return {rect:rect(e),effectiveOpacity,
        display:c.display,visibility:c.visibility,opacity:c.opacity,color:c.color,background:c.backgroundColor,
        fontSize:c.fontSize,fontFamily:c.fontFamily,lineHeight:c.lineHeight,maxWidth:c.maxWidth,
        text:e.innerText?.slice(0,160)??'',textLength:e.innerText?.length??0}};
      let scroll=body.parentElement;
      while(scroll&&!(scroll.scrollHeight>scroll.clientHeight&&/(auto|scroll)/.test(getComputedStyle(scroll).overflowY)))scroll=scroll.parentElement;
      if(!scroll)throw Error('Missing actual overflowing article ancestor');
      const v=visualViewport,a=document.activeElement,toggle=bar.querySelector('button[aria-pressed]');
      const layout=bar.querySelector('.review-reading-controls'),opener=layout?.querySelector('summary');
      const outline=opener?getComputedStyle(opener):null;
      const barRect=bar.getBoundingClientRect(),bodyRect=body.getBoundingClientRect();
      const ownsPoint=x=>{const hit=document.elementFromPoint(x,barRect.top+barRect.height/2);return !!hit&&(hit===bar||bar.contains(hit))};
      return {url:location.href,path:location.pathname,theme:document.body.getAttribute('arco-theme'),
        viewport:[innerWidth,innerHeight,devicePixelRatio],visual:v?{width:v.width,height:v.height,
          scale:v.scale,offsetLeft:v.offsetLeft,offsetTop:v.offsetTop,pageLeft:v.pageLeft,pageTop:v.pageTop}:null,
        fonts:document.fonts.status,focusMode:article.classList.contains('review-reading-focus'),
        toggle:{text:toggle?.textContent,pressed:toggle?.getAttribute('aria-pressed')},
        title:info(article.querySelector('.article-title')),meta:info(article.querySelector('.article-meta')),
        ai:info(article.querySelector('.article-header>.ai-verdict,.article-header>.ai-pending')),
        layout:{open:layout?.open,opener:{...info(opener),focused:a===opener,
          focusVisible:opener?.matches(':focus-visible')??false,outlineStyle:outline?.outlineStyle,
          outlineWidth:parseFloat(outline?.outlineWidth)||0,outlineColor:outline?.outlineColor,
          outlineOffset:parseFloat(outline?.outlineOffset)||0}},
        toolbar:info(bar),body:info(body),toolbarCoverage:{
          left:barRect.left<=bodyRect.left+1,right:barRect.right>=bodyRect.right-1,
          leftHit:ownsPoint(bodyRect.left+2),rightHit:ownsPoint(bodyRect.right-2)},
        anchor20:info(document.querySelector('#focus-p-20')),
        anchor39:info(document.querySelector('#focus-p-39')),paragraphs:body.querySelectorAll('p[id^="focus-p-"]').length,
        scroll:{rect:rect(scroll),top:scroll.scrollTop,left:scroll.scrollLeft,
          height:scroll.scrollHeight,clientHeight:scroll.clientHeight},
        outerScroll:{article:article.scrollTop,windowX:scrollX,windowY:scrollY},
        active:{tag:a?.tagName,id:a?.id,role:a?.getAttribute('role'),label:a?.getAttribute('aria-label'),
          text:a?.textContent?.slice(0,80)},horizontalOverflow:document.documentElement.scrollWidth>innerWidth};
    }""")


def validate_visual_state(state, width, height, theme, focus):
    assert state['path'] == '/inbox/all/entry/101', 'wrong article identity'
    assert state['theme'] == theme, 'wrong theme'
    assert state['viewport'][:2] == [width, height], 'wrong viewport'
    assert state['fonts'] == 'loaded', 'fonts still loading'
    assert state['focusMode'] is focus and state['toggle']['pressed'] == str(focus).lower(), 'wrong focus state'
    assert state['toggle']['text'] == ('退出专注正文' if focus else '专注正文'), 'wrong focus control'
    assert state['title']['text'] == 'Synthetic long reading fixture', 'article title changed'
    assert state['paragraphs'] == 40, 'original reading paragraphs missing'
    assert not state['horizontalOverflow'], 'document horizontal overflow'
    assert state['toolbar']['rect']['width'] > 0 and state['toolbar']['rect']['height'] > 0, 'empty toolbar'
    assert state['scroll']['clientHeight'] > 0, 'empty actual scroller'
    assert state['toolbar']['visibility'] == 'visible' and state['toolbar']['display'] != 'none' and state['toolbar']['effectiveOpacity'] == 1, 'toolbar not visually available'
    assert state['body']['visibility'] == 'visible' and state['body']['effectiveOpacity'] == 1, 'body not visually available'
    assert state['toolbarCoverage']['left'] and state['toolbarCoverage']['right'], 'sticky toolbar leaves exposed body edges'
    assert not state['toolbar']['background'].startswith('rgba(') and state['toolbar']['background'] != 'transparent', 'toolbar backdrop is not opaque'
    if state['scroll']['top'] > 500:
        assert state['toolbarCoverage']['leftHit'] and state['toolbarCoverage']['rightHit'], 'body paints through sticky toolbar edges'


def validate_layout_return_focus(state):
    opener = state['layout']['opener']
    assert not state['layout']['open'], 'reading layout popup remained open after Escape'
    assert opener['focused'] and opener['focusVisible'], 'summary lacks keyboard focus-visible state'
    assert opener['display'] != 'none' and opener['visibility'] == 'visible' and opener['effectiveOpacity'] == 1, 'summary is not visually available'
    assert opener['rect']['width'] > 0 and opener['rect']['height'] > 0, 'summary has no rendered box'
    assert opener['outlineStyle'] not in ('none', 'hidden') and opener['outlineWidth'] > 0, 'summary has no computed visible focus outline'
    assert opener['outlineColor'] not in ('transparent', 'rgba(0, 0, 0, 0)'), 'summary focus outline is transparent'


def capture_visual(h, name, focus, width, height, theme, captures, *, require_layout_focus=False):
    # Observations only: no scrolling, focus changes, URL wait, CSS injection,
    # animation suppression or product state updates. Original actions stay below.
    h.page.evaluate('document.fonts.ready')
    deadline = time.monotonic() + 5
    while True:
        before = visual_state(h.page)
        began = time.monotonic()
        h.page.wait_for_timeout(300)
        after = visual_state(h.page)
        stable_ms = (time.monotonic() - began) * 1000
        if before == after and after['fonts'] == 'loaded' and after['toolbar']['effectiveOpacity'] == 1 and after['body']['effectiveOpacity'] == 1:
            break
        if time.monotonic() >= deadline:
            raise AssertionError('reading capture did not stabilize: ' + name)
    validate_visual_state(after, width, height, theme, focus)
    if require_layout_focus:
        validate_layout_return_focus(after)
    target = h.out / name
    assert not target.exists(), 'do not replace retained screenshot'
    h.page.screenshot(path=str(target))
    final = visual_state(h.page)
    assert final == after, 'reading state changed during capture'
    raw = target.read_bytes()
    pixels = validate_capture_png(raw, [round(width * after['viewport'][2]), round(height * after['viewport'][2])])
    captures.append({'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                     'focus': focus, 'layout_return_focus_required': require_layout_focus,
                     'stable_ms': stable_ms, 'normal_animations': True,
                     'before': before, 'after': final, 'png': pixels})



for width, height in [(1440, 960), (390, 844)]:
    for theme in ["light", "dark"]:
        h = Harness(f"reading-focus-{width}-{theme}", viewport={"width": width, "height": height})
        p = h.page
        p.add_init_script("if(!localStorage.getItem('settings'))localStorage.setItem('settings',JSON.stringify({showStatus:'all',themeMode:"+json.dumps(theme)+"}));")
        h.feeds[0]["icon"] = {"feed_id": 7, "icon_id": 0}
        h.entries = [{"id": 101, "user_id": 1, "feed_id": 7, "title": "Synthetic long reading fixture",
                      "url": "https://example.test/101", "hash": "101", "status": "read", "starred": False,
                      "published_at": "2026-10-04T01:00:00Z", "created_at": "2026-10-04T01:00:00Z",
                      "changed_at": "2026-10-04T01:00:00Z", "author": "Synthetic author", "reading_time": 8,
                      "content": '<h2>正文</h2>'+''.join(f'<p id="focus-p-{n}">Synthetic reading paragraph {n}. '+('Only isolated content. '*20)+'</p>' for n in range(40)),
                      "enclosures": [], "feed": h.feeds[0], "ai": {"state": "done", "score": 9,
                      "technical_score": 8, "business_score": 8, "summary": "Synthetic AI summary. "*10,
                      "reason": "Synthetic reason", "tags": ["fixture"]}}]
        measurements = []
        captures = []
        try:
            h.goto('/inbox/all/entry/101')
            expect(p.locator('#focus-p-39')).to_be_attached()
            expect(p.locator('body')).to_have_attribute('arco-theme', theme)
            summary = p.get_by_label('阅读排版', exact=True)
            button = p.get_by_role('button', name='专注正文', exact=True)
            alignment = p.evaluate("""() => {
              const a=document.querySelector('.review-reading-controls>summary[aria-label="阅读排版"]').getBoundingClientRect();
              const b=document.querySelector('.review-reading-bar>button').getBoundingClientRect();
              return {topDelta:Math.abs(a.top-b.top),heightDelta:Math.abs(a.height-b.height),height:Math.min(a.height,b.height)};
            }""")
            h.check('controls_aligned', alignment['topDelta'] <= 1 and alignment['heightDelta'] <= 1)
            h.check('controls_hit_height', alignment['height'] >= (43.9 if width < 620 else 35.9))
            measurements.append(alignment)
            capture_visual(h, 'normal-initial.png', False, width, height, theme, captures)
            button.click()
            exit_button = p.get_by_role('button', name='退出专注正文', exact=True)
            expect(exit_button).to_be_focused()
            expect(p.locator('.article-meta')).to_be_hidden()
            expect(p.locator('.article-header > .ai-verdict')).to_be_hidden()
            expect(p.locator('.article-title')).to_be_attached()
            h.check('focus_has_real_content_effect_and_keeps_title', True)
            capture_visual(h, 'focus-on-before-mid-scroll.png', True, width, height, theme, captures)
            # Find the actual overflowing ancestor of a body paragraph. Never use window.scrollY.
            p.locator('#focus-p-20').evaluate("""e=>{
              let n=e.parentElement;
              while(n&&!(n.scrollHeight>n.clientHeight&&/(auto|scroll)/.test(getComputedStyle(n).overflowY)))n=n.parentElement;
              if(!n)throw Error('No actual article scroller');window.readingTestScroll=n;
              n.scrollTop+=e.getBoundingClientRect().top-n.getBoundingClientRect().top-120;
            }""")
            def measure():
                return p.evaluate("""() => {
                  const s=window.readingTestScroll,b=document.querySelector('.review-reading-bar'),
                    target=document.querySelector('#focus-p-20'),r=b.getBoundingClientRect(),q=s.getBoundingClientRect();
                  return {scrollTop:s.scrollTop,anchor:target.getBoundingClientRect().top-r.bottom,
                    exitVisible:r.top>=q.top-1&&r.bottom<=q.bottom+1,outerScroll:document.querySelector('.article-content').scrollTop};
                }""")
            before = measure(); measurements.append(before)
            h.check('exit_reachable_at_mid_article_without_scroll_into_view', before['exitVisible'])
            capture_visual(h, 'focus-on-mid.png', True, width, height, theme, captures)
            exit_button.click()
            expect(button).to_be_focused()
            after = measure(); measurements.append(after)
            h.check('exit_preserves_reading_position', abs(before['anchor']-after['anchor']) <= 2)
            h.check('exit_does_not_scroll_outer_shell', before['outerScroll'] == after['outerScroll'])
            expect(p.locator('.article-meta')).to_be_visible()
            capture_visual(h, 'exit-normal-mid.png', False, width, height, theme, captures)
            # The original page footer/link and close action remain reachable.
            p.locator('#focus-p-39').scroll_into_view_if_needed()
            expect(p.locator('#focus-p-39')).to_be_visible()
            link = p.locator('.article-source-footer a')
            link.scroll_into_view_if_needed(); expect(link).to_be_visible()
            h.check('source_retained', link.get_attribute('href') == 'https://example.test/101')
            h.check('no_read_or_favorite_mutation', all(path.endswith('/ai/reading-session') for _, path, _ in h.writes))
            capture_visual(h, 'reading.png', False, width, height, theme, captures)
            p.get_by_role('button', name='关闭文章', exact=True).click()
            # Reopen the real article route. Verify native details keyboard
            # activation and the production article-close hotkey together.
            h.goto('/inbox/all/entry/101')
            summary = p.get_by_label('阅读排版', exact=True)
            layout = summary.locator('..')
            for target in ['summary', 'slider', 'reset']:
                summary.press('Enter')
                expect(layout).to_have_attribute('open', '')
                if target == 'summary':
                    capture_visual(h, 'keyboard-layout-open.png', False, width, height, theme, captures)
                    control = summary
                elif target == 'slider':
                    control = p.get_by_label('正文字号', exact=True)
                else:
                    control = p.get_by_role('button', name='恢复默认排版', exact=True)
                control.press('Escape')
                expect(layout).not_to_have_attribute('open', '')
                expect(summary).to_be_focused()
                expect(p.locator('.article-title')).to_have_text('Synthetic long reading fixture')
                h.check('keyboard_' + target + '_escape_closes_layout_only', p.url.endswith('/inbox/all/entry/101'))
                if target == 'summary':
                    # Observe the real returned focus; do not focus, scroll or add CSS.
                    capture_visual(h, 'keyboard-layout-escape-focus.png', False, width, height,
                                   theme, captures, require_layout_focus=True)
            p.keyboard.press('Escape')
            expect(p.locator('.article-content')).to_have_count(0)
            h.check('second_escape_keeps_native_article_close', p.url.endswith('/inbox/all'))
        except Exception as exc:
            h.errors.append(str(exc))
            failure = h.out / 'failure.png'
            if failure.exists():
                failure = h.out / ('failure-' + str(time.time_ns()) + '.png')
            p.screenshot(path=str(failure))
            raise
        finally:
            with (h.out/'visual-evidence.json').open('x') as stream:
                json.dump({'identity': IDENTITY, 'viewport': [width, height], 'theme': theme,
                           'captures': captures, 'expected_capture_count': 7,
                           'initial_state_note': 'Initial/top means before the explicit mid-article scroll; actual scroll values are retained without forcing zero.',
                           'stability_scope': '300ms DOM endpoints and same state after a single PNG; not continuous rAF or two-image equality.'}, stream, ensure_ascii=False, indent=2)
            (h.out/'measurements.json').write_text(json.dumps(measurements, indent=2))
            h.close()
