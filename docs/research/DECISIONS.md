# 个人 AI 资讯站：重新核验与选型（2026-09-22）

## 决策
采用 Miniflux 2.3.3 + ReactFlux 固定源码快照 + RSSHub + 独立 AI 增强服务。原 FreshRSS、n8n、微信工具箱保持不变。不安装 Docker，不修改 MCP 权限；使用 ubuntu 用户目录和 systemd --user 持久运行。

| 候选 | 本轮核验 | 结论 |
| --- | --- | --- |
| miniflux/v2 | Apache-2.0；官方发布 2.3.3；全文抓取、API、已读与收藏、PostgreSQL | 核心后端，保持上游二进制原样 |
| electh/ReactFlux | MIT；2024 年创建；官方 Miniflux 第三方应用目录收录；图文卡片与移动网页 | 主阅读器；仅增加 AI 信息展示、筛选和控制台 |
| Qetesh/miniflux-ai | README 声明 MIT；v0.9.5；process_entries.py 把 AI 文本前置回文章；缺独立评分查询 | 不作为生产 worker；参考接口思路，自建薄增强层，不复制其实现 |
| DIYgod/RSSHub | AGPL-3.0；完整可自部署；有社交平台适配器 | 接入层，固定快照；路由存在不等于账号已授权或实测成功 |
| RSS-Bridge/rss-bridge | Unlicense；Miniflux 有官方集成说明 | 不重复常驻；RSSHub 不覆盖时再启用 |
| karakeep-app/karakeep | AGPL-3.0；v0.33.2；RSS 每小时创建 bookmark；自动摘要和存档能力完善 | 非本次主 Inbox；收藏先用 Miniflux，避免双库双界面 |
| DevXDojo/MrRSS | 原 WCY-dt 仓库已转移；v1.3.37；官方 server 有网页但无登录；摘要 composable 从网页触发 | 保留候选，不把自动摘要按钮误认为已验证后台预筛选 |
| samuelclay/NewsBlur | MIT；完整阅读器；所查 AI classifier 明确调用 Anthropic，文本 excerpt 限 500 字符 | 不满足当前 Ark + 全文评价主链路，不选 |
| Tiendil/feeds.fun | BSD-3-Clause；评分规则完善；未核实原网页抓取先于模型的完整链路 | 不因标签/评分功能就认定满足原文要求，不选 |
| brandonhon/ember | 单 Go 服务 + SQLite + BYOK；2026-05 创建，最新 v0.9.7 | 太新，未替代经使用检验的核心 |
| umputun/newscope | 原生评分/理由/全文；尚无正式 release；read/unread 与主图链路不足 | 不作为长期数据底座 |

## 本轮纠正
ReactFlux 演示首先是前端登录入口，不保证免账号完整演示。图文/缩略图不等于模型理解了图片。RSSHub 不会消除 X/Instagram/Facebook 的登录、风控和限流。2GB RAM 不作淘汰理由，但依然控制任务并发，禁止用 Swap 冒充物理内存。

## 证据
[社区初筛](https://www.reddit.com/r/selfhosted/comments/1s68a02/new_rss_reader_needed/) · [Awesome Selfhosted](https://awesome-selfhosted.net/tags/feed-readers.html) · [Miniflux 官方 API](https://miniflux.app/docs/api.html) · [Miniflux 应用目录](https://miniflux.app/docs/apps.html) · [ReactFlux](https://github.com/electh/ReactFlux) · [MrRSS server](https://github.com/DevXDojo/MrRSS/blob/main/docs/SERVER_MODE/README.md) · [Karakeep RSS](https://docs.karakeep.app/integrations/rss-feeds/)
各仓库 metadata.json、tree.json、README.md 已保存于同目录子文件夹，包含本次访问时间、上游版本与 SHA；不得用 Star 替代功能验收。

## 验收目标
真实原文与配图、服务端 AI 结构化分析、跨浏览器已读/收藏、分数筛选、来源健康与授权状态、模型/提示词设置、工具入口、失败重试、预算限制、备份恢复、服务重启与真实截图。抓取失败必须显式呈现，不能拿 RSS 简介冒充全文结果。
