> 当前仓库可见性：2026-10-06 经用户授权改为公开，以使用标准 GitHub-hosted runner 的免费 CI。下文的“私有仓库”是交接时的历史状态；生产凭据、数据库和私有运行材料仍不进入 Git。

# 接手与重建说明

## 代码边界

`src/api.py` 是同源网关与 AI 扩展接口，不是新的阅读器；原生 Miniflux API 仍负责内容、订阅、已读和收藏。`src/worker.py` 负责完整分页发现、按来源公平排队、原文提取、模型调用、证据校验与失败重试；`src/core.py` 负责独立 SQLite 元数据与预算。

`src/content_input.py` 区分原网页与本实例可信社交适配器原帖，并可从原网页声明的元数据补取真实封面。没有视频理解、论文 PDF 正文管道或通用跨站全文保证。重复复用只针对相同正文及相同模型/提示词配置，不是语义事件聚合。

ReactFlux 的覆盖源在 `patches/`。`src/patch_frontend.py` 应用初始补丁，`src/polish_frontend.py` 修复中文登录、标识、PWA 回退和控制台细节；`src/build_frontend.py` 统一完成补丁、离线构建及分阶段发布。上游源码保留于 `upstream/reactflux`，不提交其依赖或构建目录。

干净重建时，`patch_scope_ai_filters.py` 现在会把前一阶段实际生成的分数方向按钮规范化为统一排序选择器，也兼容已存在的旧排序选择器；不再依赖未提交的中间补丁。`src/build_frontend.py` 在直接调用 Vite 前执行上游标准 `node src/scripts/version-info.js`，避免首次构建缺少版本信息。`tests/test_frontend_overlay_rebuild.py` 在临时目录从精确固定的 Git commit 应用完整覆盖两次并比较字节一致性；`tests/test_frontend_prebuild.py` 验证 Node、工作目录及失败中止行为。

## 性能与封面维护

AI 精选首批 24 条，列表返回轻量卡片并标记 content_deferred，点击与深链刷新通过单条接口取得全文。前端分页按服务端原始返回条数推进 offset，展示去重不改变游标，避免重复条目导致跳项或无法停止；末尾以真实分页结果收束。

本轮实测的慢点是浏览器 Basic 认证被 AI 列表扇出的多次 Miniflux 请求重复执行密码校验。src/api.py 的 list_upstream_headers 先保留用户认证与 UID，再检查内部 worker token 的 /v1/me，只有 UID 完全相同才在本次内部列表请求复用 token；缺少 token、身份不匹配或验证失败时仍用用户凭据。不是取消登录、跨账号代查或降低密码哈希成本；token 不下发给浏览器。

封面由 src/content_input.py 优先选文章 hero，再选站点声明的 OG/Twitter 图片，最后只考虑文章/main 范围内候选；排除 header/nav/footer/aside，避免把全站熊猫横幅当文章封面。候选还要实际请求并检查 200 与 image/ 类型；hero 被 403 等拒绝时尝试声明的社交封面。选中正确 URL 与浏览器能加载图片是两层验收。

src/backfill_covers.py 可为历史条目补封面元数据，不调用 AI、不重算评分。进展与失败以当时生成的报告和日志为准，不能把启动回填当作已全部完成。

## 来源历史范围

来源目录会同时列出候选来源和阅读器中的手动订阅。展开已订阅来源的“历史范围”才发起查询，不会在打开控制台时批量抓取所有 feed。

- `GET /mf/v1/ai/feeds/{id}/history` 仅管理员可用，先由 Miniflux 验证当前用户的来源访问权限。存储条数与最旧/最新 `published_at` 来自 Miniflux 的升/降序各一条查询，不使用不完整的 AI 分析表；包含仍保留的 `read`、`unread`、`removed` 条目。
- RSS/Atom/RDF 的“本次暴露”是单次 feed 文档的条数和有日期条目范围，与已存储范围分开显示。缺失/无效日期保持未知；Atom 缺少有效 `published` 而使用 `updated` 时明确提示。时间统一为 UTC，检查时间可见，快照最多缓存 5 分钟。
- 独立 feed 检查不转发阅读器登录信息，也不使用来源密码或 Cookie；这些来源显示暴露历史未知，不影响存储范围。公网探测直连并固定经过验证的公开 DNS 地址（含每次重定向），保留原 Host/TLS 校验；不复用出站代理，直连不可达时显示未知。本机专用 RSSHub/X 适配器单独允许。检查限制为 2 MiB、最多 3 次重定向、总计 15 秒，失败和空 feed 分开处理。
- 这里没有按统一截止日回溯历史，也没有执行 archive/API/sitemap 回补。RSS 暴露范围不能证明站点历史完整；需要历史回补时应另做站点适配。

