# cloud-maintenance（自动恢复）

在这台服务器上以 `ubuntu` 用户运行的运维脚本：定时做健康检查，服务挂了就有限次地自动重启。2026-11-12 起纳入版本管理（GitHub `personal-ai-inbox` 仓库的 `ops/cloud-maintenance` 分支），之前只存在于服务器上。最后核对：2026-11-24。

## 在跑什么

| 单元 | 频率 | 脚本 | 做什么 |
|---|---|---|---|
| `cloud-recovery.timer` | 每 5 分钟 | `recovery_agent.py --apply` | 检查 Miniflux、RSSHub、信息箱网关、New API 的健康状态；同一服务连续 3 次失败才重启，且每小时最多 2 次、两次之间至少隔 10 分钟；重试解析连续出错 ≥3 次的订阅；轮转 New API 日志；只读查看 Kaggle 队列和厂商来源状态 |
| `cloud-repair-rollback.timer` | 上一轮结束 5 分钟后 | `repair_guard.py --recover` | 两次固定数据修复如果中途被打断，按补偿日志回滚 |

`systemd/` 里是这两个单元及 drop-in 的副本，和 `~/.config/systemd/user/` 中实际安装的一致。

同一台机器上的 `~/server-monitor` 只负责记录和通知，不做重启；重启都由这里负责，两者不冲突。

## 已停用的部分

`ops_agent.py`、`ops_review.py`、`ops_protocol.py`、`ops_runbooks.py`、`production_cases.py` 是 “Qwen 运维” 闭环：Kaggle 上的模型审阅运维证据，服务器执行白名单内的动作。**2026-11-12 已停用**（`~/ai-news/.private/recovery-agent/qwen-settings.json` 中 `"enabled": false`），上面的规则化恢复照常工作。`repair_guard.py` 仍然导入 `ops_runbooks` 和 `production_cases`，所以这些文件不能删。

## 不在版本管理里的东西

- `security-20261023/`：2026-10-23 安全升级的产物。**其中一部分正在线上使用**：`go-candidates/`（Miniflux、mihomo、X 抓取的二进制）、`desktop-commander-candidate-v2/`、`rsshub-candidate-v2/`，相关单元的 drop-in 直接指向这些路径，不能删。其余是升级前的数据库导出、venv 归档和审计记录，留作回滚。升级说明见其中的 `maintenance-report.md`。
- `backups/`、`newapi-metrics-syscalls.trace`：root 所有，`ubuntu` 用户读不了。

## 常用命令

```bash
systemctl --user list-timers cloud-recovery.timer cloud-repair-rollback.timer
journalctl --user -u cloud-recovery -n 50 --no-pager
```
