# 文档索引

## 现行文档（先看这些）

| 文档 | 内容 |
|---|---|
| [../README.md](../README.md) | 项目介绍、使用方式、系统组成、仓库结构 |
| [ops/HANDOFF.md](ops/HANDOFF.md) | **接手说明**：目录地图、服务与端口、改动前须知、发布流程、私有配置位置、排查 |
| [ops/SOP.md](ops/SOP.md) | 日常运维命令：状态、重启、条目状态、备份与恢复 |
| [ops/PROXY.md](ops/PROXY.md) | 服务器出站代理（mihomo）的配置与节点规则 |
| [ops/LOCAL_ACCESS.md](ops/LOCAL_ACCESS.md) | HTTPS 入口与维护用的 SSH 隧道 |
| [ops/LOGGING.md](ops/LOGGING.md) | 请求日志、后台日志的字段说明与只读诊断 |
| [ops/BILINGUAL-READER.md](ops/BILINGUAL-READER.md) | 全文双语翻译的策略、预算与缓存 |
| [ops/READING-TOOLS.md](ops/READING-TOOLS.md) | 文章页的翻译、排版与专注模式 |
| [ops/x-guest-provider.md](ops/x-guest-provider.md) | X 访客抓取服务 |
| [../src/kaggle_batch/RUNBOOK.md](../src/kaggle_batch/RUNBOOK.md) | Kaggle Qwen 批处理运行手册 |
| [../ops/miniflux-metadata/README.md](../ops/miniflux-metadata/README.md) | Miniflux 批量元数据接口补丁 |
| [ops/DOT-URL-EXPORT.md](ops/DOT-URL-EXPORT.md) | 只读 URL 导出清单的格式约定 |
| [ops/PRODUCT-ACCEPTANCE.md](ops/PRODUCT-ACCEPTANCE.md) | 产品验收矩阵与发布规则 |
| [dev-fixture-workspace.md](dev-fixture-workspace.md) | 前端开发用的受控测试数据工作区 |

## 历史记录

下面是某次改动或验收时写的记录，原样保留作为背景。**里面的数字、路径和限制可能已经过时**；与代码或现行文档冲突时，以代码和现行文档为准。

### 交接与交付

| 文档 | 主题 |
|---|---|
| [ops/history/HANDOFF-2026-09-to-10.md](ops/history/HANDOFF-2026-09-to-10.md) | 2026-09 至 10 月按时间追加的旧交接日志 |
| [ops/DELIVERY.md](ops/DELIVERY.md)、[ops/ACCEPTANCE-CHECKLIST.md](ops/ACCEPTANCE-CHECKLIST.md) | 首次交付时由 `src/release_report.py` 生成的验收报告 |
| [ops/TAKEOVER-20260922.md](ops/TAKEOVER-20260922.md) | 网页接手记录（2026-09-22） |
| [ops/ACCESS-BLOCKERS.md](ops/ACCESS-BLOCKERS.md) | 早期访问与初始化阻塞 |
| [ops/review-20260927/REPORT.md](ops/review-20260927/REPORT.md) | 服务器审查与修复报告（2026-09-27） |
| [ops/DEBUGGING-ACCEPTANCE.md](ops/DEBUGGING-ACCEPTANCE.md) | 一轮调试与验收 |
| [ops/ACCEPTANCE-20260930.md](ops/ACCEPTANCE-20260930.md)、[ops/PRODUCTION-INTEGRATION-20260930.md](ops/PRODUCTION-INTEGRATION-20260930.md)、[ops/RECOVERY-REVIEW-20260930.md](ops/RECOVERY-REVIEW-20260930.md) | 2026-09-30 的验收、恢复与集成 |
| [ops/FEEDBACK-20261026.md](ops/FEEDBACK-20261026.md) | 2026-10-26 实际阅读反馈修复 |

### 来源与 n8n 退役

