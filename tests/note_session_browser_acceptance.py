"""Session-owned draft privacy in the actual pinned build; local synthetic APIs only."""
import json,re
from review_reader_harness import Harness
from playwright.sync_api import expect

h=Harness('note-session-privacy',persist_auth=True);p=h.page
h.feeds[0].update(icon={'feed_id':7,'icon_id':0},checked_at='2026-10-01T08:00:00Z',parsing_error_count=0,parsing_error_message='',disabled=False,hide_globally=False)
modes={'save_error':True,'identity_error':False,'version_error':False,'note401':False}
h.entries=[{'id':eid,'user_id':1,'feed_id':7,'title':f'隔离草稿验证 {eid}','url':f'https://example.test/{eid}','comments_url':'','author':'测试','content':'<p>只使用合成内容验证退出后的草稿清理。</p>','hash':str(eid),'published_at':'2026-10-01T08:00:00Z','created_at':'2026-10-01T08:00:00Z','changed_at':'2026-10-01T08:00:00Z','status':'read','starred':False,'reading_time':1,'enclosures':[],'feed':h.feeds[0],'ai':{'status':'pending'}} for eid in [101,102,103]]
h.notes={'101':'服务器 A','102':'服务器 B','103':'服务器 C'}
def intercept(route,path,method):
    if (path.endswith('/me') and modes['identity_error']) or (path.endswith('/version') and modes['version_error']):
        route.fulfill(status=503,json={'error_message':'synthetic unavailable'});return True
    if '/ai/notes/' in path:
        if method=='PUT' and modes['save_error']:
            route.fulfill(status=503,json={'error_message':'synthetic save failure'});return True
        if method=='GET' and modes['note401']:
            route.fulfill(status=401,json={'error_message':'synthetic expired session'});return True
    return False
h.custom=intercept
def key(owner,eid,server=None):
    return p.evaluate('({owner,eid,server})=>"reader.note.draft:v1:"+encodeURIComponent(JSON.stringify([server||location.origin+"/mf",String(owner),String(eid)]))',{'owner':owner,'eid':eid,'server':server})
def open_entry(eid,recovery=False):
    p.goto(h.base+f'/inbox/all/entry/{eid}',wait_until='domcontentloaded')
    if recovery:expect(p.get_by_role('button',name='恢复本地草稿',exact=True)).to_be_visible()
    else:expect(p.get_by_role('textbox',name='我的笔记',exact=True)).to_be_enabled()
def failed_closed():
    for eid in [101,102]:
        open_entry(eid)
        note=p.get_by_role('textbox',name='我的笔记',exact=True);note.fill(f'未同步隐私草稿 {eid}');note.press('Control+s')
        expect(p.locator('.article-note-head small')).to_contain_text('保存未完成')
        p.get_by_role('button',name='关闭文章',exact=True).click()
        p.wait_for_timeout(100)
    expect(p.locator('.article-note')).to_have_count(0)
def seed_others():
    other=[key(2,101),key(1,101,'https://other.example.test/mf'),'other-feature:keep']
    p.evaluate('(keys)=>keys.forEach(key=>sessionStorage.setItem(key,"keep"))',other);return other
def assert_purged(other):
    h.check('outgoing_A_B_deleted_'+str(len(h.checks)),all(p.evaluate('(k)=>sessionStorage.getItem(k)',key(1,eid)) is None for eid in [101,102]))
    h.check('other_server_owner_and_feature_retained_'+str(len(h.checks)),all(p.evaluate('(k)=>sessionStorage.getItem(k)',k)=='keep' for k in other))
def dialog():
    d=p.get_by_role('dialog');expect(d).to_contain_text('2 篇未同步笔记草稿');return d
def fresh_login():
    p.locator('input[type="password"]').fill('synthetic-password')
    p.get_by_role('button',name=re.compile('^登录$|^Login$')).click()
    # Login may correctly restore the article that triggered a 401.
    p.locator('.sidebar-profile-trigger').wait_for()
    h.goto()
