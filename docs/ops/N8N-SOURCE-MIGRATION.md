# 12 信息源迁移与 n8n 退役（#53，配套 #52）

## 架构
同一个 ai-news-web / FastAPI 提供 `/internal/vendor-feeds/{key}.xml`。RSS GET 只读快照，不抓上游，不创建新守护进程。
每 15 分钟 systemd 检查到期来源；各源成功后间隔 6 小时，失败 30 分钟后重试；最大并发 2，继承已验证的原来源 URL/选择器/公共 HTTP headers 和出站代理。
每个来源使用独立 flock、临时文件与原子 rename。空页、错误页、解析错、状态损坏不清空旧条目；错误类型、上次成功与下一次执行时间可查询。
缓存最多 200 条/源，seen 去重集合与原工作流语义一致；已有条目不重打时间、不重算评分。
Kickstarter 明确是 BrandsNinja 邮件归档，不冒充项目正文或官方 RSS。

## 接口与访问边界
- RSS 和 `/internal/vendor-feeds/status` 仅直接回环访问，反代头/非回环客户端拒绝。
- POST `/internal/vendor-feeds/{key}/refresh` 额外要求 X-Vendor-Refresh；失败返回 502 而非虚假成功。
- 已登录管理员可通过 `/mf/v1/ai/vendor-sources` 查询状态；路由位于 Miniflux 通配代理之前。
- 读取缓存失败返回 503；有效缓存下上游失败仍保留 RSS，并通过 X-Source-State/诊断报告呈现 stale-error。

## 发布顺序
1. 独立 worktree 测试、自审、PR 合并。生产代码与目录存在 WIP 时，仅合入本次差异，不整目录覆盖。
2. 在 `.private/n8n-retirement-<time>/` 用 SQLite backup 保存原 n8n 数据库、原服务文件、工作流 active 状态和生产文件哈希；秘密、数据库不入 Git。
3. 用 `vendor_sources.py --import-n8n <database>` 导入持久条目；可重复执行，已种子化的目标不会被覆盖。
4. shadow 导入与真实抓取均通过后发布四个 vendor Python/config 文件及 API 接线；等待 readyz 200。
5. `vendor_migration.py` 默认仅给出计划；`--apply --backup <AI_NEWS_ROOT/.private/独立新目录>` 原 ID 原地改 URL。保留分类、抓取配置、阅读状态；连接异常时回读，尽量自动回滚；检查 manifest。
6. 安装 deploy/systemd 下 vendor service/timer 与 #52 调度单元。先验证服务与手动刷新，再 enable timer。
7. 逐源 Miniflux refresh、条目 ID/阅读状态/日期比对；真实桌面与移动 viewport 验证来源和文章交互。
8. 所有依赖迁移后 `systemctl stop news-n8n.service`、`systemctl disable news-n8n.service`；检查 5678/5679 关闭、n8n/task-runner 退出、其他服务与远控仍在。

停用不是卸载：**保留 personal-news/node 与远控共享依赖、n8n 数据和回滚数据库**，不得为腾磁盘删除整个 personal-news。

## 回滚
先停 vendor timer 与唯一 Kaggle 自动 timer；重新启用/启动旧 news-n8n.service，确认旧 RSS 端点，然后 `vendor_migration.py --rollback <manifest.json>` 回改订阅 URL 与源目录（只改本次的 URL 字段）。恢复旧 API/单元文件并复验，再恢复一套自动调度。
不要通过已完成测试推断未来 24 小时稳定或上游永远可用；后续失败保留诊断。

## 2026-10-01 shadow 验证
12/12 实际上游抓取成功；552 条旧缓存保留，新增 0；12 路输出的 GUID、标题、链接、pubDate、description 与原 n8n 输出逐条相等。
RSS 不是触发器：这些校验之外另做真实抓取，没有将缓存 200 当作抓取成功。
