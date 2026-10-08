"""R05–R08 actual built UI. Synthetic APIs only; never reads production data."""
import json,re
from review_reader_harness import Harness
from playwright.sync_api import expect

# Keep persisted auth generation across navigation/reload, as in a real tab.
h=Harness('review-reading',persist_auth=True);p=h.page
modes={'save_error':False,'load_error':False,'hold_save':False,'image_ok':False}
pending=[]
h.feeds[0].update(icon={'feed_id':7,'icon_id':0},checked_at='2026-10-01T08:00:00Z',parsing_error_count=0,parsing_error_message='',disabled=False,hide_globally=False)
content='<h2>第一章 控制原理</h2>'+''.join(f'<p>第{i}段：这是隔离阅读测试，检查排版与恢复操作，不发送生产请求。</p>' for i in range(12))+'<img src="https://images.example.test/board.svg" alt="电路板示意图"><h2>第二章 设计验证</h2><p>读取完整正文和笔记。</p>'
def entry(eid):
    return {'id':eid,'user_id':1,'feed_id':7,'title':f'阅读验证文章 {eid}','url':f'https://example.test/article/{eid}','comments_url':'','author':'测试作者','content':content,'hash':str(eid),'published_at':'2026-10-01T08:00:00Z','created_at':'2026-10-01T08:00:00Z','changed_at':'2026-10-01T08:00:00Z','status':'read','starred':False,'reading_time':4,'enclosures':[],'feed':h.feeds[0],'ai':{'status':'pending','has_note':True}}
h.entries=[entry(101),entry(102)];h.notes={'101':'服务器原始笔记 A','102':'服务器原始笔记 B'}
def intercept(route,path,method):
    if '/ai/notes/' in path:
        if method=='GET' and modes['load_error']:route.fulfill(status=503,json={'detail':'fixture load failure'});return True
        if method=='PUT' and modes['save_error']:route.fulfill(status=503,json={'detail':'fixture save failure'});return True
        if method=='PUT' and modes['hold_save']:pending.append(route);return True
    return False