try:
    h.goto();failed_closed();other=seed_others()
    # Reload retains the same authenticated generation and closed-article recovery.
    open_entry(101,recovery=True);p.get_by_role('button',name='关闭文章',exact=True).click()
    h.check('same_session_reload_recovers_closed_failed_draft')
    p.locator('.sidebar-profile-trigger').click();p.get_by_text(re.compile('^(退出登录|登出|退出)$')).last.click()
    d=dialog();p.set_viewport_size({'width':390,'height':640})
    for width,height in [(360,360),(360,480),(390,640),(430,640)]:
        p.set_viewport_size({'width':width,'height':height})
        actual=p.locator('.note-logout-modal')
        actual.evaluate('async e=>{await Promise.all(e.getAnimations({subtree:true}).filter(a=>Number.isFinite(a.effect.getTiming().iterations)).map(a=>a.finished.catch(()=>{})));await new Promise(requestAnimationFrame)}')
        h.check(f'logout_notice_{width}x{height}_entire_dialog_and_actions_reachable',actual.evaluate('e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight&&e.scrollWidth<=e.clientWidth}') and all(b.evaluate('e=>{const r=e.getBoundingClientRect();return r.height>=44&&r.right<=innerWidth&&r.bottom<=innerHeight}') for b in actual.locator('.arco-modal-footer button').all()))
    p.set_viewport_size({'width':390,'height':640})
    p.screenshot(path=str(h.out/'mobile-logout-notice.png'),full_page=True)
    with p.expect_download() as pending:
        d.get_by_role('button',name='导出本地草稿',exact=True).click()
    download=pending.value;path=h.out/'export-synthetic-notes.json';download.save_as(path);backup=json.loads(path.read_text())
    h.check('local_export_includes_closed_A_B',sorted((n['entryId'],n['note']) for n in backup['notes'])==[('101','未同步隐私草稿 101'),('102','未同步隐私草稿 102')])
    h.check('export_contains_no_auth_credentials','isolated-test-token' not in path.read_text() and all(name not in backup for name in ['token','username','password','readerNoteSession']))
    d.get_by_role('button',name='取消，保留会话',exact=True).click();expect(p.get_by_role('dialog')).to_have_count(0)
    h.check('cancel_keeps_account_and_drafts',all(p.evaluate('(k)=>!!sessionStorage.getItem(k)',key(1,eid)) for eid in [101,102]) and bool(p.evaluate("JSON.parse(localStorage.getItem('auth')).token")))
    p.set_viewport_size({'width':1440,'height':960});p.locator('.sidebar-profile-trigger').click();p.get_by_text(re.compile('^(退出登录|登出|退出)$')).last.click();dialog().get_by_role('button',name='丢弃本地草稿并退出',exact=True).click()
    p.wait_for_url('**/inbox/login');assert_purged(other);h.check('Profile_explicit_logout_confirms_discard')
    fresh_login();open_entry(101);expect(p.get_by_role('textbox',name='我的笔记',exact=True)).to_have_value('服务器 A')
    h.check('same_owner_relogin_does_not_recover_outgoing_drafts')
    failed_closed();other=seed_others();modes['note401']=True
    p.goto(h.base+'/inbox/all/entry/103',wait_until='domcontentloaded');p.wait_for_url('**/inbox/login');assert_purged(other);expect(p.get_by_role('dialog')).to_have_count(0);h.check('fresh_401_immediate_session_wide_cleanup')
    modes['note401']=False;fresh_login();failed_closed();other=seed_others()
    # Cached outgoing owner scope remains available before identity succeeds.
    modes['identity_error']=True;p.goto(h.base+'/inbox/',wait_until='domcontentloaded')
    logout=p.get_by_role('button',name=re.compile('^(退出登录|登出|退出)$'));expect(logout).to_be_visible();logout.click();dialog().get_by_role('button',name='取消，保留会话',exact=True).click()
    h.check('HomeRedirect_cancel_keeps_closed_drafts',bool(p.evaluate('(k)=>sessionStorage.getItem(k)',key(1,102))))
    logout.click();dialog().get_by_role('button',name='丢弃本地草稿并退出',exact=True).click();p.wait_for_url('**/inbox/login');assert_purged(other);h.check('HomeRedirect_explicit_logout_uses_cached_outgoing_scope')
    modes['identity_error']=False;fresh_login();failed_closed();other=seed_others()
    modes['version_error']=True;p.reload();logout=p.get_by_role('button',name=re.compile('^(退出登录|登出|退出)$'));expect(logout).to_be_visible();logout.click();dialog().get_by_role('button',name='丢弃本地草稿并退出',exact=True).click();p.wait_for_url('**/inbox/login');assert_purged(other);h.check('RouterProtect_explicit_logout_uses_same_scoped_cleanup')
    p.screenshot(path=str(h.out/'final-login.png'))
except Exception as exc:
    h.errors.append(str(exc));p.screenshot(path=str(h.out/'failure.png'),full_page=True);raise
finally:h.close()
