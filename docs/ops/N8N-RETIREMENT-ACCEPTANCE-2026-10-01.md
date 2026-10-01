# n8n 退役最终验收：2026-10-01

验收主机：VM-0-4-ubuntu；项目：/home/ubuntu/ai-news；生产分支：codex/kaggle-qwen36-batches。
10:17（UTC+8）停用系统级 news-n8n.service 后，替代链路、既有数据和公网 Chromium 验收均通过，可关闭 #52 / #53。

## 实际执行与状态

通过 Windows OpenSSH 密钥认证连接 ubuntu@106.53.40.6:22，执行：

```bash
sudo -n systemctl disable --now news-n8n.service
systemctl is-active news-n8n.service
systemctl is-enabled news-n8n.service
```

结果：inactive / disabled，MainPID=0，SubState=dead。5678 和 5679 无监听，n8n 主进程和 @n8n/task-runner 均退出。
未 mask、卸载或删除单元；/etc/systemd/system/news-n8n.service 保留。
旧数据库 /home/ubuntu/personal-news/state/.n8n/database.sqlite、personal-news/node 和完整回滚备份保留。

用户级命令使用以下环境：

```bash
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
systemctl --user start ai-news-vendor-refresh.service
systemctl --user show ai-news-vendor-refresh.service -p Result -p ExecMainStatus -p ActiveState
systemctl --user start ai-news-kaggle-recovery.service
systemctl --user show ai-news-kaggle-recovery.service -p Result -p ExecMainStatus -p ActiveState
systemctl --user list-timers ai-news-kaggle-recovery.timer ai-news-vendor-refresh.timer --no-pager
```

10:18 两个服务均实际启动并正常结束：Result=success、ExecMainStatus=0。oneshot 结束后的 ActiveState=inactive 是正常状态。
两个 timer 均 active/enabled，有后续计划。Kaggle 自动 timer 只有 ai-news-kaggle-recovery.timer。
未额外启动 lane 或手工提交 GPU job；未修改共享锁、配额门槛、模型或预算。
vendor service 按现有到期规则执行；本次检查的 RSS 是持久快照，未把快照 HTTP 200 说成新的强制上游抓取。

以下 8 个用户服务全部 active：
ai-news-web、ai-news-miniflux、ai-news-postgres、ai-news-proxy、ai-news-rsshub、ai-news-x-feed、ai-news-x-provider、remote-desktop-commander。

所有 shell 执行均设置了总超时；子命令、HTTP 和浏览器等待另有超时。端口以 sudo ss -lntp 检查，进程以 /proc 的实际命令和 cgroup 检查。

## 12 源与数据完整性

逐一 GET http://127.0.0.1:8092/internal/vendor-feeds/<key>.xml：全部 HTTP 200、XML 可解析、item > 0。
Miniflux API 返回原 ID 62–73，feed_url 为对应 8092 新地址，parsing_error_count 全部为 0；没有重复 URL。

| 来源 | Feed ID | HTTP | RSS item 数 | 解析错误 |
|---|---:|---:|---:|---:|
| nxp | 62 | 200 | 12 | 0 |
| quectel | 63 | 200 | 150 | 0 |
| nidec | 64 | 200 | 38 | 0 |
| ti | 65 | 200 | 102 | 0 |
| renesas | 66 | 200 | 11 | 0 |
| kickstarter | 67 | 200 | 16 | 0 |
| adi | 68 | 200 | 29 | 0 |
| microchip | 69 | 200 | 6 | 0 |
| nordic | 70 | 200 | 167 | 0 |
| espressif | 71 | 200 | 6 | 0 |
| maxon | 72 | 200 | 8 | 0 |
| onsemi | 73 | 200 | 7 | 0 |

旧 552 条文章按基线逐条核对 id / url / published_at / status / starred，全部一致。
6520 条 done 分析按部署前哈希逐条核对 result / prompt_hash / content_hash / model / analyzed_at，全部一致。
哈希使用 UTF-8 SHA256(json.dumps([result, prompt_hash, content_hash, model, analyzed_at], ensure_ascii=False))。
非 meta 配置未改变；正常运行中的后台 heartbeat / discovered_at 更新时间单独记录，不当作模型或预算变更。
原有已跟踪 WIP 的 git diff --binary SHA256 在操作前后相同；没有清理、覆盖或提交其他 WIP。

## 公网浏览器验收

使用真实 Chromium 访问 https://106.53.40.6/inbox。
桌面 1440×1000 和手机 viewport 390×844 独立上下文，20/20 通过：
登录进入今天、AI 设置 · 来源、来源目录搜索 NXP、已订阅与新订阅地址、资源看板、
NXP feed 文章、正常打开文章详情并显示既有 RSS 内容、与 API 原 URL 一致的原文链接、返回同一 feed、无横向溢出、无 JavaScript pageerror。
文章 4496 / 4497 原阅读状态已恢复并回读验证，收藏状态未改变。
截图复核发现这两篇 NXP 文章在阅读器内仍显示需要全文适配提示；旧 n8n 工作流也只输出发布时间说明和原文 URL，未丢失已有正文。
不能将该详情页检查表述为完整原文抓取通过。两篇原文地址经现有出站代理实际 GET 均返回 HTTP 200（约 61 KiB HTML）；完整原文在阅读器内的适配是既有遗留问题，本次未修改。
浏览器脚本、JSON 和四张截图保存在私有证据目录，未覆盖之前的验收 artifacts。

## 资源快照

| 指标 | 停用前 | 验收后 |
|---|---:|---:|
| n8n 两进程 RSS | 148348 KiB（144.9 MiB） | 0 |
| n8n 两进程 VmSwap | 447096 KiB（436.6 MiB） | 0 |
| MemAvailable | 909500 KiB（888.2 MiB） | 1129380 KiB（1102.9 MiB） |
| 全系统 Swap 已用 | 1067008 KiB（1042.0 MiB） | 698508 KiB（682.1 MiB） |

全系统资源会随其他服务变化，不能把总 Swap 差值等同于 n8n 单独占用值；未重启服务器或执行 swapoff。

## 证据与回滚

原完整备份：/home/ubuntu/ai-news/.private/n8n-retirement-20261001T093550。
本次最终证据：/home/ubuntu/ai-news/.private/n8n-retirement-20261001T093550/retirement-final-20261001T101635。
包含 stop-command.json、after-stop.json、replacement-runs.json、data-after-stop.json、data-final.json、
resource-before.json、resource-after.json、wip-before.json、wip-final.json、browser-after.json、browser_check.py 和截图。
秘密、数据库和私有证据不提交 Git。

故障回滚按 N8N-SOURCE-MIGRATION.md / N8N-SCHEDULER-MIGRATION.md 执行：
先停止新 timer，重新 enable --now news-n8n.service；确认旧 RSS 后按 feed-cutover/manifest.json 回改 URL 并恢复旧调度入口。
不通过重建订阅、删除数据或全量重评分修复。

未验证未来 24 小时稳定性、实体手机或 Safari；Chromium 手机 viewport 验收不代表实体设备验收。
