# 个人信息箱 · Personal AI Inbox

自己部署的网页版信息收集箱：订阅博客、厂商动态、X 等来源，由 AI 打分并写中文卡片，只把值得看的挑出来；英文好文自动配上中文翻译。

- 线上地址：<https://lylme.cn/inbox/>，用户名 `qqzl`。密码在服务器 `.private/miniflux.env`，只在自己的终端查看
- 接手与运维：[docs/ops/HANDOFF.md](docs/ops/HANDOFF.md)
- 全部文档索引：[docs/README.md](docs/README.md)

## 日常怎么用

**三种阅读方式**（顶栏左侧的分段按钮）

| 方式 | 内容 |
|---|---|
| AI 精选 | 推荐分达到门槛（默认 ≥8，顶栏可调）的文章卡片：中文摘要、推荐分、技术价值、商业启发、推荐理由 |
| 全部原始 | 所有订阅内容，含尚未分析的 |
| 待处理 | 排队中、抓取失败、模型出错的条目，看得到原因 |

- **排序**：新鲜优先（推荐分扣掉一点“发布天数”，新文章更靠前）、推荐分、技术价值、商业启发、发布时间，都可以正反向。
- **读文章**：站内阅读原文和图片，保留原文链接。英文文章可切换“原文 / 中文 / 双语对照”，≥8 分的英文文章会在后台提前翻译好；文章右上角还有排版和专注模式，也可以给文章写笔记。
- **快速跳转**：`Ctrl/⌘ K` 打开命令面板，跳到来源、分类或功能。
- **来源与设置**：来源目录（候选来源、已订阅来源、每个来源的历史范围）、AI 设置、工具入口。
- 已读、收藏、订阅和 AI 结果都存在服务器上，换设备也一样；手机浏览器有专门的布局。

## 系统组成

```text
来源：RSS/Atom · RSSHub（:1200） · Python 厂商适配器 · X（x-provider :17910 → x-feed :17911）
   │  出站流量走 mihomo 代理（:17890）
   ▼
Miniflux 2.3.3（:8091）+ 独立 PostgreSQL（:55432）
   │  负责订阅、条目、已读、收藏——阅读器本身不重写
   ▼
AI 增强层（SQLite：state/analysis.sqlite3）
   ├─ 评分与中文卡片：Kaggle 上的 Qwen3.6-35B-A3B，5 个账号轮流跑批，每 11 分钟调度一次
   ├─ 全文翻译：经服务器上的 New API 调用 gpt-4o-mini，滚动翻译 ≥8 分的英文文章
   └─ 封面与正文图片：后台预热，1 GiB 共享缓存
   ▼
网关 src/api.py（FastAPI :8092）：提供 /inbox/ 前端，转发 /mf/ 的 Miniflux API 并加上 AI 扩展接口
   ▼
Nginx（/inbox/、/mf/）→ 浏览器里的 ReactFlux（固定上游版本 + 本仓库的补丁）
```

AI 结果存成独立的元数据，不会再生成一份“AI 版 RSS 文章”。原来按次付费的火山方舟分析通道（`src/worker.py`）仍保留，但目前在设置里关闭。

## 几条边界

- 评分是模型的判断，不保证事实正确；`evidence` 只校验引文确实出现在原文里。
- 历史文章没有全部分析完，Kaggle 批处理按队列逐步推进。
- 翻译有每日 Token 上限（当天文章另有保留额度），到上限就停，第二天接着翻。
- X 用访客模式抓取，稳定性取决于对方限制；Telegram、Instagram、Facebook 目前没有接通。
- 数据库每天在本机备份，**不是异地备份**；GitHub 只放代码和文档，不放文章数据与私有配置。

## 仓库结构

| 路径 | 作用 |
|---|---|
| `src/api.py` | 网关：静态前端、Miniflux API 转发、AI 列表 / 状态 / 设置 / 笔记等扩展接口 |
| `src/core.py`、`content_input.py`、`content_quality.py` | AI 元数据库、预算、原文提取与质量判断 |
| `src/kaggle_batch/` | Kaggle Qwen 批处理：准备、提交、回收、校验、导入（[运行手册](src/kaggle_batch/RUNBOOK.md)） |
| `src/bilingual_translation.py`、`card_translation.py` | 全文双语翻译、卡片翻译 |
| `src/vendor_sources.py`、`x_feed_server.py`、`x_feed_prefetch.py`、`x_source.py` | 厂商网页适配器、X 订阅源 |
| `src/reader_image_*.py`、`warm_reader_covers.py` | 图片代理、缓存与封面预热 |
| `src/backup.py`、`diagnose.py`、`verify_deployment.py` | 备份、只读诊断、部署检查 |
| `src/build_frontend.py`、`src/patch_*.py`、`src/install_*.py` | 给 ReactFlux 打补丁、构建、发布 |
| `frontend-review/after/`、`patches/` | 前端补丁的完整源文件（审过的版本） |
| `sources.catalog.json`、`x_sources.catalog.json`、`tools.catalog.json` | 候选来源、X 账号、工具入口目录 |
| `upstream.lock.json` | 上游组件的固定版本与哈希 |
| `tests/` | Python、Node 与浏览器测试；`tests/build_ci_reader.py` 可复现前端构建 |

## 开发与测试

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.dev.lock.txt
PYTHONPATH=src .venv/bin/python -m pytest -q
```

前端构建会拉取固定版本的 ReactFlux，套上补丁后用 pnpm 11.21.0 构建：

```bash
python tests/build_ci_reader.py --prepare-only
pnpm --dir upstream/reactflux install --frozen-lockfile --ignore-scripts
python tests/build_ci_reader.py
```

CI 在 `.github/workflows/reader-regression.yml`，会按改动的文件挑选要跑的检查；只改 `README.md` 或 `docs/*.md` 时只跑文档检查。

## 开源组成

- [Miniflux](https://github.com/miniflux/v2) 2.3.3（Apache-2.0）：订阅与阅读后端；官方源码加一个小的批量元数据接口补丁（`ops/miniflux-metadata/`），用 Go 1.27.3 编译
- [ReactFlux](https://github.com/electh/ReactFlux)（MIT）：网页阅读器，固定提交 + 本仓库补丁
- [RSSHub](https://github.com/DIYgod/RSSHub)（AGPL-3.0）：扩展来源，只在本机回环监听

版本与哈希见 `upstream.lock.json`，选型理由见 `docs/research/`。
