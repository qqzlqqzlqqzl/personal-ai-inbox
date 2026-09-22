# 个人信息箱 · 交付与验收报告

记录时间：2026-09-22 17:26 UTC+8。实现基线：`226fe18addeba675a9e505986149fb20faa2f60c`；最后交付记录会作为后续文档提交保存。运行目录 `/home/ubuntu/ai-news`，私有仓库 `qqzlqqzlqqzl/personal-ai-inbox`。

**结论：Linux 上的核心收集、原文、AI、图文阅读、同步与恢复链路通过验收。外部平台连接及 Windows 浏览器私有通道仍有明确未通过项，不是“全部外部来源都可用”。**

## 实际验收快照

| 项目 | 结果 |
|---|---|
| 已导入来源 | 56 |
| 已存条目 | 2867（含历史库存） |
| 已完成真实 AI 分析 | 76，来自 48 个来源 |
| 实测模型 | deepseek-v4-flash-ga-260731 |
| 自动化隔离回归 | 40 通过，0 失败 |
| 真实接口与存量数据检查 | 23 项通过 |
| 独立桌面/移动浏览器 | 21 项通过；测试改动已恢复 |
| 四服务重启 | 通过；已读、收藏和内容保留，耗时 7.07 秒 |
| 备份恢复 | 独立数据库恢复通过，SQLite integrity_check=ok，生产库未覆盖 |
| 凭据检查 | 119 个非忽略文件及所有可达 Git 历史检查，0 发现 |

数据会继续变化；以上是验收快照，不代表所有历史条目均已分析。其余条目仍可在“全部原始”浏览，后台按预算处理。

## 代码审查与验收中修正的问题

只查最新200条导致漏处理；失败来源抢占队列；AI过滤遗漏日期条件；SQLite连接未明确关闭；失败模型响应的实际用量记录不完整；默认Token/英文登录；元数据论文可能被当成全文；PWA导航回退范围过宽；更新清空旧资源会影响旧标签页的风险；旧站Node运行时耦合。现在分别有代码修正与回归证据，阅读器本体仍使用成熟上游。

## 交付入口

建立私有转发后打开 `http://127.0.0.1:8092`，用户名 `reader`。密码仅在服务器 `.private/miniflux.env`，不在 GitHub。详见 [私有访问说明](LOCAL_ACCESS.md)。主界面、正文及手机布局的实际截图在 [screenshots](../../artifacts/screenshots)。

## 没有伪装成通过的条件

- **Windows访问**：本机8092返回ConnectionRefused，私有隧道尚未建立；自动使用SSH凭据的调用被拦截，因此没有改走旁路读取秘密。这不影响已经验证的Linux应用，但不能说你现在直接点击Windows的localhost就能打开。
- **社交平台**：Telegram路由HTTP 503，服务器到t.me/telegram.me的出站请求失败；X/Instagram等还需授权，Facebook未验证。服务在线与平台连通分开记录。
- **内容范围**：arXiv元数据源被阻止作为论文全文评分；没有PDF全文、视频理解、评论或完整讨论串承诺。源站反爬或抽取失败明确保留错误，不生成假成功。
- **备份范围**：每日私有快照保留14份，但在同一台服务器；GitHub只保存源码、文档和测试证据，不是文章数据库异地备份。
- **费用范围**：默认80次模型请求/日及500,000Token/日仅限制本项目，不限制保持不动的旧n8n或其他应用。

## 证据与维护

[完整 Checklist](ACCEPTANCE-CHECKLIST.md) · [操作 SOP](SOP.md) · [接手/重建](HANDOFF.md) · [自动回归](../../artifacts/unit-tests.xml) · [真实接口](../../artifacts/live-acceptance.json) · [浏览器](../../artifacts/browser-acceptance.json) · [重启](../../artifacts/restart-acceptance.json) · [恢复](../../artifacts/restore-test.json)
