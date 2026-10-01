# Kaggle 调度迁移（#52）

唯一自动入口为 ai-news-kaggle-recovery.timer → recovery_watchdog → lane_scheduler.tick。
不新增第二个 6 小时定时器；保留同一把 scheduler.lock、暂停标志、五 lane 配置、<=1h 额度门槛、未完成批次及既有评分。
内部 /internal/kaggle-month/status 和 POST start/pause/resume 仍使用 X-Vendor-Refresh 鉴权。
暂停不取消已经提交的有限 GPU 任务，恢复先核对批次状态，禁止盲重提。

## 切换与回滚
1. 在私有备份目录用 SQLite backup 保存 n8n 数据库、原服务/定时器；备份不得提交 Git。
2. 将本 PR 的两个 deploy/systemd 文件安装到 ~/.config/systemd/user；daemon-reload，enable --now recovery.timer。
3. 保留 12 信息源期间，只停用 n8n 的 kaggleMonthControl 工作流；使用 n8n 支持的管理接口/离线 CLI，不能在活服务下直接改库。
4. 信息源迁移完成可整体 stop/disable news-n8n.service；n8n 数据与共享 Node 不删除。
5. 观察真实 timer tick、status 鉴权与暂停门槛。运行测试只使用 mock，不能以测试触发付费模型或重复 GPU 提交。

依赖暂时不可用输出 dependency_unavailable，退出 1，systemd 30 秒后重试，180 秒内最多 3 次；后续 11 分钟 timer 继续恢复。
未知异常输出 scheduler_error，保留失败状态，不伪装成功；日志只写错误类别，避免泄露密钥。
回滚前先停 recovery.timer，恢复服务文件与旧 n8n 工作流状态，然后再恢复唯一自动入口，避免双重调度。
