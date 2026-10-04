"""Refs #110: real built layout/scroll/focus controls in fresh loopback contexts."""
import json

from playwright.sync_api import expect
from review_reader_harness import Harness


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
        try:
            h.goto('/inbox/all/entry/101')
            expect(p.locator('#focus-p-39')).to_be_attached()
            expect(p.locator('body')).to_have_attribute('arco-theme', theme)
            summary = p.locator('.review-reading-controls > summary')
            button = p.get_by_role('button', name='专注正文', exact=True)
            alignment = p.evaluate("""() => {
              const a=document.querySelector('.review-reading-controls>summary').getBoundingClientRect();
              const b=document.querySelector('.review-reading-bar>button').getBoundingClientRect();
              return {topDelta:Math.abs(a.top-b.top),heightDelta:Math.abs(a.height-b.height),height:Math.min(a.height,b.height)};
            }""")
            h.check('controls_aligned', alignment['topDelta'] <= 1 and alignment['heightDelta'] <= 1)
            h.check('controls_hit_height', alignment['height'] >= (43.9 if width < 620 else 35.9))
            measurements.append(alignment)
            button.click()
            exit_button = p.get_by_role('button', name='退出专注正文', exact=True)
            expect(exit_button).to_be_focused()
            expect(p.locator('.article-meta')).to_be_hidden()
            expect(p.locator('.article-header > .ai-verdict')).to_be_hidden()
            expect(p.locator('.article-title')).to_be_attached()
            h.check('focus_has_real_content_effect_and_keeps_title', True)
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
            exit_button.click()
            expect(button).to_be_focused()
            after = measure(); measurements.append(after)
            h.check('exit_preserves_reading_position', abs(before['anchor']-after['anchor']) <= 2)
            h.check('exit_does_not_scroll_outer_shell', before['outerScroll'] == after['outerScroll'])
            expect(p.locator('.article-meta')).to_be_visible()
            # The original page footer/link and close action remain reachable.
            p.locator('#focus-p-39').scroll_into_view_if_needed()
            expect(p.locator('#focus-p-39')).to_be_visible()
            link = p.locator('.article-source-footer a')
            link.scroll_into_view_if_needed(); expect(link).to_be_visible()
            h.check('source_retained', link.get_attribute('href') == 'https://example.test/101')
            h.check('no_read_or_favorite_mutation', all(path.endswith('/ai/reading-session') for _, path, _ in h.writes))
            p.screenshot(path=str(h.out/'reading.png'))
            p.get_by_role('button', name='关闭文章', exact=True).click()
        except Exception as exc:
            h.errors.append(str(exc)); p.screenshot(path=str(h.out/'failure.png')); raise
        finally:
            (h.out/'measurements.json').write_text(json.dumps(measurements, indent=2))
            h.close()
