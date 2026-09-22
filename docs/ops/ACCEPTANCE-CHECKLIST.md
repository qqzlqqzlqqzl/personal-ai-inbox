# 发布验收 Checklist

更新时间：2026-09-22 17:26 UTC+8。核心应用验收与外部连接条件分别列出，不把未连通的平台标为成功。

| ID | 验收项 | 结果 | 证据 |
|---|---|---|---|
| DEP-01 | 四服务与持久化重启 | 通过 | [restart-acceptance.json](../../artifacts/restart-acceptance.json) |
| SEC-01 | 回环部署、鉴权与私有路径 | 通过 | [live-acceptance.json](../../artifacts/live-acceptance.json) |
| SEC-02 | 跨站写入、坏输入与凭据扫描 | 通过 | [unit-tests.xml](../../artifacts/unit-tests.xml) |
| SRC-01 | 真实来源、导入与 OPML 导出 | 通过 | [live-acceptance.json](../../artifacts/live-acceptance.json) |
| SRC-02 | 公开 Telegram 平台实际连接 | 未通过：出站网络 | [social-live.json](../../artifacts/social-live.json) |
| ING-01 | 超过200条发现、幂等及来源公平调度 | 通过 | [unit-tests.xml](../../artifacts/unit-tests.xml) |
| TXT-01 | 真实原文、输入依据、配图 | 通过 | [live-acceptance.json](../../artifacts/live-acceptance.json) |
| TXT-02 | 抓取失败和论文摘要不冒充全文 | 通过 | [unit-tests.xml](../../artifacts/unit-tests.xml) |
| AI-01 | 真实 Ark 多来源分析、逐条证据校验 | 通过 | [live-acceptance.json](../../artifacts/live-acceptance.json) |
| AI-02 | 用量预算、错误输出与重试 | 通过 | [unit-tests.xml](../../artifacts/unit-tests.xml) |
| AI-03 | 推荐分/技术/商业/日期筛选 | 通过 | [live-acceptance.json](../../artifacts/live-acceptance.json) |
| UI-01 | 桌面登录、卡片、正文、原文、深链接 | 通过 | [browser-acceptance.json](../../artifacts/browser-acceptance.json) |
| UI-02 | 390×844移动会话布局，无横向溢出 | 通过；非物理手机 | [browser-acceptance.json](../../artifacts/browser-acceptance.json) |
| SYNC-01 | 独立会话的已读、收藏、AI及偏好 | 通过 | [browser-acceptance.json](../../artifacts/browser-acceptance.json) |
| UI-03 | 控制台、来源目录、服务器工具入口 | 通过 | [browser-acceptance.json](../../artifacts/browser-acceptance.json) |
| OPS-01 | PG独立恢复与SQLite完整性 | 通过 | [restore-test.json](../../artifacts/restore-test.json) |
| OPS-02 | 独立运行时、暂存构建与旧资源保留 | 通过；当前实例 | [frontend-build.json](../../artifacts/frontend-build.json) |
| GIT-01 | 源码/历史凭据扫描与私有仓库留档 | 扫描通过；最终SHA见提交历史 | [secret-audit.json](../../artifacts/secret-audit.json) |
| ACCESS-01 | Windows浏览器私有通道 | 未建立：本机8092拒绝连接 | [private-access.json](../../artifacts/private-access.json) |

## 复验原则

- 自动化隔离测试、真实接口、真实浏览器、真实恢复分别记录，不互相代替。
- 本次未重启整台服务器；只重启本项目四个服务。旧服务仍运行。
- 未进行物理手机网络测试或全新VPS从零重装，不将配置说明等同于实测。
- X/Instagram/Facebook未具备完整授权与验证；Telegram本次实测失败仍保留原样。
- 基础安全回归和凭据扫描不等于第三方安全审计。
