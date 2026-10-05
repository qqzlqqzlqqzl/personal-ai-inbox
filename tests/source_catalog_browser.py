"""Built-reader source provenance with synthetic cache-only responses; Refs #107.

This checks presentation, not live collection, project completeness or production
activation. The separately reviewed shared fixture owns the pinned sandbox.
"""
import inspect
import json
from pathlib import Path
import sys
from playwright.sync_api import expect
from review_reader_harness import Harness

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from vendor_catalog import annotate_vendor_sources

if 'sandboxed' not in inspect.signature(Harness.__init__).parameters:
    raise RuntimeError('Reviewed explicit sandboxed Harness support is required')

NOW = 1791158400
for width, height, theme in [(1440, 960, 'light'), (1440, 960, 'dark'), (390, 844, 'light'), (390, 844, 'dark')]:
    h = Harness(f'source-catalog/{width}x{height}-{theme}', sandboxed=True, viewport={'width': width, 'height': height})
    p = h.page
    url = 'http://127.0.0.1:8092/internal/vendor-feeds/kickstarter.xml'
    h.catalog = [{'name': 'Kickstarter 邮件归档（合成）', 'url': url, 'category': '产品与众筹',
                  'subscription_category': '产品灵感', 'status': 'ok', 'subscribed': True,
                  'feed_id': 71, 'analysis_supported': True, 'subscription_supported': True,
                  'note': '第三方邮件归档；邮件文字可读，项目详情与直达链接未取得'}]
    h.categories = [{'id': 1, 'title': '产品灵感'}, {'id': 3, 'title': '产品与众筹'}]
    h.feeds = [{'id': 71, 'user_id': 1, 'title': 'Kickstarter 邮件归档（合成）', 'feed_url': url,
                'site_url': 'https://example.test/archive', 'category': h.categories[0],
                'icon': {'feed_id': 71, 'icon_id': 0}}]
    rss = {'name': 'Kicktraq 科技众筹（合成）',
           'url': 'https://www.kicktraq.com/categories/technology/latest.rss',
           'category': '产品与众筹', 'status': 'ok', 'subscribed': False,
           'provider': 'Kicktraq', 'rss_summary_only': True, 'analysis_supported': False,
           'subscription_supported': True, 'summary_policy_ready': True,
           'checked_at': '2026-10-04T22:00:44Z', 'note': '仅 RSS 摘要；不代表项目全文或直接 Kickstarter 链接'}
    h.catalog.append(rss)
    annotate_vendor_sources(h.catalog, {'kickstarter': {'kind': 'newsletter'}}, {'kickstarter': {
        'seeded': True, 'last_success_at': NOW - 30000, 'last_attempt_at': NOW - 60,
        'last_error': {'type': 'HTTPStatusError', 'at': NOW - 60, 'http_status': 429}}}, now=NOW)
    p.add_init_script("localStorage.setItem('settings',JSON.stringify({themeMode:" + json.dumps(theme) + ",showStatus:'all'}))")
    try:
        h.goto('/inbox/all')
        expect(p.locator('body')).to_have_attribute('arco-theme', theme)
        h.panel()
        p.get_by_role('button', name='来源目录', exact=True).click()
        row = p.locator('.ai-source-list > div').filter(has=p.get_by_text('Kickstarter 邮件归档（合成）', exact=True))
        expect(row).to_have_count(1)
        for text in ('上游读取失败', '快照已过期', '非项目列表', '项目详情与直达链接缺失',
                     '产品与众筹', '当前订阅分类：产品灵感', '上次成功', '最近尝试'):
            expect(row).to_contain_text(text)
        h.check('cached_200_does_not_hide_upstream_failure', True)
        expect(row.locator('.ai-source-note')).to_have_text(h.catalog[0]['note'])
        expect(row.get_by_role('link', name='查看订阅地址 ↗', exact=True)).to_have_attribute('href', url)
        h.check('archive_link_not_presented_as_project_link', '项目链接 ↗' not in row.inner_text())
        h.check('no_horizontal_overflow', p.locator('.ai-dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth'))
        rss_row = p.locator('.ai-source-list > div').filter(has=p.get_by_text(rss['name'], exact=True))
        expect(rss_row).to_contain_text('未接入项目全文评分')
        expect(rss_row).to_contain_text('未提供 Kickstarter 直达链接')
        rss_row.get_by_role('button', name='添加摘要订阅', exact=True).click()
        expect(p.get_by_role('region', name='订阅确认')).to_contain_text(rss['name'])
        h.check('analysis_disabled_rss_subscription_requires_confirmation', not h.writes)
        p.get_by_role('button', name='取消添加', exact=True).click()
        p.screenshot(path=str(h.out / 'stale-archive.png'))
        p.get_by_role('button', name='关闭', exact=True).click()
        h.catalog[0].pop('note')
        h.catalog[0]['vendor_snapshot'].update(state_available=False, freshness='unknown', last_success_at=None,
                                              last_attempt_at=None, last_error=None)
        rss.update(url=rss['url'].replace('https:', 'http:', 1),
                   subscription_supported=False, summary_policy_ready=False)
        h.panel()
        p.get_by_role('button', name='来源目录', exact=True).click()
        expect(row).to_contain_text('上游缓存状态未知')
        expect(row.locator('.ai-source-note')).to_have_count(0)
        expect(row).to_contain_text('非项目列表')
        h.check('missing_note_and_state_are_honest', True)
        expect(rss_row.get_by_role('button', name='待摘要适配', exact=True)).to_be_disabled()
        p.get_by_role('textbox', name='自定义 RSS / RSSHub 地址', exact=True).fill(rss['url'])
        p.get_by_role('button', name='添加订阅', exact=True).click()
        expect(p.get_by_role('region', name='订阅确认')).to_have_count(0)
        h.check('unverified_http_identity_cannot_enter_confirmation', not h.writes)
        p.screenshot(path=str(h.out / 'unknown-archive.png'))
        h.check('no_refresh_or_api_write', not h.writes and not any('/refresh' in path for _, path in h.calls))
    except Exception as error:
        h.errors.append(str(error))
        p.screenshot(path=str(h.out / 'failure.png'))
        raise
    finally:
        (h.out / 'calls.json').write_text(json.dumps({'calls': h.calls, 'api_writes': len(h.writes),
            'synthetic': True, 'live_project_collection_verified': False}, ensure_ascii=False, indent=2))
        h.close()