隔离回归：`PYTHONPATH=src pytest -q tests/test_feed_history.py tests/test_api.py`、`node --test tests/test_source_history.mjs`。已安装上游依赖及隔离 `jsdom` 后，可运行 `node --test tests/source_history_component_acceptance.mjs` 验证展开、重复点击、失败重试及关闭后旧响应隔离。`tests/history_browser_acceptance.py` 只接受本地构建目录，所有 API 和外网请求均拦截为测试数据，可用 `AI_NEWS_TEST_BUILD=runtime/history-build` 指定待验收构建；不会连接生产服务。

## 日志与 X 来源边界

从 [LOGGING.md](LOGGING.md) 的只读诊断入口检查服务、心跳、预算、feed 错误、近期事件与慢请求；不要转储凭据或原文。analysis_reused 只是复用既有结果，不证明一次新模型调用成功。

src/x_source.py 的预检分开报告适配器配置、直连网络与 RSS 实际帖子；专用 x_handle 订阅再次预检，无帖子返回 409。当前直连超时、未配置 RSSHub 503、第三方未认证 403 均不算取得帖子，也未部署稳定的无个人 X Token 方案。第三方不需要个人 X 登录 Token 仍可能需要服务商 API Key；RSSHub thirdPartyApi 需要 GraphQL 协议兼容，不能直接填任意 REST 根地址。详见 [X-NO-TOKEN.md](../research/X-NO-TOKEN.md)。

## 公网入口与构建边界

日常入口 `https://106.53.40.6/inbox/`，无需隧道。`src/build_frontend.py` 固定 `VITE_BASE_PATH=/inbox/`，publish 验证时剥掉此 URL 前缀；资源实际仍在 `build/assets/`。Login 使用 `import.meta.env.BASE_URL`。不要改回根路径 PWA；`/mf/` 始终是根路径 API。

Nginx 配置副本及备份位置见 DELIVERY。仅复用现有 HTTPS server，8092 保持回环。重建后必须检查 Service Worker scope、使用 Inbox 后的 FreshRSS 和现有 API。构建暂存目录保留用于回溯，不自动删除；按维护者的回收策略处理。

## 运行与配置

云服务器 mihomo 已接入 web、RSSHub 与 Miniflux，业务与订阅刷新使用不同 fallback 组；候选节点按固定名称限制为已验证的凌云台湾 06/07 与 Yahaha 节点。私密边界、实测与待补证项见 [PROXY.md](PROXY.md)。X 网络已可达，但授权与帖子内容仍未接通。

- 生产 Python 依赖：`requirements.lock.txt`；测试依赖：`requirements.dev.lock.txt`。
- Node 已复制为独立 `runtime/node`，生产 RSSHub 不再依赖旧 `personal-news` 的 Node 路径。版本与二进制 hash 在 `upstream.lock.json`。
- PostgreSQL 是 Ubuntu 包解压后的独立实例，目录 `runtime/pg`，数据 `state/postgres`，端口 55432；不要用系统 PostgreSQL 覆盖它。
- 私有配置通过 user systemd 的 `EnvironmentFile` 注入，不由浏览器输入或读取。不要把 `.private/access.json` 当成正式登录信息。
- 字体、Chrome 和额外图形库只用于截图测试，没有复制进 Git，也不属于服务端必需依赖。

## 干净服务器重建顺序

目标环境是 Ubuntu x86_64、用户 ubuntu、固定项目路径 `/home/ubuntu/ai-news`。不同路径或用户需要同步修改 ROOT、端口与 systemd 配置，不能盲目运行。

