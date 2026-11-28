# 接手说明

写给接手维护的人（包括 AI 助手）。最后核对：2026-11-24。

先读完“一、目录地图”和“二、改动前必须知道”，这两节能避免大部分事故。日常命令见 [SOP.md](SOP.md)。2026-09 到 2026-10 按时间追加的旧交接日志已归档到 [history/HANDOFF-2026-09-to-10.md](history/HANDOFF-2026-09-to-10.md)，只当背景资料看。

## 一、目录地图

线上运行分散在三个目录，靠软链接串起来。**改代码前先确认改的是哪一份。**

| 目录 | 是什么 | 谁在用 |
|---|---|---|
| `~/website-release-20261004/reader-3349cf2-source/` | **现行后端源码**（`AI_NEWS_ROOT`）。`src/` 是真实文件，`SOURCE.json` 记着发布时对应的提交 | 网关 `ai-news-web`、Kaggle 调度与五个批处理、厂商适配器、封面预热、备份 |
| `~/ai-news.before-20261004-fullcycle/` | **数据与运行环境**：`state/`（数据库）、`runtime/`（Python venv、Node、PostgreSQL 程序）、`.private/`（密钥）、`backups/`、`logs/` | 上面那份源码的 `state`、`runtime`、`.private` 等都是指向这里的软链接 |
| `~/ai-news` → `~/website-release-20261004/reader-release-21f49-v2/view/` | 历史入口。`upstream/` 是真实目录，里面的 `reactflux/build/` 就是**线上前端**；`src/` 是 10 月初的**旧代码副本** | Nginx 提供的前端文件；X 订阅的两个单元（`ai-news-x-feed`、`ai-news-x-prefetch`）还从这份旧 `src/` 启动；Kaggle 五个批处理的配置文件在 `~/ai-news/src/kaggle_batch/cloud-config-month-*.json` |

其他常用位置：

- `~/ai-news-worktrees/`：历次任务的 Git 工作树。其中 `prod-snapshot` 带推送用的部署密钥（`git config core.sshCommand`）
- `~/.config/systemd/user/ai-news-*`：用户级 systemd 单元。很多单元靠 `*.service.d/` 里的 drop-in 改写了 `ExecStart`，**看实际生效的命令要用 `systemctl --user cat <单元>`**，不要只看单元文件本身
- `~/cloud-maintenance/`：自动恢复脚本，见该目录的 README

### 服务与端口（全部只监听 127.0.0.1）

| 端口 | 单元 | 作用 |
|---|---|---|
| 8092 | `ai-news-web` | 网关：`/inbox/` 前端、`/mf/` Miniflux API 转发与 AI 扩展接口 |
| 8091 | `ai-news-miniflux` | Miniflux 2.3.3（带批量元数据补丁，Go 1.27.3 编译） |
| 55432 | `ai-news-postgres` | Miniflux 专用 PostgreSQL 16，数据在 `state/postgres` |
| 1200 | `ai-news-rsshub` | RSSHub（9464 是它的指标端口） |
| 17890 / 19090 | `ai-news-proxy` | mihomo 出站代理 / 控制器，见 [PROXY.md](PROXY.md) |
| 17910 / 17911 | `ai-news-x-provider` / `ai-news-x-feed` | X 访客抓取 / 转成 Atom 订阅源 |

### 定时任务

| 单元 | 频率 | 作用 |
|---|---|---|
| `ai-news-kaggle-recovery.timer` | 每 11 分钟 | Kaggle 调度与恢复：给 5 个账号的批处理（`ai-news-kaggle-month@{primary,secondary,third,fourth,fifth}`）派活、回收、导入 |
| `ai-news-vendor-refresh.timer` | 每 15 分钟检查 | 刷新到期的厂商网页适配器（每个来源约 6 小时一次） |
| `ai-news-x-prefetch.timer` | 每 15 分钟 | 刷新一批 X 订阅缓存 |
| `ai-news-reader-covers.timer` | 上一轮结束 5 分钟后 | 预热最新 ≥8 分文章的封面和正文图片 |
| `ai-news-backup.timer` | 每天 04:15 | 在线备份 PostgreSQL 与 SQLite，保留 2 份，上限 4 GiB |
| `cloud-recovery.timer` | 每 5 分钟 | 健康检查；Miniflux / RSSHub / 网关 / New API 连续失败时有限次重启 |

## 二、改动前必须知道

1. **GitHub 默认分支是 `codex/kaggle-qwen36-batches`**，新改动都基于它。`main` 停在 9 月，不要用来发布。
2. **线上目录不是 Git 工作副本。** 发布方式是：在临时克隆里改、测试、提交 → 把文件同步到线上目录 → 用部署密钥推到默认分支（见第四节）。
3. **前端补丁有哈希护栏。** `frontend-review/previous-hashes.json` 和安装脚本里记着“允许被替换的旧文件哈希”。改了某个被补丁覆盖的文件，要同时更新对应哈希并在 `tests/test_source_catalog_overlay.py` 里登记，否则构建会以 “Unreviewed source drift” 失败。
4. **服务器内存只有 2 GB。** 前端构建要限制 Node 堆（`--max-old-space-size=900`），并用 `systemd-run --user --scope -p MemoryMax=1200M` 跑，避免挤掉线上服务。
5. **密钥不出服务器。** `.private/` 下的文件不要打印、不要复制进 Git、不要贴到聊天里。需要确认配置时只看变量名。
6. **用户删掉的来源不要自动恢复**（例如普通开源发布 / release notes 类来源）。
7. 改 systemd 单元前先 `systemctl --user cat` 看 drop-in；实际执行的命令常常和单元文件里写的不一样。

