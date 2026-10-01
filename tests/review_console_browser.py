from review_reader_harness import Harness
from playwright.sync_api import expect
h=Harness('review-console');p=h.page
modes={'status_error':True,'save_error':False,'subscribe_error':True,'pause_subscribe':False,'pause_save':False};pending=[];pending_saves=[]
def intercept(route,path,method):
    if path.endswith('/ai/status') and modes['status_error']:
        route.fulfill(status=503,json={'detail':'isolated status failure'});return True
    if path.endswith('/ai/settings') and method=='PUT' and modes['save_error']:
        route.fulfill(status=503,json={'detail':'isolated save failure'});return True
    if path.endswith('/ai/settings') and method=='PUT' and modes['pause_save']:
        pending_saves.append(route);return True
    if path.endswith('/ai/subscribe'):
        url=route.request.post_data_json.get('url')
        if modes['pause_subscribe']:pending.append(route);return True
        if modes['subscribe_error'] and url.endswith('tech1.xml'):
            route.fulfill(status=502,json={'detail':'isolated source failure'});return True
    return False
h.custom=intercept
try:
    h.goto();h.panel();expect(p.get_by_label('模型 ID',exact=True)).to_have_value('fixture-model')
    expect(p.get_by_role('button',name='重试看板',exact=True)).to_be_visible();h.check('R01_partial_failure_preserves_settings')
    p.get_by_role('button',name='来源目录',exact=True).click();expect(p.locator('.ai-source-list>div')).to_have_count(24)
    p.get_by_role('button',name='显示更多来源',exact=True).click();expect(p.locator('.ai-source-list>div')).to_have_count(31);h.check('R02_catalog_pagination')
    p.get_by_label('目录分类').select_option('设计');expect(p.locator('.ai-source-list>div')).to_have_count(2)
    p.get_by_label('目录订阅状态').select_option('attention');expect(p.locator('.ai-source-list>div')).to_have_count(1);expect(p.locator('.ai-source-list>div').first).to_contain_text('错误来源');h.check('R02_combined_catalog_filters')
    p.get_by_role('button',name='重置目录筛选',exact=True).click();p.get_by_label('目录订阅状态').select_option('subscribed');expect(p.get_by_role('link',name='打开此订阅 →')).to_have_attribute('href','/inbox/feed/7');h.check('R02_direct_subscription_navigation')
    p.get_by_role('button',name='模型与偏好',exact=True).click();prompt=p.get_by_label('个人筛选提示词',exact=True);prompt.fill('新的草稿不应被看板刷新覆盖')
    p.get_by_role('button',name='资源看板',exact=True).click();modes['status_error']=False;p.get_by_role('button',name='重试看板',exact=True).click();expect(p.get_by_text('已收录文章',exact=True)).to_be_visible()
    p.get_by_role('button',name='刷新看板',exact=True).click();expect(p.locator('.review-sampled')).to_contain_text('上次成功读取')
    p.get_by_role('button',name='模型与偏好',exact=True).click();expect(prompt).to_have_value('新的草稿不应被看板刷新覆盖');h.check('R01_refresh_does_not_overwrite_dirty_settings')
    p.locator('.ai-dialog>header').get_by_role('button',name='关闭',exact=True).click();expect(p.get_by_label('确认关闭控制台')).to_be_visible();p.get_by_role('button',name='继续编辑',exact=True).click();expect(prompt).to_have_value('新的草稿不应被看板刷新覆盖');h.check('R04_cancel_close_keeps_draft')
    before=len([w for w in h.writes if w[1].endswith('/ai/settings')]);p.get_by_label('每日最多模型请求',exact=True).fill('');p.get_by_role('button',name='保存到服务器',exact=True).click();expect(p.get_by_text('有未填写或超出范围的设置，请核对。',exact=True)).to_be_visible();h.check('R04_invalid_settings_zero_puts',before==len([w for w in h.writes if w[1].endswith('/ai/settings')]))
    p.get_by_label('每日最多模型请求',exact=True).fill('80');modes['save_error']=True;p.get_by_role('button',name='保存到服务器',exact=True).click();expect(p.locator('.ai-message')).to_contain_text('操作未完成');expect(prompt).to_have_value('新的草稿不应被看板刷新覆盖');h.check('R04_failed_save_keeps_draft')
    modes['save_error']=False;p.get_by_role('button',name='保存到服务器',exact=True).click();expect(p.get_by_text('已保存到服务器',exact=True)).to_be_visible()
    last=[w for w in h.writes if w[1].endswith('/ai/settings')][-1][2];h.check('R04_only_changed_allowed_fields_saved',last=={'prompt':'新的草稿不应被看板刷新覆盖'})
    # A resource refresh must not advance the baseline underneath a dirty draft.
    model=p.get_by_label('模型 ID',exact=True);model.fill('local-model')
    h.settings['daily_articles']=81
    with p.expect_response(lambda r:r.url.endswith('/ai/settings') and r.request.method=='GET'):
        p.get_by_role('button',name='刷新设置',exact=True).click()
    expect(model).to_have_value('local-model');expect(p.get_by_label('每日最多模型请求',exact=True)).to_have_value('80')
    modes['pause_save']=True;p.get_by_role('button',name='保存到服务器',exact=True).click()
    expect(model).to_be_disabled();expect(p.get_by_label('个人筛选提示词',exact=True)).to_be_disabled();expect(p.get_by_role('button',name='刷新设置',exact=True)).to_be_disabled()
    h.check('R04_pending_save_protects_fields')
    p.locator('.ai-dialog>header').get_by_role('button',name='关闭',exact=True).click()
    expect(p.locator('.ai-message')).to_contain_text('正在保存，请等待结果')
    expect(p.locator('.ai-dialog')).to_be_visible();h.check('R04_pending_save_cannot_claim_discard')
    before_gets=len([c for c in h.calls if c==('GET','/mf/v1/ai/settings')])
    p.get_by_role('button',name='资源看板',exact=True).click()
    with p.expect_response(lambda r:r.url.endswith('/ai/status')):
        p.get_by_role('button',name='刷新看板',exact=True).click()
    h.check('R04_pending_save_prevents_stale_settings_read',before_gets==len([c for c in h.calls if c==('GET','/mf/v1/ai/settings')]))
    p.get_by_role('button',name='模型与偏好',exact=True).click()
    last=[w for w in h.writes if w[1].endswith('/ai/settings')][-1][2]
    h.check('R04_external_change_not_overwritten',last=={'model':'local-model'})
    modes['pause_save']=False
    for route in pending_saves:
        payload=route.request.post_data_json;h.settings.update(payload);route.fulfill(json=payload)
    pending_saves.clear()
    expect(p.get_by_text('已保存到服务器',exact=True)).to_be_visible();expect(model).to_be_enabled()
    expect(p.get_by_label('每日最多模型请求',exact=True)).to_have_value('81')
    h.check('R04_partial_save_response_preserves_fresh_server_fields',h.settings['daily_articles']==81)
    p.get_by_role('button',name='来源目录',exact=True).click();p.get_by_role('button',name='重置目录筛选',exact=True).click();p.get_by_label('搜索来源').fill('技术候选0')
    writes=len(h.writes);p.get_by_role('button',name='添加当前可用来源',exact=True).click();expect(p.get_by_label('订阅确认')).to_be_visible();h.check('R03_review_before_network_writes',len(h.writes)==writes)
    p.get_by_role('button',name='取消添加',exact=True).click();h.check('R03_cancel_creates_nothing',len(h.writes)==writes)
    p.get_by_role('button',name='添加当前可用来源',exact=True).click();p.get_by_role('button',name='确认添加来源',exact=True).click();expect(p.get_by_text('本轮结束：成功 9，已存在 0，失败 1，未发送 0。',exact=True)).to_be_visible();h.check('R03_partial_failure_distinguished')
    modes['subscribe_error']=False;p.get_by_role('button',name='仅重试失败来源',exact=True).click();expect(p.get_by_label('订阅确认')).to_contain_text('将添加 1 个来源');p.get_by_role('button',name='确认添加来源',exact=True).click();expect(p.get_by_text('本轮结束：成功 1，已存在 0，失败 0，未发送 0。',exact=True)).to_be_visible();h.check('R03_retry_failed_only')
    p.get_by_label('搜索来源').fill('技术候选1');p.get_by_role('button',name='添加当前可用来源',exact=True).click();modes['pause_subscribe']=True;p.get_by_role('button',name='确认添加来源',exact=True).click();p.get_by_role('button',name='停止剩余添加',exact=True).click()
    expect(p.get_by_text('将停止后续项，等待已发出的请求结束。',exact=True)).to_be_visible()
    modes['pause_subscribe']=False
    for route in pending:route.fulfill(json={'id':999})
    pending.clear();expect(p.get_by_text('本轮结束：成功 1，已存在 0，失败 0，未发送 9。',exact=True)).to_be_visible();h.check('R03_stop_remaining_does_not_submit_more')
    p.set_viewport_size({'width':390,'height':844});h.check('R01_R04_mobile_no_overflow',p.locator('.ai-dialog').evaluate('e=>e.scrollWidth<=e.clientWidth'))
    p.screenshot(path=str(h.out/'mobile-console.png'),full_page=True)
    p.locator('.ai-dialog>header').get_by_role('button',name='关闭',exact=True).click();expect(p.locator('.ai-dialog')).to_have_count(0);h.check('R01_focus_restored',p.get_by_role('button',name='AI 设置 · 来源',exact=True).evaluate('(e)=>e===document.activeElement'))
except Exception as exc:h.errors.append(str(exc));raise
finally:h.close()
