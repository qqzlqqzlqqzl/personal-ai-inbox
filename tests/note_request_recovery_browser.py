"""Real pinned ofetch/browser hung-note recovery; local synthetic routes only."""
import re
from playwright.sync_api import expect
from review_reader_harness import Harness
h=Harness('note-request-recovery',persist_auth=True);p=h.page
h.feeds[0].update(icon={'feed_id':7,'icon_id':0},checked_at='2026-10-01T08:00:00Z',parsing_error_count=0,parsing_error_message='',disabled=False,hide_globally=False)
h.entries=[{'id':101,'user_id':1,'feed_id':7,'title':'挂起请求恢复验证','url':'https://example.test/101','comments_url':'','author':'测试','content':'<p>合成笔记请求，只验证客户端恢复。</p>','hash':'101','published_at':'2026-10-01T08:00:00Z','created_at':'2026-10-01T08:00:00Z','changed_at':'2026-10-01T08:00:00Z','status':'read','starred':False,'reading_time':1,'enclosures':[],'feed':h.feeds[0],'ai':{'status':'pending'}}]
h.notes={'101':'服务器笔记'};modes={'hold':True};pending=[]
def intercept(route,path,method):
    if '/ai/notes/' in path and method=='PUT' and modes['hold']:
        pending.append(route);return True
    return False
h.custom=intercept
key=lambda:p.evaluate('"reader.note.draft:v1:"+encodeURIComponent(JSON.stringify([location.origin+"/mf","1","101"]))')
try:
    h.goto();p.goto(h.base+'/inbox/all/entry/101',wait_until='domcontentloaded')
    note=p.get_by_role('textbox',name='我的笔记',exact=True);expect(note).to_have_value('服务器笔记')
    p.clock.install(time='2026-10-02T10:00:00Z');p.clock.pause_at('2026-10-02T10:00:01Z')
    note.fill('第一份挂起保存');note.press('Control+s');expect(p.locator('.article-note-head small')).to_contain_text('正在保存')
    note.fill('最新排队草稿');p.get_by_role('button',name='立即保存笔记',exact=True).click()
    h.check('hung_PUT_queued_latest_has_one_request',len(pending)==1)
    p.clock.run_for(15010);expect(p.get_by_role('button',name='重试保存',exact=True)).to_be_enabled();expect(p.locator('.article-note-head small')).to_contain_text('保存未完成')
    h.check('deadline_keeps_latest_draft',p.evaluate('(key)=>JSON.parse(sessionStorage.getItem(key)).note',key())=='最新排队草稿')
    modes['hold']=False;p.get_by_role('button',name='重试保存',exact=True).click();expect(p.locator('.article-note-head small')).to_contain_text('已保存')
    h.check('retry_saves_latest_and_clears_only_successful_draft',h.notes['101']=='最新排队草稿' and p.evaluate('(key)=>sessionStorage.getItem(key)',key()) is None)
    for route in pending:
        try:route.fulfill(json={'note':'旧挂起保存响应','updated_at':'old'})
        except Exception:pass # The real browser may already have cancelled its transport.
    pending.clear();expect(note).to_have_value('最新排队草稿');h.check('late_timed_out_response_does_not_replace_editor')
    modes['hold']=True;note.fill('退出前挂起草稿');note.press('Control+s');expect(p.locator('.article-note-head small')).to_contain_text('正在保存')
    p.locator('.sidebar-profile-trigger').click();p.clock.run_for(500);p.get_by_text(re.compile('^(退出登录|登出|退出)$')).last.click()
    p.clock.run_for(500)
    p.get_by_role('dialog').get_by_role('button',name='丢弃本地草稿并退出',exact=True).click();p.wait_for_url('**/inbox/login')
    before=len(h.writes);p.clock.run_for(20000)
    h.check('logout_retires_hung_request_queue_and_deadline',len(h.writes)==before and p.evaluate('(key)=>sessionStorage.getItem(key)',key()) is None)
    p.clock.resume()
    modes['hold']=False;p.locator('input[type="password"]').fill('synthetic-password');p.get_by_role('button',name=re.compile('^登录$|^Login$')).click();p.locator('.sidebar-profile-trigger').wait_for()
    p.goto(h.base+'/inbox/all/entry/101',wait_until='domcontentloaded');note=p.get_by_role('textbox',name='我的笔记',exact=True);expect(note).to_have_value('最新排队草稿')
    for route in pending:
        try:route.fulfill(json={'note':'旧会话响应','updated_at':'old session'})
        except Exception:pass
    expect(note).to_have_value('最新排队草稿');h.check('relogin_remains_current_after_hung_old_request')
    p.screenshot(path=str(h.out/'recovered-note.png'))
except Exception as exc:
    h.errors.append(str(exc));p.screenshot(path=str(h.out/'failure.png'),full_page=True);raise
finally:h.close()