1. 从本私有仓库克隆，安装与锁文件匹配的 Node/Python。根据 `docs/research/download_sources.py` 下载固定 SHA 的上游源码；Miniflux 使用 `src/runtime_fetch.py` 下载官方 2.3.3 并核对 SHA256。
2. Ubuntu PostgreSQL 16 / client / libpq 包可下载解压到 `runtime/pg`，不必安装系统服务；当前实例的启动参数见 `docs/ops/ai-news-postgres.service`。建立 Python venv 并安装锁定依赖。
3. 由维护者把 Ark Key 安全放到私有目录，再在自己的终端先执行 `src/bootstrap.py`，建立 PostgreSQL；认证初始化在安装服务单元后进行。这是初始化，不是显示密钥的命令。已有实例不需重跑；初始化失败不得改成免认证。
4. ReactFlux 使用 pnpm 11.21.0，RSSHub 使用 pnpm 10.34.5；分别安装固定 lockfile 依赖，构建 RSSHub，再运行 `src/build_frontend.py`。已有构建工具分别位于 `runtime/build-tools` 与 `runtime/rsshub-tools`。
5. 运行 `src/install_services.py` 安装 user systemd 单元，再运行 `src/initialize_secrets.py` 生成仅本机保存的认证配置并完成初始化。最后运行 `src/finalize_runtime.py` 确认独立 Node 与每日备份定时器。用户 systemd 环境变量见 SOP。
6. 使用 `artifacts/current-subscriptions.opml` 恢复本交付的订阅，或运行来源校验与 `src/import_sources.py`。模型分析是预算内后台工作，不要为了演示对所有历史条目无限调用 API。

## 验收入口

本轮分测点性能、登录、浏览器和未闭环事项见 [调试验收记录](DEBUGGING-ACCEPTANCE.md)；不要把历史交接数据当本轮实测。

```sh
cd /home/ubuntu/ai-news
PYTHONPATH=src runtime/venv/bin/pytest -q tests/test_core.py tests/test_worker.py tests/test_api.py tests/test_secret_config.py tests/test_publish.py
runtime/venv/bin/python tests/live_acceptance.py
AI_NEWS_WEB_BASE=http://127.0.0.1:8092 runtime/venv/bin/python tests/browser_acceptance.py
AI_NEWS_WEB_BASE=https://106.53.40.6 runtime/venv/bin/python tests/browser_acceptance.py
runtime/venv/bin/python src/backup.py
runtime/venv/bin/python src/backup.py --verify-latest
runtime/venv/bin/python src/audit_secrets.py
```

自动化测试用临时 SQLite 和 mock HTTP，不污染生产；live 与 browser 两项使用真实 Linux 服务。浏览器验收会临时改动一个真实条目的已读/收藏以及最低分/工具列表，并在 finally 恢复。运行时不要与人工同时编辑这些相同设置；浏览器任务不是单元测试的一部分。

专项验收脚本：

- tests/performance_acceptance.py：真实接口性能；tests/browser_performance.py：首批卡片、点击/深链全文、完整分页、gzip 与 X 失败界面。
- tests/test_ai_pagination.mjs：分页与去重游标；tests/cover_acceptance.py：封面候选；tests/browser_cover_acceptance.py：Airing 真实卡片图片 URL、加载尺寸及状态不变。
- tests/worker_logging_acceptance.py：worker 日志；tests/restart_acceptance.py：重启后的可用性。具体动作先读脚本，不将有状态验收误当只读诊断。

性能浏览器测试会临时打开条目并恢复已读状态；封面浏览器测试只看列表，拦截文章写请求并检查状态不变。真实浏览器验收串行运行，避免与人工编辑或其他浏览器任务冲突。通过数量、性能数值和回填进展只引用本次 artifacts 报告，不在交接中固定写死。

浏览器环境需 Playwright、Chromium 及其依赖。当前测试使用服务器已有 Chrome 153 和项目内解压的图形库，不修改系统；可通过 `CHROMIUM_EXECUTABLE` 指定自己的兼容 Chromium。中文字体属于测试运行环境，不随仓库分发。

## 后续最值得改进的方向

外部网络与社交授权解决后逐个平台接入并记录证据；需要论文全文时增加明确 PDF 解析而不是摘要代替；需要语义去重或偏好学习时另加结构化模块，不能把当前的相同正文复用/反馈保存宣传成已实现这些能力。不要替换成熟阅读器，也不要把 AI 结果改回第二个 RSS。

## 历史材料

`docs/research/DECISIONS.md` 及各仓库快照解释选型；`ACCESS-BLOCKERS.md` 仅保留早期安全检查的历史记录。最终交付状态、未通过的外部条件和证据以 `DELIVERY.md`、验收 Checklist 以及 `artifacts/` 中的机器可读报告为准。


