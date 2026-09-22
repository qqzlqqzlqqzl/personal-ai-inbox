# 运维 SOP

## 1. 固定边界

项目仅位于 `/home/ubuntu/ai-news`，以 `ubuntu` 用户运行。不得修改旧 `personal-news`、FreshRSS、n8n 或 `wechat-toolbox`。四个服务都只绑定回环地址；不要用 `0.0.0.0` 或放开防火墙来掩盖私有访问问题。

非交互终端执行用户 systemd 前设置：

```sh
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
cd /home/ubuntu/ai-news
```

## 2. 检查与重启

```sh
runtime/venv/bin/python src/verify_deployment.py
curl -fsS http://127.0.0.1:8092/healthz
systemctl --user status ai-news-postgres ai-news-miniflux ai-news-rsshub ai-news-web
journalctl --user -u ai-news-web -n 60 --no-pager
systemctl --user restart ai-news-web
```

`--ark` 会额外做一次小型真实模型请求，不能高频调用。健康检查的 `model_configured` 仅表示配置存在；真实成功结果应检查 AI 状态及验收报告。不要在日志中输出环境变量、请求头或 `.private` 文件。

## 3. 模型与来源

提示词、接口地址、模型、最低分和预算在网页控制台调整，服务端保存。模型仍须返回规定 JSON 字段；`evidence` 必须是原文中的连续引句，不能翻译。默认每日最多 80 次模型请求、500,000 Token，UTC 日期切换；失败请求也占额度，缺失用量时保守保留预留预算。

密钥只在 `.private/ai.env`；变更后重启 `ai-news-web`。管理员登录只以 `.private/miniflux.env` 为准。对外分享日志或截图前检查是否含密码、令牌或原文隐私内容。曾在不安全位置显示过的密钥应轮换。

来源可在原生侧栏添加、暂停、编辑、删除。我们导入时不让 Miniflux 对所有历史条目同步爬取，而由 AI worker 按队列先取原文，避免导入被整批网页抓取阻塞。`source_text` 记录实际模型输入，`source_chars` 与 `input_chars` 分别表示抽取长度和实际输入长度。

失败状态含义：`fetch_error` 原站请求或抽取失败；`ai_error` 模型调用/格式/证据失败；`insufficient_content` 正文信息过少；`budget_paused` 等预算；`waiting_model` 未配置模型。先处理原因，再用控制台重试；不要把失败改成成功，不要取消预算保护。

## 4. 安全更新

更新前先备份，之后只拉取已审阅的私有项目提交：

```sh
runtime/venv/bin/python src/backup.py
 git pull --ff-only
runtime/venv/bin/python src/audit_secrets.py
PYTHONPATH=src runtime/venv/bin/pytest -q tests/test_core.py tests/test_worker.py tests/test_api.py tests/test_secret_config.py tests/test_publish.py
runtime/venv/bin/python src/build_frontend.py
systemctl --user restart ai-news-web
runtime/venv/bin/python tests/live_acceptance.py
```

`build_frontend.py` 在独立目录构建，校验资源后发布，保留旧哈希资源，让已打开的浏览器仍能加载旧分块。不要直接清空线上 `upstream/reactflux/build`。上游 ReactFlux 版本由快照 SHA 锁定；升级上游须更新补丁与期望 SHA，并完整重跑浏览器测试，不能直接追 `main/latest`。

## 5. 备份与隔离恢复验证

每日约服务器本地时间 04:15—04:25 运行 `ai-news-backup.timer`，保留最近 14 个完整快照。备份会短暂停止本项目的 web 和 Miniflux（实测约两秒），完成后恢复；不会停止 PostgreSQL、RSSHub 或旧应用。数据含账号和 API 令牌，整个备份目录保持私有且绝不能进入 Git。

```sh
systemctl --user list-timers ai-news-backup.timer
runtime/venv/bin/python src/backup.py
runtime/venv/bin/python src/backup.py --verify-latest
```

验证命令会创建独立的 `acceptance_restore_*` 临时数据库，检查行数及 SQLite 完整性，然后删除临时库；不会覆盖 `news`。快照位于 `backups/snapshot-*/`，含校验清单。当前是同盘备份，不是异地灾备；私有环境配置不作为文件复制进快照，迁移时需另行安全保管，或重新配置与轮换凭据。

**真正回滚生产数据只由维护者明确执行。** 先另做一份当前快照，再停止 web/Miniflux。校验选定快照的 SHA256，通过隔离恢复验证后，使用 `news_app` 角色和其私有密码，将 `miniflux.dump` 恢复到 `news`（`pg_restore --clean --if-exists --no-owner --no-acl`）。不要以 `newsowner --no-owner` 恢复后忘记业务表所有者，否则应用会失去权限。SQLite 使用 Python `Connection.backup()` 从快照恢复到正式数据库，避免直接覆盖带旧 WAL 的文件。最后恢复匹配的私有配置、启动两项服务，重跑 `/v1/me`、真实数据及浏览器验收。不要对生产执行验收临时数据库的 DROP 命令。

## 6. 网络与社交适配

服务器出站代理与电脑访问网站的入站隧道是两件事。前者解决原站抓取，后者解决浏览器能否访问回环服务。日常浏览器直接使用 `https://106.53.40.6/inbox/`，无需入站隧道；SSH 仅作维护/故障备用，转发示例见 LOCAL_ACCESS.md；首次连接必须验证主机指纹。不要上传 SSH 密码、Ark Key 或浏览器 Cookie 来排查普通页面问题。

RSSHub 参数放在私有 `.private/rsshub.env`；只根据固定上游版本的官方文档配置，再重启 `ai-news-rsshub`。不要把参数值输出到日志。X 常需账号 Token，Instagram 需对应授权；Telegram 公共网页源不代表私有群组访问权限。网络恢复后执行：

```sh
runtime/venv/bin/python tests/check_social.py
```

这会检验公开 Telegram 官方频道，并在成功时作为示例加入“社交动态”分类。HTTP 503 应检查 RSSHub 日志与出站网络；不能把服务 `/healthz` 成功当成平台连接成功。

## 7. GitHub 留档

每次变更明确选择源码、补丁、文档和非敏感证据提交。不要使用强推，不要删除原有历史。提交与推送前运行 `src/audit_secrets.py`；确认 `.private/`、`state/`、`runtime/`、`logs/`、`backups/`、`upstream/` 未被追踪。服务器的 Git SSH 部署密钥仅授权本私有仓库；迁移或退役服务器时在 GitHub 撤销此键。

## 8. 停止 / 回滚程序版本

仅停止本项目前缀 `ai-news-*`。不要关机、重启整台服务器、删系统库或停止旧应用来解决本项目问题。代码回滚采用明确版本的新分支或 `git revert`，重新构建及重启后复测；数据库结构如变化，先核对迁移兼容性，必要时按已验证快照恢复。
