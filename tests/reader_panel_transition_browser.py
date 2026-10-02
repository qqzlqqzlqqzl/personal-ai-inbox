"""Controlled delayed media event reproduces panel helper's count/resize race.

The same built reader and real Chromium are used. No sleeps or weakened mobile
focus assertions: hold one media change until the fixture releases it.
"""
import json
from playwright.sync_api import expect
from review_reader_harness import Harness

h=Harness('reader-panel-transition',has_touch=True,is_mobile=True)
p=h.page
p.add_init_script("""(() => {
 const original=window.matchMedia.bind(window);window.pendingCompact=[];
 window.flushCompact=()=>{window.holdCompact=false;for(const deliver of window.pendingCompact.splice(0))deliver()};
 window.matchMedia=query=>{const media=original(query);if(query.includes('max-width: 768px')){
   const add=media.addEventListener.bind(media);
   media.addEventListener=(kind,listener,...rest)=>add(kind,event=>{
     if(window.holdCompact)window.pendingCompact.push(()=>listener(event));else listener(event)
   },...rest);
 }return media};
})()""")
try:
    h.goto();expect(p.locator('.ai-toolbar > .ai-settings-button')).to_be_visible()
    p.evaluate('window.holdCompact=true');p.set_viewport_size({'width':390,'height':844})
    # Old helper sees count0, skips the status opener, then waits for a settings
    # button which disappears when the queued media event commits compact DOM.
    before={'status_count':p.locator('.ai-status-trigger').count(),
            'compact':p.evaluate("matchMedia('(max-width: 768px)').matches")}
    h.check('old_count_branch_skips_compact_opener',before=={'status_count':0,'compact':True})
    p.evaluate('flushCompact()');expect(p.locator('.ai-status-trigger')).to_be_visible()
    expect(p.get_by_role('button',name='AI 设置 · 来源',exact=True)).to_have_count(0)
    h.check('old_branch_leaves_settings_unavailable',True)
    p.set_viewport_size({'width':900,'height':900});expect(p.locator('.ai-toolbar > .ai-settings-button')).to_be_visible()
    p.evaluate('window.holdCompact=true');p.set_viewport_size({'width':390,'height':844})
    # Timed event delivery is the controlled stimulus; panel must await the DOM.
    p.evaluate('setTimeout(flushCompact,200)');h.panel()
    expect(p.locator('dialog[open]')).to_have_count(1)
    h.check('panel_waits_for_delayed_compact_commit',True)
    p.locator('.ai-dialog>header').get_by_role('button',name='关闭',exact=True).click()
    expect(p.locator('.ai-status-trigger')).to_be_focused()
    h.check('visible_opener_focus_and_zero_api_writes',not h.writes)
    (h.out/'transition.json').write_text(json.dumps({'controlled_media_delay':True,'before':before,'errors':h.errors,'api_writes':len(h.writes)},indent=2))
finally:h.close()
