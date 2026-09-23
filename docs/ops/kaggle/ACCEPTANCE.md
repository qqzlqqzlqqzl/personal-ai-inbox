# 交付验收记录（2026-09-24）

已部署至 `/home/ubuntu/ai-news/src/kaggle_batch`，云端提交 `9f95bdd861f071e40875bcf6612c014ff818f7ad`，分支 `codex/kaggle-qwen36-batches`。按需运行入口及回滚说明见 [RUNBOOK.md](../../../src/kaggle_batch/RUNBOOK.md)。未启用定时任务，未开启旧付费 worker。

| 要求 | 实际证据与结论 |
| --- | --- |
| 只比较 Q4_K_M / Q5_K_M | 两份 versions 固定文件、SHA256、Dataset；Q5 实际为 UD-Q5_K_M，Q4 上游字节身份未证明，已披露 |
| 复用权重和编译产物 | 四个主批次 model_source=attached_dataset；runtime SHA256 固定；无本轮重下载/重编译 |
| 双 T4、全部层 GPU | 四份主批次 metrics 的 devices、41/41 offload 和 server.log；记录每卡峰值及 RAM |
| 64K、开启思考 | observed_contexts=65536，27/27 请求有 reasoning；并发服务 2 个槽位各 65536 |
| 同源公平对照 | 串行同一 v1/v2 提示组及并发同一 source-fidelity-v2 组分别对比；23 篇评分、23 卡片，不能跨轮把提示变化算成量化收益 |
| 数万字文章 | 三篇 35,287～36,511 字符全文输入，实际约 7K～9.5K tokens，无正文截断 |
| 8K/16K/60K | 两模型单请求诊断全部通过；两条 59998-token 并发请求各自完成，无截断；合成召回不冒充真实文章质量 |
| 上限影响对照 | q4/q5-context-allocation.json，8176 输入且同模型输出长度相同，每组各两次；耗时差约 4.72%/2.58%，样本限制明确 |
| 结构、引用、保真复核 | Q4 并发 27/27、Q5 25/27；两组均发现语义错误，错误原批结果不直接导入。小批次经复核/修复后导入 |
| 重复提交 | 控制器测试及云端隔离恢复验收确认重复 submit 未发请求；immutable ID 不重复启动 GPU |
| 观察超时 | test_observation_timeout_keeps_running_job 与 deadline 测试使用受控时钟，保留 RUNNING，不将超时视为远程停止；这是控制器测试，不宣称自然发生的远程超时 |
| 实际推理中断 | interruption-recovery.json：首条落盘后 SIGKILL，远程 ERROR、returncode=-9、首条保留；只重跑两条缺失，COMPLETE 且 2/2 校验通过 |
| 回传失败 | cloud-acceptance.json：隔离目录注入不完整传输，再实际重下已完成的 Kaggle 输出，3 条验证通过、零 push；注入故障与真实下载分开标明 |
| 诊断不得导入 | import guard 在打开业务数据库前拒绝 must_not_import；真实诊断和恢复均保留该标记，未写入产品 |
| 云端独立执行 | 云端提取和提交；独立于 SSH 的收集 PID 952886/957489 自行 sleep、回传、校验、退出；云端直接做版本核对、备份及导入 |
| 幂等导入 | 两篇评分和两张卡片首次 imported，再次 already_imported；旧错误翻译明确拒绝；原批通过账本覆盖证据标为 resolved |
| 产品实际呈现 | 正式 HTTPS API 字段匹配；云端真实 Chromium 两张卡片中文标题/简介、详情正文/证据均通过；截图已查看，阅读状态已恢复 |
| 每日 200+ 容量 | RUNBOOK 表格分开评分/卡片、思考及每批启动；Q4 29.93～30.45h/周、Q5 35.43～35.99h/周，未测平台开销明确排除，不保证单账号 30h 足够 |
| 6h/12h 调度关闭 | timer 参数单元测试；正式配置 schedule_enabled=false，实际 smoke 返回 schedule_disabled / gpu_started=false；未安装启用 Kaggle timer |
| 备份、测试、凭据 | 云端代码归档与 SQLite 完整性校验；49 批处理测试、72 原项目测试；secret audit 459 文件零发现；只提交指定新源码与 handoff |

## 核心批次

| 用途 | ID（owner 均为 qogirqogir） | 终态 |
| --- | --- | --- |
| Q4 串行 64K | qwen-inbox-ee2b13c9b865dc81b5a34da7 | COMPLETE |
| Q5 串行 64K | qwen-inbox-0d42cfe93921f68970f49c3f | COMPLETE |
| Q4 16K 分配对照 | qwen-inbox-9009f344042db47d91ef837e | COMPLETE |
| Q5 16K 分配对照 | qwen-inbox-40ac519d58085c1ed85231f6 | COMPLETE |
| Q4 并发 64K | qwen-inbox-fa7ce014c6637f197e92eec2 | COMPLETE |
| Q5 并发 64K | qwen-inbox-c200cdacdc6ca34dc0fa2e47 | COMPLETE |
| 故意中断 | qwen-inbox-d764f33a1a75e9d93ac8eb35 | ERROR（预期） |
| 只补缺失项 | qwen-inbox-8934b39987035eaa55ec7ae4 | COMPLETE |
| 云端业务小批次 | qwen-inbox-532c8fb924bcfa4ca5417d04 | COMPLETE / resolved |
| 云端翻译修复 | qwen-inbox-d70b17b474305436dcf9dddc | COMPLETE / imported |

公开汇总：q4/q5-64k-metrics.json、q4/q5-parallel-64k-metrics.json、两个 context-allocation.json、interruption-recovery.json、cloud-acceptance.json。私有原文/原始输出在忽略目录 jobs、cloud-pilot-output、cloud-repair-output，未进入 Git。旧 16K/思考关闭的历史性能不参与本次产量结论。

## 不应误读的边界

这次证明了可运行、可恢复、可导入和实际显示，**没有证明模型逐条事实完全可靠或单账号稳定承载每天 200+ 篇**。更高位宽没有消除误译；硬件适配通过和业务可靠性是两件事。默认选 Q4 是本次速度/显存结果支持的选择，保留 Q5 固定版本可复测。第二账号提交仍失败，未把两张 T4 或同账号两个任务说成两个可用账号。