h.custom=intercept
image_calls=[]
def image_route(route):
    image_calls.append(route.request.url)
    if modes['image_ok']:route.fulfill(status=200,content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"><rect width="900" height="600" fill="#b6d9c7"/><text x="40" y="100" font-size="30">Synthetic board</text></svg>')
    else:route.fulfill(status=404,body='fixture missing')
h.ctx.route('https://images.example.test/**',image_route)
h.ctx.grant_permissions(['clipboard-read','clipboard-write'])
p.on('dialog',lambda d:d.accept())
def open_entry(eid,recovering=False):
    p.goto(h.base+f'/inbox/all/entry/{eid}',wait_until='domcontentloaded')
    if recovering:expect(p.get_by_role('button',name='恢复本地草稿',exact=True)).to_be_visible()
    else:expect(p.get_by_role('textbox',name='我的笔记',exact=True)).to_be_enabled()
def draft_key(eid):
    return p.evaluate('(id)=>"reader.note.draft:v1:"+encodeURIComponent(JSON.stringify([location.origin+"/mf","1",String(id)]))',eid)
def note_writes():return [w for w in h.writes if '/ai/notes/' in w[1]]
def close_article():p.get_by_role('button',name='关闭文章',exact=True).click()
try:
    h.goto();writes=len(h.writes)
    p.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,isComposing:true,bubbles:true}))");expect(p.locator('.review-navigation-dialog')).to_have_count(0)
    p.keyboard.press('Control+k');expect(p.get_by_role('dialog',name='快速跳转',exact=True)).to_be_visible()
    search=p.get_by_role('combobox',name='搜索视图、分类或订阅')
    expect(search).to_have_attribute('aria-activedescendant','review-command-0');search.press('ArrowDown');expect(search).to_have_attribute('aria-activedescendant','review-command-1');search.press('ArrowUp');expect(search).to_have_attribute('aria-activedescendant','review-command-0')
    search.fill('已订阅技术源');search.evaluate("e=>e.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:13,isComposing:true,bubbles:true}))");expect(p.get_by_role('dialog',name='快速跳转',exact=True)).to_be_visible();h.check('R08_IME_and_nonempty_arrow_selection')
    search.fill('普通输入abc');expect(search).to_have_value('普通输入abc');expect(p.get_by_role('dialog',name='快速跳转',exact=True)).to_be_visible();h.check('R08_plain_typing_does_not_navigate');search.fill('已订阅技术源');expect(p.locator('#review-command-list').get_by_role('option')).to_have_count(1);search.press('Enter');p.wait_for_url('**/inbox/feed/7')
    h.check('R08_feed_navigation_zero_writes',len(h.writes)==writes)
    trigger=p.get_by_role('button',name='快速跳转',exact=False);trigger.click();search=p.get_by_role('combobox',name='搜索视图、分类或订阅');search.fill('不存在的内容');search.press('ArrowDown');search.press('Enter');expect(p.get_by_role('dialog',name='快速跳转')).to_be_visible();search.press('Escape')
    h.check('R08_empty_keyboard_and_focus_restore',trigger.evaluate('e=>e===document.activeElement'))
    h.panel();p.keyboard.press('Control+k');expect(p.locator('.review-navigation-dialog')).to_have_count(0);h.check('R08_does_not_nest_in_console')
    p.locator('.ai-dialog>header').get_by_role('button',name='关闭',exact=True).click()
    p.keyboard.press('Control+k');search=p.get_by_role('combobox',name='搜索视图、分类或订阅');search.fill('收藏');search.press('Enter');p.wait_for_url('**/inbox/starred');h.check('R08_starred_navigation')
    ai_before=p.evaluate("localStorage.getItem('ai-view-state')");before_routes=len(h.writes)
    for term,path in [('今天的文章','today'),('所有未读文章','all'),('技术博客','category/1')]:
        p.get_by_role('button',name='快速跳转',exact=False).click();search=p.get_by_role('combobox',name='搜索视图、分类或订阅');search.fill(term)
        # Category query also matches a feed detail; choose the exact visible category.
        if path=='category/1':p.get_by_role('option').filter(has=p.get_by_text('技术博客',exact=True)).click()
        else:search.press('Enter')
        p.wait_for_url('**/inbox/'+path);h.check('R08_'+path.replace('/','_')+'_preserves_AI_filter',p.evaluate("localStorage.getItem('ai-view-state')")==ai_before)
    h.check('R08_view_routes_zero_writes',len(h.writes)==before_routes)
    open_entry(101);note=p.get_by_role('textbox',name='我的笔记',exact=True);expect(note).to_have_value(h.notes['101'])
    modes['save_error']=True;note.fill('保存失败但必须保留的草稿');note.press('Control+s');expect(p.locator('.article-note-head small')).to_contain_text('保存未完成')
    h.check('R05_failed_save_keeps_draft',p.evaluate('key=>JSON.parse(sessionStorage.getItem(key)).note',draft_key(101))=='保存失败但必须保留的草稿')
    modes['save_error']=False;p.get_by_role('button',name='重试保存',exact=True).click();expect(p.locator('.article-note-head small')).to_contain_text('已保存');h.check('R05_retry_clears_only_confirmed_draft',p.evaluate('key=>sessionStorage.getItem(key)',draft_key(101)) is None)
    key=draft_key(101);p.evaluate('({key})=>sessionStorage.setItem(key,JSON.stringify({version:1,note:"待人工确认的恢复草稿",base:"旧服务器内容",at:Date.now()}))',{'key':key})
    before=len(note_writes());p.reload();expect(p.get_by_role('button',name='恢复本地草稿',exact=True)).to_be_visible();expect(p.locator('.review-note-recovery')).to_contain_text('服务器内容已变化')
    p.get_by_role('button',name='复制笔记备份',exact=True).click();h.check('R05_copy_uses_unsynced_draft',p.evaluate('navigator.clipboard.readText()')=='待人工确认的恢复草稿')
    p.get_by_role('button',name='恢复本地草稿',exact=True).click();note=p.get_by_role('textbox',name='我的笔记',exact=True);expect(note).to_have_value('待人工确认的恢复草稿')
    note.focus();note.press('Tab');p.wait_for_timeout(850);h.check('R05_recovery_blur_never_overwrites',len(note_writes())==before)
    close_article();p.wait_for_timeout(150);h.check('R05_recovery_unmount_never_overwrites',len(note_writes())==before)
    open_entry(101,recovering=True);p.get_by_role('button',name='恢复本地草稿',exact=True).click();p.get_by_role('button',name='立即保存笔记',exact=True).click();expect(p.locator('.article-note-head small')).to_contain_text('已保存');h.check('R05_recovery_explicit_save',h.notes['101']=='待人工确认的恢复草稿')
    modes['hold_save']=True;note=p.get_by_role('textbox',name='我的笔记',exact=True);note.fill('A 的延迟保存');note.press('Control+s');expect(p.locator('.article-note-head small')).to_contain_text('正在保存')
    close_article();p.locator('[data-entry-id="102"]').first.click();note_b=p.get_by_role('textbox',name='我的笔记',exact=True);expect(note_b).to_have_value('服务器原始笔记 B')
    modes['hold_save']=False
    for route in pending:
        eid=route.request.url.rsplit('/',1)[-1];h.notes[eid]=route.request.post_data_json['note'];route.fulfill(json={'note':h.notes[eid],'updated_at':'2026-10-01T12:00:00Z'})
    pending.clear();expect(note_b).to_have_value('服务器原始笔记 B');h.check('R05_A_response_cannot_replace_B')
    close_article();modes['load_error']=True;p.goto(h.base+'/inbox/all/entry/102',wait_until='domcontentloaded');expect(p.get_by_role('button',name='重新加载笔记',exact=True)).to_be_enabled();before=len(note_writes());expect(p.get_by_role('textbox',name='我的笔记',exact=True)).to_be_disabled();h.check('R05_load_failure_zero_writes',len(note_writes())==before)
    modes['load_error']=False;p.get_by_role('button',name='重新加载笔记',exact=True).click();expect(p.get_by_role('textbox',name='我的笔记',exact=True)).to_have_value('服务器原始笔记 B');h.check('R05_retry_load_recovers')
    # Existing reader state remains unchanged by typography controls.
    before=[w for w in h.writes if '/v1/entries' in w[1]]
    telemetry_before=[w[2] for w in h.writes if w[1].endswith('/ai/reading-session')];session_before=telemetry_before[-1]['session_id'];open_count=sum(w['action']=='open' for w in telemetry_before)
    reading_summary=p.get_by_label('阅读排版',exact=True);reading_summary.focus();reading_summary.press('Enter');expect(reading_summary.locator('..')).to_have_attribute('open','');expect(reading_summary).to_be_focused();h.check('R06_keyboard_opens_reading_controls')
    p.get_by_label('正文字号',exact=True).fill('1.35');p.get_by_label('正文行距',exact=True).fill('2.2');p.get_by_label('正文栏宽',exact=True).fill('80')
    h.check('R06_line_height_applied',p.locator('.article-body').evaluate('e=>Math.abs(parseFloat(getComputedStyle(e).lineHeight)/parseFloat(getComputedStyle(e).fontSize)-2.2)<0.01'))
    p.get_by_role('button',name='专注正文',exact=True).click();expect(p.locator('.article-meta')).to_be_hidden();expect(p.get_by_role('button',name='退出专注正文',exact=True)).to_be_visible();p.get_by_role('button',name='退出专注正文',exact=True).click()
    p.get_by_role('button',name='恢复默认排版',exact=True).click();expect(p.get_by_label('正文行距',exact=True)).to_have_value('1.8')
    h.check('R06_typography_focus_no_read_mutation',before==[w for w in h.writes if '/v1/entries' in w[1]])
    telemetry_after=[w[2] for w in h.writes if w[1].endswith('/ai/reading-session')];h.check('R06_controls_keep_reading_session',telemetry_after[-1]['session_id']==session_before and sum(w['action']=='open' for w in telemetry_after)==open_count)
    reading_summary.focus();reading_summary.press('Enter');expect(reading_summary.locator('..')).not_to_have_attribute('open','');expect(reading_summary).to_be_focused();h.check('R06_keyboard_closes_reading_controls_with_focus')
    notice=p.get_by_role('group',name='图片加载恢复');notice.scroll_into_view_if_needed();expect(notice).to_contain_text('电路板示意图');expect(notice.get_by_role('link',name='打开原图')).to_have_attribute('href','https://images.example.test/board.svg')
    expect(p.locator('.image-overlay-button:enabled')).to_have_count(0);expect(p.get_by_role('dialog',name='Lightbox',exact=True)).to_have_count(0);h.check('R07_failed_image_cannot_open_empty_lightbox')
    before_images=len(image_calls);p.wait_for_timeout(250);h.check('R07_failure_no_automatic_retry',len(image_calls)==before_images)
    modes['image_ok']=True;notice.get_by_role('button',name='重试这张图片',exact=True).focus();notice.get_by_role('button',name='重试这张图片',exact=True).press('Enter');expect(p.locator('img[alt="电路板示意图"]')).to_be_visible();expect(p.locator('.image-overlay-button').first).to_be_enabled();h.check('R07_manual_retry_success_enables_preview')
    preview=p.locator('.image-overlay-button').first;preview.scroll_into_view_if_needed();preview.focus()
    scroll_before=preview.evaluate("e=>{let n=e.parentElement;while(n&&!(n.scrollHeight>n.clientHeight&&/(auto|scroll)/.test(getComputedStyle(n).overflowY)))n=n.parentElement;window.fixtureImageScroll=n;return n?.scrollTop??0}")
    h.check('R07_preview_has_real_scrolled_ancestor',p.evaluate('!!window.fixtureImageScroll') and scroll_before>0)
    preview.press('Space');expect(p.get_by_role('dialog',name='Lightbox',exact=True)).to_be_visible();p.keyboard.press('Control+k');expect(p.locator('.review-navigation-dialog')).to_have_count(0);h.check('R08_does_not_nest_in_lightbox');p.keyboard.press('Escape');expect(p.get_by_role('dialog',name='Lightbox',exact=True)).to_have_count(0);expect(p.locator('img[alt="电路板示意图"]').first).to_be_visible();expect(preview).to_be_focused();h.check('R07_lightbox_focus_and_scroll_return',abs(p.evaluate('window.fixtureImageScroll?.scrollTop??0')-scroll_before)<=2);h.check('R07_lightbox_return')
    # Bound repeated manual requests and exercise TOC search on the actual reader.
    toc_opener=p.get_by_role('button',name=re.compile('目录|Table of Contents'));toc_opener.focus();toc_scroll=p.evaluate('window.fixtureImageScroll.scrollTop');toc_opener.press('Enter')
    toc=p.get_by_label('搜索文章目录',exact=True);toc.fill('不存在章节');expect(p.get_by_role('button',name='清除目录搜索',exact=True)).to_be_visible();p.get_by_role('button',name='清除目录搜索',exact=True).click();expect(toc).to_have_value('');expect(toc).to_be_focused();p.keyboard.press('Escape');expect(toc).to_be_hidden();expect(p.locator('.article-content')).to_be_visible();expect(toc_opener).to_be_focused();h.check('R06_TOC_keyboard_close_focus_and_position',abs(p.evaluate('window.fixtureImageScroll.scrollTop')-toc_scroll)<=2);h.check('R06_TOC_search_empty_reset')
    modes['image_ok']=False;p.reload();expect(p.get_by_role('textbox',name='我的笔记',exact=True)).to_be_enabled();p.locator('.article-note').scroll_into_view_if_needed();notice=p.get_by_role('group',name='图片加载恢复');expect(notice).to_be_visible();before_images=len(image_calls)
    for attempt in range(1,4):
        notice.get_by_role('button',name='重试这张图片',exact=True).click();expect(notice).to_contain_text(f'已手动重试 {attempt} / 3 次')
    expect(notice.get_by_role('button',name='已达本次重试上限',exact=True)).to_be_disabled();h.check('R07_three_manual_retries_maximum',len(image_calls)==before_images+3)
    # 200% visual zoom in the isolated page; retain the same reading state.
    p.evaluate("document.documentElement.style.zoom='2'")
    h.check('R06_200_percent_zoom_no_horizontal_overflow',p.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    p.evaluate("document.documentElement.style.zoom=''")
    p.set_viewport_size({'width':390,'height':844});p.get_by_label('阅读排版',exact=True).click();expect(p.get_by_label('正文栏宽',exact=True)).to_be_disabled();h.check('R06_mobile_controls_no_overflow',p.evaluate('document.documentElement.scrollWidth<=innerWidth'))
    p.screenshot(path=str(h.out/'mobile-reading.png'),full_page=True);p.get_by_label('阅读排版',exact=True).click()
    # Compact article controls expose the same searchable TOC on a narrow viewport.
    toc_button=p.get_by_role('button',name=re.compile('目录|Table of Contents'))
    if not toc_button.count() or not toc_button.first.is_visible():
        p.get_by_role('button',name=re.compile('更多|More actions')).first.click()
        p.get_by_text(re.compile('^(目录|Table of Contents)$')).first.click()
    else:toc_button.first.click()
    toc=p.get_by_label('搜索文章目录',exact=True);toc.fill('第二章');expect(p.get_by_text('第二章 设计验证',exact=True).last).to_be_visible();p.get_by_text('第二章 设计验证',exact=True).last.click();expect(toc).to_have_count(0)
    h.check('R06_mobile_TOC_search_jumps',p.locator('.article-body h2').last.evaluate('e=>{const r=e.getBoundingClientRect();return r.top>=0&&r.top<innerHeight}'))
    close_article();open_entry(101);notice=p.get_by_role('group',name='图片加载恢复');notice.scroll_into_view_if_needed();expect(notice).to_contain_text('已手动重试 0 / 3 次');h.check('R07_article_change_resets_attempts')
    modes['image_ok']=True;notice.get_by_role('button',name='重试这张图片',exact=True).press('Enter');expect(p.locator('.image-overlay-button').first).to_be_enabled();h.check('R07_mobile_keyboard_recovery')
    close_article();h.entries[1]['content']='<p>无标题的隔离正文，不能生成不存在的文章目录。</p>';p.set_viewport_size({'width':1440,'height':960});open_entry(102)
    expect(p.get_by_role('button',name=re.compile('目录|Table of Contents'))).to_have_count(0);h.check('R06_headingless_article_has_no_TOC');close_article();p.set_viewport_size({'width':390,'height':844})

    p.locator('.ai-status-trigger').click();p.get_by_role('button',name='快速跳转',exact=False).click();expect(p.get_by_role('dialog',name='快速跳转')).to_be_visible();h.check('R08_mobile_palette_no_overflow',p.locator('.review-navigation-dialog').evaluate('e=>e.scrollWidth<=e.clientWidth'));p.screenshot(path=str(h.out/'mobile-navigation.png'),full_page=True);p.keyboard.press('Escape')
except Exception as exc:
    h.errors.append(str(exc));p.screenshot(path=str(h.out/'failure.png'),full_page=True);raise
finally:h.close()