| 文档 | 主题 |
|---|---|
| [ops/N8N-SOURCE-MIGRATION.md](ops/N8N-SOURCE-MIGRATION.md)、[ops/N8N-SCHEDULER-MIGRATION.md](ops/N8N-SCHEDULER-MIGRATION.md)、[ops/N8N-RETIREMENT-ACCEPTANCE-2026-10-01.md](ops/N8N-RETIREMENT-ACCEPTANCE-2026-10-01.md) | 旧 n8n 的来源与调度迁入本项目，n8n 退役 |
| [ops/ADAFRUIT-ORIGINAL-ACCEPTANCE.md](ops/ADAFRUIT-ORIGINAL-ACCEPTANCE.md) | Adafruit 原作者提取 |
| [ops/FEED-HISTORY-ACCEPTANCE.md](ops/FEED-HISTORY-ACCEPTANCE.md) | 来源历史范围 |
| [ops/ARTICLE-COMPLETENESS.md](ops/ARTICLE-COMPLETENESS.md) | 正文完整性 |
| [READER_CONTENT_QUALITY.md](READER_CONTENT_QUALITY.md) | 处理原因与内容资格 |

### Kaggle 批处理

| 文档 | 主题 |
|---|---|
| [ops/kaggle/ACCEPTANCE.md](ops/kaggle/ACCEPTANCE.md) | 首次交付验收（2026-09-24） |
| [KAGGLE_FIFTH_LANE.md](KAGGLE_FIFTH_LANE.md) | 增加第五个账号 |
| [ops/KAGGLE-RESERVE-ACCEPTANCE.md](ops/KAGGLE-RESERVE-ACCEPTANCE.md) | 一小时保留时间 |
| [ops/kaggle/required-ledger-review.md](ops/kaggle/required-ledger-review.md)、[ops/kaggle/scheduler-stop-review.md](ops/kaggle/scheduler-stop-review.md) | 调度账本与停止准入复查 |
| [kaggle-cli-parser-closeout-20261004.md](kaggle-cli-parser-closeout-20261004.md) | Kaggle CLI 解析收尾 |

### 阅读器前端

| 文档 | 主题 |
|---|---|
| [ops/UI-REVIEW-RELEASE.md](ops/UI-REVIEW-RELEASE.md) | 卡片、排序、原站入口 |
| [ops/SCROLL-RELEASE.md](ops/SCROLL-RELEASE.md) | 连续阅读与预加载 |
| [ops/MOBILE-READING-SPACE-20261002.md](ops/MOBILE-READING-SPACE-20261002.md) | 手机阅读空间 |
| [ops/OVERLAY-UPGRADE-20261002.md](ops/OVERLAY-UPGRADE-20261002.md) | 前端补丁升级准入 |
| [ops/READER_STARTUP_CANDIDATE_20261005.md](ops/READER_STARTUP_CANDIDATE_20261005.md) | 首屏代码加载 |
| [ops/READER_IMAGE_CACHE_20261006.md](ops/READER_IMAGE_CACHE_20261006.md) | 图片缓存与预热 |
| [ops/READER-CACHE-LOCKING-73.md](ops/READER-CACHE-LOCKING-73.md)、[ops/NOTES-METADATA-73.md](ops/NOTES-METADATA-73.md)、[ops/NOTES-PAIR-STAGING-73.md](ops/NOTES-PAIR-STAGING-73.md) | 笔记与卡片缓存（Issue #73） |
| [ops/TRANSLATION-RECOVERY.md](ops/TRANSLATION-RECOVERY.md) | 未完成翻译的续跑 |
| [ops/HTTPS-HTTP2.md](ops/HTTPS-HTTP2.md) | HTTPS 启用 HTTP/2 |
| [READER_QUERY_READING_UX.md](READER_QUERY_READING_UX.md) | 查询、阅读控制与主题 |
| [reader-account-calendar.md](reader-account-calendar.md) | 账户日历（Issue #85） |
| [agent-status.md](agent-status.md) | Agent 状态页草案 |

## 调研

`research/` 下是选型和性能调研：[DECISIONS.md](research/DECISIONS.md)（2026-09-22 的选型结论）、[X-NO-TOKEN.md](research/X-NO-TOKEN.md)（X 免 Token 接入的结论），各上游项目的快照说明（`research/<作者>__<项目>/`），以及 `reader-*` 开头的加载性能实验记录。
