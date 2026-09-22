# 本轮调试与验收

记录日期：2026-09-22。本文记录本轮已验证结果与未闭环事项，后续运行会改变性能、队列与回填数据；复核时以对应机器报告的时间戳为准。

## 验证结果

| 项目 | 本轮证据与结论 |
| --- | --- |
| 登录 | qqzl 真实账号经用户 Windows 直连 HTTPS 与浏览器登录验证；/v1/me 返回 200，bcrypt cost 为 10。密码不写入文档或验收产物。[登录报告](../../artifacts/login-acceptance.json) |
| Python / JS | Python 73 项通过、无失败；JS 分页测试通过。Python 依据 [JUnit](../../artifacts/unit-tests.xml)，JS 脚本为 tests/test_ai_pagination.mjs。 |
| 公网常规浏览器 | 32 项通过，已恢复临时状态；这是生产服务器 Chromium 经公网 HTTPS，不是用户 Windows 浏览器或物理手机。[报告](../../artifacts/browser-acceptance-public.json) |
| 扩展浏览器 | 14 项通过：24 条初始卡片、延迟全文、深链、完整分页、gzip、封面字段及 X 失败界面。50 条分页 offset 为 0/24/48，无重复、跳项并正常停止；测试状态已恢复。[报告](../../artifacts/browser-performance-public.json) |
| Airing | 数据库实测保持 152 篇且 152 篇有封面元数据（[状态报告](../../artifacts/final-state-acceptance.json)）；两篇目标卡片分别选择 cover.webp 与 2026-03-31.jpg，实际加载宽度为 2000 与 1280，未改已读状态。[封面浏览器报告](../../artifacts/browser-cover-public.json) |
| 预算 | 保持每天 80 次模型请求、500,000 Token，UTC 日界，没有为验收提高预算。[诊断快照](../../artifacts/diagnostics-acceptance.json) |

正文测试曾误用只有 7 个可见字符的短帖 2643，使“正文大于 100 字符”断言超时。现选用真实长文 2345，API HTML 33,970 字符、页面正文 15,626 字符，并核对正文文本片段；未降低断言来掩盖全文缺失。

## 性能：不同测点分别看

历史交接数据不是本轮重测：AI 12 条列表约 579,823 字节、1.13–1.33 秒，React JS 未压缩 310,633 字节，浏览器约 4–5 秒。这些值只作为历史描述，不能直接算成本轮修复的严格前后收益。

接手时已有进行中的轻量列表和 gzip 修改：服务器经公网的 AI 12 / 24 条中位数分别为 363.9 / 581.8 ms。最终同测点分别为 239.0 / 427.1 ms；24 条 Basic 认证为 458.1 ms。两阶段的文章集合、负载和缓存会变，报告保留原始多次采样。[历史、WIP 与最终服务器数据](../../artifacts/performance-acceptance.json)

重复 Basic 校验是独立验证的慢点：同 bcrypt cost 10、隔离 ASGI 网关连接真实 Miniflux 的对照中，重复 Basic 中位数 1629.2 ms，同 UID token 复用为 406.3 ms。生产实现先验证用户身份，再核对内部 token 的 UID，匹配才用于本次内部列表请求；不是绕过登录或降低密码强度。[认证对照](../../artifacts/auth-performance-comparison.json)

用户 Windows 直连公网 HTTPS、无代理，每项三次采样：

| 请求 | 中位耗时 | 解压后响应体 | gzip 响应体 |
| --- | ---: | ---: | ---: |
| /v1/me | 170.4 ms | 865 B | 约 450 B |
| AI 12 条 | 426.3 ms | 39,148 B | 10,809 B |
| AI 24 条 | 557.1 ms | 78,547 B | 20,525 B |
| 原始未读 20 条 | 225.9 ms | 195,819 B | 64,395 B |

这才包含用户电脑到服务器的网络路径；仍是 HTTP 请求测量，不是 Windows 浏览器完整渲染耗时。服务器 Chromium 的最终首批卡片为 2131.6 ms，也不能冒充用户电脑首屏。[Windows 原始报告](../../artifacts/windows-public-performance.json)

## 封面回填与 QA

本轮已处理 2619 条原先无封面的记录。第一轮新增 752 张、无图 190 条、原网页抓取失败 1677 条，耗时 1366.63 秒；随后只读取已有正文和附件，17.09 秒内补回 237 张。合计新增 989 张、确认未找到候选 190 条，仍有 1440 条原网页不可用且无本地候选，保守记为 failed，不能断言原网页没有封面。两阶段 AI 请求均为 0，合计 1383.72 秒。[汇总](../../artifacts/cover-backfill.json)、[原网页阶段](../../artifacts/cover-backfill-original.json)、[正文补救阶段](../../artifacts/cover-backfill-recovery.json)。补救报告的 no_cover 仅指现有正文/附件无候选，不能消除原网页阶段的失败。

初次封面截图和 [初次 QA](../../artifacts/cover-qa-initial.json) 属于中间证据；[筛选器修正后 QA](../../artifacts/cover-qa.json) 才是本轮筛选规则的复核结果，覆盖 20 个来源、60 篇样本，其中 54 页成功取得。Rust 三篇已不再误选通用 rust-social-wide.jpg；少数派两篇 hero URL 移除了 format/webp 转换参数。该 QA 不是全库回填后的抽样，不将候选选择成功等同图片处处可加载。两篇 Airing 的实际浏览器加载验证只覆盖这两篇；收尾只更新回填完成与剩余失败统计，不为凑结论重复抓取全部外站。

## 日志与 X 的实测边界

请求日志已有 request ID、应用耗时和响应长度；诊断输出服务、心跳、预算、订阅错误与有界慢请求样本。真实 journal 捕获 analysis_reused 和 analysis_failed。受当日请求预算耗尽影响，本轮没有新增成功模型调用验收，不把旧成功结果或复用当新调用。复用样本 2744 → 2745 的模型调用增量为 0，测试记录已恢复。[日志证据](../../artifacts/logging-acceptance.json)、[复用验收](../../artifacts/worker-logging-acceptance.json)、[排查入口](LOGGING.md)

X 仍不能声称可用：直连 ConnectTimeout、本机 RSSHub 503 且未配置、第三方候选未认证 403，没有验证取得帖子。已验证的是预检结果如实展示、无帖子时禁用专用订阅；无需个人 X Token 的第三方路线仍需协议适配和服务商凭据，没有稳定方案部署成功。[调查](../research/X-NO-TOKEN.md)

旧 /news/、/api/ 与 /inbox/ PWA scope 边界保持；最新状态复核显示 FreshRSS 返回 302，8092 仅监听 127.0.0.1（[状态报告](../../artifacts/final-state-acceptance.json)）。本轮没有以新应用替换旧系统。具体重建、测试动作与运行限制见 [HANDOFF](HANDOFF.md)。

最后补修 gzip;q=0 协商与回填 retry 在 LIMIT 之前筛选的边界，项目 tests/ 共 73 项通过；生产重启后静态/动态三种编码组合共 6 项 HTTP 验证通过（[报告](../../artifacts/gzip-negotiation-acceptance.json)）。直接从仓库根无范围运行 pytest 曾误收集 upstream/miniflux-ai 的独立测试，因其 services/yaml 依赖未安装而收集失败；本项目验收范围为 tests/。
