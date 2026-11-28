# 日常运维命令

目录和各服务的关系先看 [HANDOFF.md](HANDOFF.md)。以下命令都在服务器上以 `ubuntu` 用户执行。

为了少打字，先设两个变量：

```bash
SRC=/home/ubuntu/website-release-20261004/reader-3349cf2-source   # 现行后端源码
PY=/home/ubuntu/ai-news/runtime/venv/bin/python
```

从非交互终端（例如脚本、远程命令）操作用户级 systemd 前，还需要：

```bash
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
```

## 看状态

```bash
curl -fsS http://127.0.0.1:8092/healthz
systemctl --user status ai-news-web ai-news-miniflux ai-news-postgres ai-news-rsshub
systemctl --user list-timers 'ai-news-*' 'cloud-*'
journalctl --user -u ai-news-web -n 60 --no-pager
timeout 65s $PY $SRC/src/diagnose.py
```

`diagnose.py` 是只读的一站式检查：服务、心跳、预算、订阅错误、最近事件和慢请求。日志各字段的含义见 [LOGGING.md](LOGGING.md)。

## 重启

只重启 `ai-news-*` 前缀的单元，不要为了本项目重启整台服务器或其他应用。

```bash
systemctl --user restart ai-news-web
```

改了 `.private/ai.env` 之后也要重启 `ai-news-web` 才生效。

## 发布后冒烟检查

每次发布前端后，从用于发布的克隆根目录执行：

```bash
python tests/smoke_live_frontend.py --build /home/ubuntu/ai-news/upstream/reactflux/build
```

脚本用 gzip 和 identity 分别请求线上页面和 Service Worker，核对两种响应引用的入口包与本地构建一致，并确认入口包可下载。

2026-11-28 排查发现，线上目录残留了 10 月的 `.gz` 文件，支持 gzip 的浏览器一直拿到旧 Service Worker，并继续加载旧入口包。此前手工检查的 `curl` 请求未启用 gzip，只看到了更新后的原文件，因此漏掉了问题。

## 条目状态的含义

| 状态 | 含义 | 处理 |
|---|---|---|
| `fetch_error` | 原站请求或正文提取失败 | 看来源是否改版、是否被限制 |
| `ai_error` | 模型调用、格式或证据校验失败 | 看日志里的阶段和错误类型 |
| `insufficient_content` | 正文太短，信息不够 | 一般无需处理 |
| `budget_paused` | 当天额度用完 | 等第二天自动继续 |
| `waiting_model` | 没有可用的模型通道 | 检查 Kaggle 调度或 AI 设置 |

先处理原因，再在“待处理”里重试。不要把失败直接改成成功，也不要关掉预算保护。

## 来源

来源可以在阅读器侧栏添加、暂停、编辑、删除。来源目录里展开已订阅来源的“历史范围”，可以看到存了多少条、最早和最晚的发布时间。用户删掉的来源不要自动加回来。

## 备份与恢复

`ai-news-backup.timer` 每天 04:15 做一次在线备份：用 `pg_dump` 导出 Miniflux 数据库，用 SQLite 在线备份接口复制各个 SQLite 文件。快照放在 `~/ai-news/backups/online-*`，保留最近 2 份，总量上限 4 GiB。

```bash
systemctl --user list-timers ai-news-backup.timer
journalctl --user -u ai-news-backup -n 5 --no-pager
$PY $SRC/src/backup.py --verify-latest
```

`--verify-latest` 会把最新快照恢复到一个临时数据库里检查，然后删掉，不碰正式数据。

注意：备份和正式数据在**同一块盘**上，不是异地灾备；`.private/` 里的密钥也不在快照里，迁移服务器时要另外安全保管或重新生成。

**真正回滚生产数据只能由维护者明确决定后执行。** 大致步骤：先另做一份当前快照 → 停 `ai-news-web` 和 `ai-news-miniflux` → 校验选定快照的 SHA256 → 用 `news_app` 角色执行 `pg_restore --clean --if-exists --no-owner --no-acl` 恢复到 `news` 库 → SQLite 用 Python 的 `Connection.backup()` 恢复（不要直接覆盖带 WAL 的文件）→ 启动服务 → 检查登录、列表和文章。

## 网络

服务器的出站请求（抓原站、RSSHub、X、翻译）走本机 mihomo 代理 `127.0.0.1:17890`，配置与节点规则见 [PROXY.md](PROXY.md)。日常访问直接用 <https://lylme.cn/inbox/>，不需要 SSH 隧道；隧道只在维护或公网故障时用，见 [LOCAL_ACCESS.md](LOCAL_ACCESS.md)。

## 提交到 GitHub 前

```bash
$PY $SRC/src/audit_secrets.py
```

确认 `.private/`、`state/`、`runtime/`、`logs/`、`backups/`、`upstream/` 都没有被加进提交。不要强推，不要改写历史；回滚代码用 `git revert` 或新分支。
