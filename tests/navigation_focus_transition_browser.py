"""Real built-reader focus ownership with both delayed media commit orders."""
import json
from playwright.sync_api import expect
from review_reader_harness import Harness

INIT="""(() => {
 const original=window.matchMedia.bind(window);window.focusTransition={hold:false,queue:[]};
 window.flushFocusTransition=()=>{window.focusTransition.hold=false;window.focusTransition.queue.splice(0).forEach(fn=>fn())};
 window.matchMedia=query=>{
  const media=original(query);if(query!=='(max-width: 768px), (pointer: coarse) and (max-height: 500px)')return media;
  const add=media.addEventListener.bind(media),remove=media.removeEventListener.bind(media),listeners=new Map();
  media.addEventListener=(kind,listener,...rest)=>{
   if(kind!=='change')return add(kind,listener,...rest);
   const wrap=event=>{if(window.focusTransition.hold)window.focusTransition.queue.push(()=>{if(listeners.has(listener))listener.call(media,event)});else listener.call(media,event)};
   listeners.set(listener,wrap);return add(kind,wrap,...rest);
  };
  media.removeEventListener=(kind,listener,...rest)=>{const wrap=listeners.get(listener);if(wrap){listeners.delete(listener);return remove(kind,wrap,...rest)}return remove(kind,listener,...rest)};
  return media;
 };
})()"""
reports=[]
cases=['close-before-commit','commit-before-close','user-focus','user-pointer-blank','user-keyboard','user-body-focus','reopen','new-modal','desktop-to-mobile','no-stale-ticket','issued-ticket-reversal','issued-ticket-expiry']
for case in cases:
    mobile=case!='desktop-to-mobile'
    h=Harness('navigation-focus-'+case,has_touch=True,is_mobile=True,viewport={'width':390 if mobile else 900,'height':844 if mobile else 900})
    p=h.page;p.add_init_script(INIT)
    try:
        h.goto('/inbox/all')
        status=p.locator('.ai-status-trigger');desktop=p.locator('.ai-toolbar > .review-navigation-trigger')
        if mobile:
            expect(status).to_be_visible();status.tap()
            p.locator('.ai-status-actions').get_by_role('button',name='快速跳转',exact=True).tap()
        else:desktop.click()
        expect(p.locator('.review-navigation-dialog')).to_be_visible()
        h.check('built_navigation_modal_is_open',True)
        p.evaluate("const b=document.createElement('button');b.id='focus-external';b.textContent='fixture external focus';document.body.append(b)")
        external=p.locator('#focus-external')
        if case=='no-stale-ticket':
            p.keyboard.press('Escape');expect(status).to_be_focused()
            p.evaluate('window.focusTransition.hold=true')
        else:p.evaluate('window.focusTransition.hold=true')
        p.set_viewport_size({'width':900 if mobile else 390,'height':900 if mobile else 844})
        p.wait_for_function('window.focusTransition.queue.length>0',timeout=5000)
        if case=='commit-before-close':
            p.evaluate('flushFocusTransition()');expect(status).to_have_count(0)
        if case!='no-stale-ticket':
            p.keyboard.press('Escape');expect(p.locator('.review-navigation-dialog')).to_have_count(0)
        if case=='user-focus':external.evaluate('(node)=>node.focus()');expect(external).to_be_focused()
        if case=='user-pointer-blank':p.evaluate("document.body.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}))")
        if case=='user-keyboard':
            p.keyboard.press('ArrowLeft');expect(status).to_be_focused()
        if case=='user-body-focus':
            p.evaluate('document.body.tabIndex=-1;document.body.focus()');expect(p.locator('body')).to_be_focused()
        if case=='issued-ticket-reversal':
            queued=p.evaluate('window.focusTransition.queue.length')
            p.set_viewport_size({'width':390,'height':844})
            p.wait_for_function('count => window.focusTransition.queue.length>count',arg=queued,timeout=5000)
        if case=='issued-ticket-expiry':p.wait_for_timeout(1200)
        if case=='reopen':
            p.keyboard.press('Control+k');expect(p.locator('.review-navigation-dialog')).to_be_visible()
        if case=='new-modal':
            p.evaluate("const d=document.createElement('dialog');d.id='fixture-new-modal';d.innerHTML='<button id=fixture-modal-focus>new modal focus</button>';document.body.append(d);d.showModal();d.querySelector('button').focus()")
            expect(p.locator('#fixture-modal-focus')).to_be_focused()
        p.evaluate('flushFocusTransition()')
        if case=='issued-ticket-reversal':
            expect(status).to_be_visible()
            p.set_viewport_size({'width':900,'height':900})
        if mobile:expect(status).to_have_count(0);expect(desktop).to_be_visible()
        else:expect(status).to_be_visible();expect(desktop).to_be_hidden()
        if case in ['close-before-commit','commit-before-close']:expect(desktop).to_be_focused()
        elif case=='desktop-to-mobile':expect(status).to_be_focused()
        elif case=='user-focus':expect(external).to_be_focused()
        elif case in ['user-pointer-blank','user-keyboard','user-body-focus','no-stale-ticket','issued-ticket-reversal','issued-ticket-expiry']:expect(desktop).not_to_be_focused()
        elif case=='reopen':
            expect(p.locator('.review-navigation-dialog')).to_be_visible()
            expect(p.get_by_role('combobox',name='搜索视图、分类或订阅')).to_be_focused()
        elif case=='new-modal':expect(p.locator('#fixture-modal-focus')).to_be_focused()
        if case=='no-stale-ticket':
            p.set_viewport_size({'width':390,'height':844});expect(status).to_be_visible()
            p.set_viewport_size({'width':900,'height':900});expect(desktop).to_be_visible();expect(desktop).not_to_be_focused()
        h.check('focus_contract_'+case,True)
        h.check('zero_api_writes',not h.writes)
        record=p.evaluate("({active:{tag:document.activeElement.tagName,cls:document.activeElement.className,id:document.activeElement.id},navigationOpen:!!document.querySelector('.review-navigation-dialog[open]'),queued:window.focusTransition.queue.length})")
        reports.append({'case':case,'actual':record,'errors':h.errors,'api_writes':len(h.writes)})
        (h.out/'transition.json').write_text(json.dumps(reports[-1],ensure_ascii=False,indent=2))
        p.screenshot(path=str(h.out/'final.png'))
    except Exception as exc:
        h.errors.append(str(exc));p.screenshot(path=str(h.out/'failure.png'));raise
    finally:h.close()
print(json.dumps({'cases':reports,'real_built_reader':True,'controlled_media_delivery':True},ensure_ascii=False))
