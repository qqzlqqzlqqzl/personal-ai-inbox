"""Generate delivery documents from passing machine-readable evidence; retain external failures."""
import json, subprocess, time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT=Path('/home/ubuntu/ai-news')
def load(name):return json.loads((ROOT/'artifacts'/name).read_text())
def main():
    live=load('live-acceptance.json');browser=load('browser-acceptance.json')
    public_browser=load('browser-acceptance-public.json')
    restore=load('restore-test.json');restart=load('restart-acceptance.json')
    audit=load('secret-audit.json');social=load('social-live.json')
    suites=list(ET.parse(ROOT/'artifacts/unit-tests.xml').getroot().iter('testsuite'))
    tests=sum(int(s.get('tests',0)) for s in suites)
    failures=sum(int(s.get('failures',0))+int(s.get('errors',0)) for s in suites)
    if failures or not all(r.get('passed') for r in [live,browser,public_browser,restore,restart]) or audit['findings']:
        raise RuntimeError('Core acceptance evidence is not all passing')
    stamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M UTC+8')
    commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    source_check=next(c['detail'] for c in live['checks'] if c['name']=='broad_real_sources')
    ai_check=next(c['detail'] for c in live['checks'] if c['name']=='real_ark_multiple_sources')
    items=[
      ('DEP-01','四服务与持久化重启','通过','restart-acceptance.json'),
      ('SEC-01','回环部署、鉴权与私有路径','通过','live-acceptance.json'),
      ('SEC-02','跨站写入、坏输入与凭据扫描','通过','unit-tests.xml'),
      ('SRC-01','真实来源、导入与 OPML 导出','通过','live-acceptance.json'),
      ('SRC-02','公开 Telegram 平台实际连接','未通过：出站网络','social-live.json'),
      ('ING-01','超过200条发现、幂等及来源公平调度','通过','unit-tests.xml'),
      ('TXT-01','真实原文、输入依据、配图','通过','live-acceptance.json'),
      ('TXT-02','抓取失败和论文摘要不冒充全文','通过','unit-tests.xml'),
      ('AI-01','真实 Ark 多来源分析、逐条证据校验','通过','live-acceptance.json'),
      ('AI-02','用量预算、错误输出与重试','通过','unit-tests.xml'),
      ('AI-03','推荐分/技术/商业/日期筛选','通过','live-acceptance.json'),
      ('UI-01','桌面登录、卡片、正文、原文、深链接','通过','browser-acceptance.json'),
      ('UI-02','390×844移动会话布局，无横向溢出','通过；非物理手机','browser-acceptance.json'),
      ('SYNC-01','独立会话的已读、收藏、AI及偏好','通过','browser-acceptance.json'),
      ('UI-03','控制台、来源目录、阅读状态','通过','browser-acceptance.json'),
      ('OPS-01','PG独立恢复与SQLite完整性','通过','restore-test.json'),
      ('OPS-02','独立运行时、暂存构建与旧资源保留','通过；当前实例','frontend-build.json'),
      ('GIT-01','源码/历史凭据扫描与私有仓库留档','扫描通过；最终SHA见提交历史','secret-audit.json'),
      ('ACCESS-01','SSH 私有通道','日常无需；仅维护/故障备用','private-access.json'),
      ('PUBLIC-01','公网 HTTPS 与专用登录','通过','browser-acceptance-public.json')]
    checklist='# 发布验收 Checklist\n\n更新时间：'+stamp+'。核心应用验收与外部连接条件分别列出，不把未连通的平台标为成功。\n\n| ID | 验收项 | 结果 | 证据 |\n|---|---|---|---|\n'
    for code,title,state,artifact in items:
        checklist+=f'| {code} | {title} | {state} | [{artifact}](../../artifacts/{artifact}) |\n'
    checklist+='\n## 复验原则\n\n- 自动化隔离测试、真实接口、真实浏览器、真实恢复分别记录，不互相代替。\n- 本次未重启整台服务器；只重启本项目四个服务。旧服务仍运行。\n- 未进行物理手机网络测试或全新VPS从零重装，不将配置说明等同于实测。\n- X/Instagram/Facebook未具备完整授权与验证；Telegram本次实测失败仍保留原样。\n- 基础安全回归和凭据扫描不等于第三方安全审计。\n'
    (ROOT/'docs/ops/ACCEPTANCE-CHECKLIST.md').write_text(checklist)
    report=f'''# 个人信息箱 · 交付与验收报告

记录时间：{stamp}。实现基线：`{commit}`；最后交付记录会作为后续文档提交保存。运行目录 `/home/ubuntu/ai-news`，私有仓库 `qqzlqqzlqqzl/personal-ai-inbox`。

**结论：核心收集、原文、AI、图文阅读、同步、恢复与公网 HTTPS 链路通过验收；外部社交平台仍有明确未通过项，不把未授权来源标成成功。**

## 实际验收快照

| 项目 | 结果 |
|---|---|
| 已导入来源 | {source_check['feeds']} |
| 已存条目 | {source_check['entries']}（含历史库存） |
| 已完成真实 AI 分析 | {ai_check['analyzed']}，来自 {ai_check['distinct_sources']} 个来源 |
| 实测模型 | {', '.join(live['model_names'])} |
| 自动化隔离回归 | {tests} 通过，{failures} 失败 |
| 真实接口与存量数据检查 | {len(live['checks'])} 项通过 |
| 独立桌面/移动浏览器 | 本机 {len(browser['checks'])} 项、公网 {len(public_browser['checks'])} 项通过；测试改动已恢复 |
| 四服务重启 | 通过；已读、收藏和内容保留，耗时 {restart['seconds']} 秒 |
| 备份恢复 | 独立数据库恢复通过，SQLite integrity_check=ok，生产库未覆盖 |
| 凭据检查 | {audit['files_scanned']} 个非忽略文件及所有可达 Git 历史检查，0 发现 |

数据会继续变化；以上是验收快照，不代表所有历史条目均已分析。其余条目仍可在“全部原始”浏览，后台按预算处理。

## 代码审查与验收中修正的问题

只查最新200条导致漏处理；失败来源抢占队列；AI过滤遗漏日期条件；SQLite连接未明确关闭；失败模型响应的实际用量记录不完整；默认Token/英文登录；元数据论文可能被当成全文；PWA导航回退范围过宽；更新清空旧资源会影响旧标签页的风险；旧站Node运行时耦合。现在分别有代码修正与回归证据，阅读器本体仍使用成熟上游。

## 交付入口

日常直接打开 `https://106.53.40.6/inbox/`，用户名 `qqzl`。服务器地址固定为当前站点 `/mf`，登录页不再显示服务器选择或 Token 登录；密码仅在服务器 `.private/miniflux.env`，不在 GitHub。SSH 隧道仅作为维护备用。详见 [访问说明](LOCAL_ACCESS.md)。

## 没有伪装成通过的条件

- **社交平台**：Telegram路由HTTP {social.get('http','无响应')}，服务器到t.me/telegram.me的出站请求失败；X/Instagram等还需授权，Facebook未验证。服务在线与平台连通分开记录。
- **内容范围**：arXiv元数据源被阻止作为论文全文评分；没有PDF全文、视频理解、评论或完整讨论串承诺。源站反爬或抽取失败明确保留错误，不生成假成功。
- **备份范围**：每日私有快照保留14份，但在同一台服务器；GitHub只保存源码、文档和测试证据，不是文章数据库异地备份。
- **费用范围**：默认80次模型请求/日及500,000Token/日仅限制本项目，不限制保持不动的旧n8n或其他应用。

## 证据与维护

[完整 Checklist](ACCEPTANCE-CHECKLIST.md) · [操作 SOP](SOP.md) · [接手/重建](HANDOFF.md) · [自动回归](../../artifacts/unit-tests.xml) · [真实接口](../../artifacts/live-acceptance.json) · [浏览器](../../artifacts/browser-acceptance.json) · [重启](../../artifacts/restart-acceptance.json) · [恢复](../../artifacts/restore-test.json)
'''
    (ROOT/'docs/ops/DELIVERY.md').write_text(report)
    print(json.dumps({'core_passed':True,'unit_tests':tests,'live_checks':len(live['checks']),'browser_checks':len(browser['checks']),'public_browser_checks':len(public_browser['checks']),'external_conditions':['social network and authorization']},ensure_ascii=False))

if __name__=='__main__':main()