## Kaggle Qwen 按需批处理

新增独立批处理入口，不替换网页服务。使用说明、模型固定版本、64K/思考配置、恢复及额度限制见 [Kaggle 运行手册](../../src/kaggle_batch/RUNBOOK.md)。定时任务保持关闭；旧付费 worker 开关保持不变。

真实 GPU、恢复、产品读回及限制见 [Kaggle 验收记录](kaggle/ACCEPTANCE.md)。


## 2026-10-08 bounded overnight optimization (active)

User authorized autonomous optimization through 07:00 UTC+8, with new implementation
cut off at 06:15. Main owns GitHub issue/PR closure, scoped CI and actual deployment.
Six Dot Astra/xhigh workers first inspected Reader lists, images, background queues,
Events filtering, frontend interaction and storage. Keep one existing checkout/runtime,
all private credentials server-side, existing budgets and the shared 1 GiB image cap.
Avoid redundant full tests, copied environments and paid stress tests.

Measured starting state: 19.8 GiB free of 49.1 GiB; health 200; native Reader healthy;
30 selected entries took 679/662/540 ms. Actual enrichment profile: 179 SQLite
connections, ~217 ms card parsing, ~396 ms enrichment. Reader list batching is the
first bounded issue. Preview source HTTP403 warnings were 670 distinct entries in
four hours, not repeated IDs; no ownership fix or blanket retry suppression is justified.
The live backup unit already supplies all five lane configs and paused-file.
The completed 100-article bilingual preview stays completed; do not reseed it.

Events #102 / PR #103 completed 2026-10-08 01:49 UTC+8: ten pending opens
share one GET; related UI CI 37661501898 passed in 84 seconds. Tested
a0d1b3e merged as 60140c8, exact app.js published, health 200, service PID unchanged.

Reader #173 uses the established delivery base codex/kaggle-qwen36-batches
(f1b564b); default main remains the earlier September branch. The focused scope
requires all four enrichment runtime modules and retains full fallback for other
shared changes. Redundant stub-only test scaffolding was removed; real dependency
tests cover the same behavior.

## Verified progress, 2026-10-08 02:35 UTC+8

- Events issue 102 closed / PR103 merged 60140c84c563672068bedf876a5acbf6e5b8f502.
  Production static/app.js SHA 5cbf5256e0914666eca687c124183dded2cd6a0f021784f8a745e4cb1735f318;
  same-details burst 10 requests -> 1, scoped UI CI success (84 seconds), health 200.
- Reader issue 173 closed / PR174 merged d28242f56453a4bb37caa7298a84c424b9a48902
  into the actual delivery branch codex/kaggle-qwen36-batches. GitHub main is an
  old September baseline; do not use it as the release base.
  Scoped CI 37664821562: 422 tests + 290 subtests passed. Metadata CI passed.
  Four backend files deployed 02:23:28. Source tree 6d7432883c76c6433dffb5bac4e30569e50246a7.
  Same 30 IDs/order/total: 5-request median 623.23 -> 370.25 ms (-40.6%).
  Health/raw/detail 200; native Miniflux PID 1230783 unchanged.
- Windows Edge verified Reader list and covers. 32 currently rendered images loaded,
  zero failed; a three-viewport scroll showed already loaded covers. This is a
  warm browser sample, not a cold-network performance guarantee.
- Removed 13 confirmed-unused Linux web-build staging directories, kept newest two
  and active frontend. Reclaimed 103161856 allocated bytes (~98.4 MiB); ~20 GiB free.
- Rejected Events single-pass facet refactor: current facets only 2.03 ms median,
  versus 75.59 ms candidate reading for 945 rows. No worthwhile user-facing benefit.
- New finite Reader issue 175: cover warmup may miss virtual cards mounted after
  the initial width measurement. Dot's original image worker owns only
  patches/ProgressiveLoadMore.jsx and tests/test_reader_thumbnail_component.mjs;
  primary owns CI, PR, deployment and handoff. Existing two-image concurrency,
  ten-page background prefetch and shared 1 GiB cache must remain unchanged.
- Kaggle current observation: fourth lane RUNNING, fifth submitted; no blanket
  cancellation/unquarantine. Prior quarantined batches retain their claims.
