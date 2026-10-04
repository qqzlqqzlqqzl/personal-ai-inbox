"""Old release auth without a note-session marker; no unverified-owner purge."""
import re,json
from playwright.sync_api import expect
from review_reader_harness import Harness
SEED="""if(!sessionStorage.getItem('legacy-fixture-seeded')){
sessionStorage.setItem('legacy-fixture-seeded','1');
for(const [server,owner,id,note] of [[location.origin+'/mf','1','101','旧版关闭草稿 A'],[location.origin+'/mf','1','102','旧版关闭草稿 B'],[location.origin+'/mf','2','101','另一个账号的草稿'],['https://other.example.test/mf','1','101','另一个服务器的草稿']]){
const key='reader.note.draft:v1:'+encodeURIComponent(JSON.stringify([server,owner,id]));
sessionStorage.setItem(key,JSON.stringify({version:1,note,base:'服务器笔记',at:Date.now()}));}}"""
def run_case(kind):
    h=Harness('note-legacy-'+kind,persist_auth=True);p=h.page;h.ctx.add_init_script(SEED)
    h.feeds[0].update(icon={'feed_id':7,'icon_id':0},checked_at='2026-10-01T08:00:00Z',parsing_error_count=0,parsing_error_message='',disabled=False,hide_globally=False)
    h.entries=[{'id':101,'user_id':1,'feed_id':7,'title':'旧版草稿迁移验证','url':'https://example.test/101','comments_url':'','author':'测试','content':'<p>合成旧版会话，无 readerNoteSession 字段。</p>','hash':'101','published_at':'2026-10-01T08:00:00Z','created_at':'2026-10-01T08:00:00Z','changed_at':'2026-10-01T08:00:00Z','status':'read','starred':False,'reading_time':1,'enclosures':[],'feed':h.feeds[0],'ai':{'status':'pending'}}]
    h.notes={'101':'服务器笔记'};modes={'fail':True}
    def intercept(route,path,method):
        if path.endswith('/me') and modes['fail']:
            route.fulfill(status=401 if kind=='early401' else 503,json={'error_message':'synthetic pre-identity failure'});return True
        return False
    h.custom=intercept
    def key(owner,eid=101,server=None):
        return p.evaluate('({owner,id,server})=>"reader.note.draft:v1:"+encodeURIComponent(JSON.stringify([server||location.origin+"/mf",String(owner),String(id)]))',{'owner':owner,'id':eid,'server':server})
    def retained():
        return all(p.evaluate('(k)=>!!sessionStorage.getItem(k)',k) for k in [key(1),key(1,102),key(2),key(1,server='https://other.example.test/mf')])
    try:
        p.goto(h.base+'/inbox/',wait_until='domcontentloaded')
        if kind=='early401':
            p.wait_for_url('**/inbox/login');expect(p.get_by_text('本地笔记草稿清理未完成',exact=True)).to_be_visible()
            h.check('pre_identity401_reports_incomplete_and_preserves_unverified_scopes',retained() and not p.evaluate("JSON.parse(localStorage.getItem('auth')).token"))
            modes['fail']=False;p.locator('input[type="password"]').fill('synthetic-password');p.get_by_role('button',name=re.compile('^登录$|^Login$')).click();p.locator('.sidebar-profile-trigger').wait_for()
            p.goto(h.base+'/inbox/all/entry/101',wait_until='domcontentloaded');expect(p.get_by_role('textbox',name='我的笔记',exact=True)).to_have_value('服务器笔记');expect(p.get_by_role('button',name='恢复本地草稿',exact=True)).to_have_count(0)
            h.check('fresh_login_never_adopts_unverified_legacy_plaintext')
        else:
            logout=p.get_by_role('button',name=re.compile('^(退出登录|登出|退出)$'));logout.click()
            d=p.get_by_role('dialog');expect(d).to_contain_text('退出前确认草稿清理限制');expect(d).to_contain_text('退出不能保证删除');expect(d.get_by_role('button',name='导出本地草稿',exact=True)).to_have_count(0)
            h.check('unknown_scope_notice_never_claims_zero_complete_cleanup')
            p.set_viewport_size({'width':390,'height':400});p.locator('.note-logout-modal .arco-modal-content').get_by_role('alert').scroll_into_view_if_needed();p.screenshot(path=str(h.out/'legacy-cleanup-warning.png'))
            if kind=='explicit':
                d.get_by_role('button',name='仍退出账号',exact=True).click();p.wait_for_url('**/inbox/login');expect(p.get_by_text('本地笔记草稿清理未完成',exact=True)).to_be_visible()
                h.check('explicit_early_logout_reports_residual_plaintext_and_keeps_other_owners',retained())
            else:
                d.get_by_role('button',name='取消，保留会话',exact=True).click();h.check('cancel_keeps_legacy_auth_and_all_drafts',retained() and bool(p.evaluate("JSON.parse(localStorage.getItem('auth')).token")))
                p.set_viewport_size({'width':1440,'height':960});modes['fail']=False;p.get_by_role('button',name='重试',exact=True).click();p.get_by_role('button',name='AI 精选',exact=True).wait_for()
                p.goto(h.base+'/inbox/all/entry/101',wait_until='domcontentloaded');expect(p.get_by_role('button',name='恢复本地草稿',exact=True)).to_be_visible();h.check('identity_retry_safely_migrates_only_verified_owner')
                p.get_by_role('button',name='关闭文章',exact=True).click();p.locator('.sidebar-profile-trigger').click();p.get_by_text(re.compile('^(退出登录|登出|退出)$')).last.click()
                d=p.get_by_role('dialog');expect(d).to_contain_text('2 篇未同步笔记草稿');d.get_by_role('button',name='丢弃本地草稿并退出',exact=True).click();p.wait_for_url('**/inbox/login')
                h.check('verified_legacy_logout_purges_only_verified_scope',p.evaluate('(k)=>sessionStorage.getItem(k)',key(1)) is None and p.evaluate('(k)=>sessionStorage.getItem(k)',key(1,102)) is None and bool(p.evaluate('(k)=>sessionStorage.getItem(k)',key(2))) and bool(p.evaluate('(k)=>sessionStorage.getItem(k)',key(1,server='https://other.example.test/mf'))))
    except Exception as exc:
        h.errors.append(str(exc));p.screenshot(path=str(h.out/'failure.png'),full_page=True);raise
    finally:h.close()
for kind in ['migration','explicit','early401']:run_case(kind)
