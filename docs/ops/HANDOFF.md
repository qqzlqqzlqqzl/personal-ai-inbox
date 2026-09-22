# 接手与重建说明

## 代码边界

`src/api.py` 是同源网关与 AI 扩展接口，不是新的阅读器；原生 Miniflux API 仍负责内容、订阅、已读和收藏。`src/worker.py` 负责完整分页发现、按来源公平排队、原文提取、模型调用、证据校验与失败重试；`src/core.py` 负责独立 SQLite 元数据与预算。

`src/content_input.py` 区分原网页与本实例可信社交适配器原帖，并可从原网页声明的元数据补取真实封面。没有视频理解、论文 PDF 正文管道或通用跨站全文保证。重复复用只针对相同正文及相同模型/提示词配置，不是语义事件聚合。

ReactFlux 的覆盖源在 `patches/`。`src/patch_frontend.py` 应用初始补丁，`src/polish_frontend.py` 修复中文登录、标识、PWA 回退和控制台细节；`src/build_frontend.py` 统一完成补丁、离线构建及分阶段发布。上游源码保留于 `upstream/reactflux`，不提交其依赖或构建目录。

## 运行与配置

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

```sh
cd /home/ubuntu/ai-news
PYTHONPATH=src runtime/venv/bin/pytest -q tests/test_core.py tests/test_worker.py tests/test_api.py tests/test_secret_config.py tests/test_publish.py
runtime/venv/bin/python tests/live_acceptance.py
runtime/venv/bin/python tests/browser_acceptance.py
runtime/venv/bin/python src/backup.py
runtime/venv/bin/python src/backup.py --verify-latest
runtime/venv/bin/python src/audit_secrets.py
```

自动化测试用临时 SQLite 和 mock HTTP，不污染生产；live 与 browser 两项使用真实 Linux 服务。浏览器验收会临时改动一个真实条目的已读/收藏以及最低分/工具列表，并在 finally 恢复。运行时不要与人工同时编辑这些相同设置；浏览器任务不是单元测试的一部分。

浏览器环境需 Playwright、Chromium 及其依赖。当前测试使用服务器已有 Chrome 153 和项目内解压的图形库，不修改系统；可通过 `CHROMIUM_EXECUTABLE` 指定自己的兼容 Chromium。中文字体属于测试运行环境，不随仓库分发。

## 后续最值得改进的方向

外部网络与社交授权解决后逐个平台接入并记录证据；需要论文全文时增加明确 PDF 解析而不是摘要代替；需要语义去重或偏好学习时另加结构化模块，不能把当前的相同正文复用/反馈保存宣传成已实现这些能力。不要替换成熟阅读器，也不要把 AI 结果改回第二个 RSS。

## 历史材料

`docs/research/DECISIONS.md` 及各仓库快照解释选型；`ACCESS-BLOCKERS.md` 仅保留早期安全检查的历史记录。最终交付状态、未通过的外部条件和证据以 `DELIVERY.md`、验收 Checklist 以及 `artifacts/` 中的机器可读报告为准。
