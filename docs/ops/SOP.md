# 新实例运维 SOP

范围仅限 `/home/ubuntu/ai-news` 及 `ai-news-*.service`，不得修改 personal-news、FreshRSS、n8n、wechat-toolbox。不开放公网端口。

## 2026-09-22 初始化验收

- PostgreSQL 角色 `news_app`、数据库 `news` 已检查/补齐，数据库 owner 为 `news_app`。
- `.private/arkKey.txt`、`postgres.password`、`database.password`、`miniflux.env`、`ai.env` 权限均为 0600。
- Miniflux 管理员为 reader，密码保存在 `.private/miniflux.env`；不得打印该文件。Worker 的 Ark Key 与独立 Miniflux token 由 systemd 的 `EnvironmentFile` 注入。
- `src/worker.py` 已使用 `os.environ` 读取 Key，本轮不改 Worker、ReactFlux 或架构。
- 四个服务 active；端口 55432/8091/8092/1200 均只监听 127.0.0.1。
- Gateway `/healthz` 的 gateway、reader、rsshub、model_configured、reader_worker_configured 均 true。
- 经 Gateway 的 Miniflux `/mf/v1/me` token 验证 HTTP 200。
- Ark 模型 `deepseek-v4-flash-ga-260731` 完成一次真实请求，HTTP 200。只验证接口可用，不代表文章抓取和评分全流程验收。
- 开机启动配置保留/启用，未重启服务器进行断电恢复试验。

## 初始化与检查

以下命令在项目目录下执行；不执行旧 bootstrap 来覆盖现有配置。

```sh
timeout 150 runtime/venv/bin/python src/initialize_secrets.py
timeout 60 runtime/venv/bin/python src/verify_deployment.py
# 仅需重新验证模型时执行，会产生一次真实模型请求。
timeout 140 runtime/venv/bin/python src/verify_deployment.py --ark
timeout 90 runtime/venv/bin/python src/audit_secrets.py
timeout 30 runtime/venv/bin/python -m unittest discover -s tests -v
```

初始化脚本保留已有 Miniflux 管理员密码，并按描述复用已有 Worker token，避免重复创建。发现已有数据库 owner 异常时停止，不擅自变更。Secret 格式错误只返回失败，不打印值。

```sh
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
timeout 15 systemctl --user is-active ai-news-postgres ai-news-miniflux ai-news-rsshub ai-news-web
timeout 15 ss -ltn
```

修改 `.private/ai.env` 后需要重启 `ai-news-web.service` 才能生效。不要执行会展开服务环境变量的诊断命令，不把 Secret 或模型请求头贴入日志。

## Git 发布边界

`.private/`、`state/`、`runtime/`、`logs/`、`backups/`、上游运行副本及源码压缩包均忽略。只提交源码、运维文档、调研材料、source validation、catalog 和 OPML。

发布前运行 `audit_secrets.py`，检查所有可达历史 blob 与未忽略的当前文件：比对当前实际密钥及密码，并扫描常见私钥、GitHub token、API key、JWT 格式。只报告文件/对象与规则，不打印匹配内容；格式扫描不能证明不存在未知格式、已轮换且不再保留的历史密钥。

GitHub 仓库必须 Private，不能将凭据写入 remote URL。当前服务器未安装 GitHub 登录凭据，首轮通过本机已有授权和 Git bundle 转运推送，保留原始历史。服务器 remote 指向同一私有仓库；以后直接在服务器 push 需要单独授权，不能默认已具备 GitHub 写入权限。

本轮验收替代 `ACCESS-BLOCKERS.md` 中的早期初始化阻塞状态，旧文档保留为历史记录。

Miniflux API 官方参考：https://miniflux.app/docs/api.html