## 三、AI 处理现状

| 环节 | 现状 |
|---|---|
| 评分与中文卡片 | Kaggle 免费 GPU 上的 Qwen3.6-35B-A3B（GGUF），5 个账号共享同一队列。凭据在 `.private/kaggle/`，批次状态在 `state/kaggle-batches/` 和 `state/kaggle-month-*/`。细节见 [Kaggle 运行手册](../../src/kaggle_batch/RUNBOOK.md) |
| 按次付费分析 | 火山方舟通道（`src/worker.py`）保留但**已关闭**（AI 设置里 `enabled=false`） |
| 全文翻译 | `src/bilingual_translation.py`，经服务器上的 New API 调用 `gpt-4o-mini`。自动滚动翻译 ≥8 分的英文文章：先今天，再最近 7 天，再更早。每日上限 5,000,000 Token / 10,000 次请求（其中 1,000,000 Token 留给当天文章），北京时间 08:00 重置。配置在 `.private/ai.env` 的 `BILINGUAL_*` 变量。细节见 [BILINGUAL-READER.md](BILINGUAL-READER.md) |
| 推荐门槛 | AI 精选默认 ≥8 分，用户可在顶栏调整 |

## 四、发布流程

### 后端（Python）

1. 在临时克隆里改代码，跑相关测试：`PYTHONPATH=src python -m pytest -q tests/test_api.py …`
2. 备份线上文件（例如复制到 `~/ux-review-*/` 之类的备份目录），再把改好的文件放进 `reader-3349cf2-source/src/`。
3. 重启受影响的单元：`systemctl --user restart ai-news-web`（Kaggle、备份等单元下次触发时自动用新代码）。
4. 检查：`curl -s http://127.0.0.1:8092/healthz`、`journalctl --user -u ai-news-web -n 50`。

### 前端（ReactFlux 补丁）

1. 在临时克隆里构建，与 CI 完全相同的方式：

   ```bash
   python3 tests/build_ci_reader.py --prepare-only
   pnpm --dir upstream/reactflux install --frozen-lockfile --ignore-scripts
   python3 tests/build_ci_reader.py
   ```

   产物在 `runtime/browser-build/`。本机构建记得先把 `tests/build_ci_reader.py` 里的 `max-old-space-size` 调到 900，构建完改回来。
2. 核对产物（入口文件名、整个目录的哈希）与预期一致。
3. 发布到线上，旧的哈希资源会保留，已打开的页面不会坏：

   ```bash
   cd 临时克隆/src
   python3 -c "from pathlib import Path; import build_frontend; build_frontend.publish(Path('../runtime/browser-build'), Path('/home/ubuntu/ai-news/upstream/reactflux/build'), '/inbox/')"
   ```

   发布前先备份线上的 `index.html` 和 `sw.js`。
4. 每次发布后执行[发布后冒烟检查](SOP.md#发布后冒烟检查)，再打开 <https://lylme.cn/inbox/> 确认页面正常。

### 推送到 GitHub

服务器直连 GitHub 很慢，先设代理，再借 `prod-snapshot` 的部署密钥推：

```bash
export HTTPS_PROXY=http://127.0.0.1:17890
KEY=/home/ubuntu/ai-news-worktrees/prod-snapshot
git -c core.sshCommand="$(git -C $KEY config core.sshCommand)" \
  push "$(git -C $KEY remote get-url origin)" HEAD:refs/heads/codex/kaggle-qwen36-batches
```

只做快进推送，不要强推。

## 五、私有配置（只列位置，不列内容）

| 文件 | 内容 |
|---|---|
| `.private/miniflux.env` | Miniflux 数据库连接与管理员账号（登录密码以这里为准，不是旧的 `access.json`） |
| `.private/ai.env` | 网关环境变量：模型 Key、Miniflux API Key、出站代理、`BILINGUAL_*` 翻译配置 |
| `.private/rsshub.env` | RSSHub 参数 |
| `.private/mihomo/` | 代理订阅与配置 |
| `.private/kaggle/` | 5 个 Kaggle 账号的凭据 |
| `.private/database.password`、`postgres.password` | PostgreSQL 密码 |
| `.private/github_deploy*` | GitHub 部署密钥（`prod-snapshot` 的 `core.sshCommand` 用的就是它） |

以上路径都相对于 `~/ai-news.before-20261004-fullcycle/`（也可经 `~/ai-news/.private/` 访问）。

## 六、出问题时

| 现象 | 先看 |
|---|---|
| 页面白屏 | 浏览器开发者工具看 `/inbox/assets/*.js` 是否 404：通常是前端构建时 base 路径不是 `/inbox/` |
| 打不开 / 502 | `systemctl --user status ai-news-web ai-news-miniflux ai-news-postgres`；`cloud-recovery` 每 5 分钟会自动尝试重启 |
| 新文章不进来 | Miniflux 的订阅错误（来源目录可见）；出站代理 `ai-news-proxy` 是否在线 |
| AI 精选不更新 | `journalctl --user -u ai-news-kaggle-recovery -n 80`；Kaggle 账号可能处于冷却或配额用完，不要为了绕过而重复提交 |
| 翻译停了 | 多半是当天额度用完，第二天 08:00 后自动继续 |
| 一站式只读诊断 | `timeout 65s ~/ai-news/runtime/venv/bin/python ~/website-release-20261004/reader-3349cf2-source/src/diagnose.py`，日志字段说明见 [LOGGING.md](LOGGING.md) |
