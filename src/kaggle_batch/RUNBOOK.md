# Kaggle Qwen 按需批处理

业务链路已用真实文章跑通：云端提取 → Kaggle 提交 → 模型推理 → 云端独立回传 → 校验 → 幂等写入 → 正式 HTTPS API 与浏览器卡片读回。评分和卡片翻译使用现有业务提示，文章全文不截断。原始结果保留，卡片末尾明显未完成句的移除会写入 normalizations。

## 配置与资源

- 正式脚本：`/home/ubuntu/ai-news/src/kaggle_batch/`。
- 配置：同目录 `cloud-config.json`；凭据只引用 `.private/kaggle/access_token`，不复制进源码或 Notebook。
- 默认 Q4_K_M，公开 Dataset `micronic/qwen3-6-35b-a3b-gguf`，固定版本见 `versions-q4.json`。Q5 对照为 `smartmichaelma/qwen3-6-35b-a3b-gguf-q5km` 中的 **UD-Q5_K_M**，见 `versions-q5.json`。
- 每请求 65536 token，两个并发槽位共 131072；双 T4、全部 41 层卸载、思考开启、reasoning_budget=-1，输出预算为剩余上下文。超过上下文明确失败，不截正文。
- 模型和 runtime 均从 Kaggle 已有附件复用，每次核验完整 SHA256，不重新下载/上传权重、不编译。
- 业务 Python 为 `runtime/venv/bin/python`；Kaggle CLI 使用隔离的 `runtime/kaggle-venv/bin/python`。不修改网页服务、端口或付费 API 设置。
- 目前只有 qogirqogir 凭据通过提交验证；备用账号仍 403，不能声称两账号可用。不要通过重复提交绕过配额。

## 按需运行

```sh
cd /home/ubuntu/ai-news
timeout 8000 runtime/venv/bin/python src/kaggle_batch/cloud_cycle.py --config src/kaggle_batch/cloud-config.json --manual-recovery --batch EXISTING_BATCH_ID
```

这是明确授权的已有批次恢复，只能处理指定 ID，不 prepare、submit 或继续 drain 新工作。旧 `--manual` 宽泛绕过已关闭。新工作只能在严格 `schedule_enabled=true` 且无 pause 时走自动入口。按 source_refs 保存输入版本，已完成评分不重算，可处理单独的翻译积压。新手灰度先将 `batch_limit` 设置为 3，再运行一次。不要同时从其他目录另起同一生产队列。

周期内相邻检查 sleep 660 秒；恢复已有任务也先等 660 秒。外层观察超时不等于 Kaggle 终止，下次仍处理原 ID。程序从云端独立执行；需要脱离 SSH 时由云端进程管理器执行同一命令，不由桌面持续轮询。

需要分步复核时：

```sh
timeout 3600 runtime/venv/bin/python src/kaggle_batch/cloud_bridge.py --config src/kaggle_batch/cloud-config.json prepare --limit 3
timeout 660 runtime/venv/bin/python src/kaggle_batch/cloud_bridge.py --config src/kaggle_batch/cloud-config.json advance --batch BATCH_ID
```

第一次 advance 只提交 prepared 批次；后续 advance 在终态时下载、校验并导入。**自动校验不等于事实完全正确**。灰度/人工复核时先用 Controller 的 status/download 和 validate_business.py，不要用 advance 越过复核环节。来源变化、引用不匹配、缺失或失败项不会强行变成成功。

## 结果、失败与恢复

状态位于 `state/kaggle-batches/batches.sqlite3`，每批次目录保存不可变 manifest、runner、原始 output、verified-results、business-validation、import-report 和导入前 SQLite 备份。该目录私有且不进 Git。

| 状态/问题 | 处理 |
| --- | --- |
| submitting / submit_unknown | 先按同一 ID 查官方状态；禁止因本地超时另起 GPU |
| RUNNING / QUEUED | sleep 至少 660 秒后再查；不重新 push |
| 下载中断 | 重跑 download 同一终态批次；不会重新推理 |
| 缺失/格式错误 | 下载核验后用 retry，只包含失败或缺失项 |
| 人工发现不忠实翻译 | 保留原始输出，拒绝该组；以明确 invalid_ids 生成修复，不重评分 |
| source_changed / prompt_changed | 保留现有数据，重新取当前输入，不覆盖新版来源 |
| 诊断批次 | must_not_import 阻止导入，即使模型返回成功 |
| 观察时限结束 | 保留同一批次并恢复观察，不能冒充远程任务结束 |

在生产目录运行控制器：

```sh
timeout 240 runtime/venv/bin/python src/kaggle_batch/batch_control.py --root state/kaggle-batches --owner qogirqogir --kaggle-python runtime/kaggle-venv/bin/python download BATCH_ID
timeout 60 runtime/venv/bin/python src/kaggle_batch/batch_control.py --root state/kaggle-batches --owner qogirqogir retry BATCH_ID
```

CLI 操作前应在同一进程环境中设置 KAGGLE_API_TOKEN 为私有 token **文件路径**，不要把 token 内容写入命令。重复 submit 同一不可变 ID 不再次 push。修改提示/runner 会产生不同 ID，必须先确认旧批次终态。

修复批次导入后，可用 `cloud_bridge.py ... resolve --batch ORIGINAL_ID --replacement CHILD_ID` 关闭原任务。resolve 核对同模型、原始 source_refs 与每项实际导入账本，只有全部覆盖才标记 resolved，不把被拒绝原结果标记为已导入。

停止本地观察器不会停止 Kaggle；远程批次有有限 session_timeout 和自身服务清理。不要为了停观察器终止网页、Miniflux 或其他项目服务。

## 6h / 12h 调度（保持关闭）

配置支持 `interval_hours=6` 或 `12`。`schedule_enabled=false`、缺失或非布尔 true 时，自动入口在 provider/service admission 前退出，包括旧批次恢复。长周期、子进程和 provider 边界重新核验 exact config 与 pause；停止返回不重置 recovery。明确 `--manual-recovery --batch ID` 只恢复已有非 prepared ID，并保留 cooldown；共享旧表兼容初始化仍可执行，但只补齐目标 ID 的 claims，不补其他 pending ID。`--reconcile-readonly` 观察自身账本，无 Controller、凭据、provider、schema migration 或逻辑数据写入；SQLite 的 mode=ro 仍可能创建/更新 WAL/SHM 协调 sidecar，真实只读文件系统可能 fail closed，不使用可能漏读 live WAL 的 immutable 模式。模板位于 `ai-news-kaggle.service.example`；下列命令仅输出 timer 文本，不安装或启用：

```sh
timeout 20 runtime/venv/bin/python src/kaggle_batch/cloud_cycle.py --config src/kaggle_batch/cloud-config.json --render-timer
```

只有另行决定启用时，才安装 user service/timer 并修改开关。配置关闭与 timer 是否 enabled 是不同状态；本源码修复未查看或改变生产 timer。Month UI enabled 使用 effective schedule/pause；停止时额度仅读缓存。新 month service 模板没有 --manual，需要根独立审核后部署。参见 `docs/ops/kaggle/scheduler-stop-review.md`。

## 已测质量与额度限制

| 相同 23 篇评分 + 23 卡翻译，64K/思考/双并发 | Q4_K_M | UD-Q5_K_M |
| --- | ---: | ---: |
| 业务阶段墙钟秒数 | 1739.21 | 2062.44 |
| 结构及引用校验 | 27/27 | 25/27 |
| 两卡显存峰值 MiB | 11803 / 11281 | 14199 / 13687 |
| 每日 200 篇评分 + 200 卡翻译，每天两批估算小时/周 | 29.93 | 35.43 |
| 每天四批估算小时/周 | 30.45 | 35.99 |

两组均通过约 8K、16K、60K 输入及两条 60K 并发召回。Q4 在本样本更省时/显存，Q5 未呈现确定质量收益；权重来源、UD 实现与聊天模板不同，不是严格的纯位宽消融。评分和翻译仍有事实漂移，结构检查通过不保证语义正确。实际产品小批次采用复核、拒绝错误组、选择性修复后导入。

额度估算包含思考和权重核验/加载，**不含平台准备、挂载、保存和失败重试**。单账号每周 30 小时不足以稳妥承诺该产量；Q4 两批估算只是贴近上限，不能据此启用高负载调度。平台总周转远长于部分脚本耗时，不能混为一谈。

同一 8176-token 输入、每组两次：64K 上限相对 16K 上限，Q4 平均耗时 +4.72%，Q5 +2.58%。样本小且不同会话，不能保证完全等速；主配置仍保留 64K。

## 验证与回滚

在脚本目录运行 `timeout 120 /home/ubuntu/ai-news/runtime/venv/bin/python -m unittest discover -p 'test_*.py'`。生产项目自身测试和 secret audit 另行执行。真实 GPU、故障注入、产品 API 和 Chromium 证据与单元测试分别记录。

更新前保留 Git 代码归档与 SQLite 在线备份，部署只增加本批处理目录。代码回滚用明确部署提交的 git revert，并保留 state 和批次证据；不用删除目录、不清空模型附件、不覆盖整库。已经导入的业务数据若确需回滚，应先另做当前备份，核对后按原项目 SOP 的数据库恢复步骤执行，不能自动恢复旧整库覆盖后来操作。

## Required lane ledgers and first installation

New dispatch fails closed with `state=dispatch_blocked`, `recovery_error=local_state`,
a fixed `reason` and ordinal `peer_N` when any required ledger is missing,
unreadable, damaged, incomplete, or contains unknown states, invalid IDs or
orphan claims. Reports contain no ledger paths, manifest contents or provider
errors. `state_root` is always required, including when `peer_state_roots=[]`.
The existing `kaggle-month-{primary,secondary,third,fourth,fifth}` layout requires
all five sibling roots even through the standalone Controller CLI; the scheduler also checks all five lane configurations
agree on the complete topology. Additional explicitly configured peers remain
required by each bridge. A retry time permits another check, never claim release.

Claims are read with `mode=ro` in a single transaction per ledger and connections
are explicitly closed. This provides one consistent snapshot for each ledger;
it is not a distributed atomic snapshot of all five databases. Dispatch checks
are made under the existing coordination lock before extraction, after extraction
reacquires that lock, and before manifest publication. Prepared submission and
scheduler service startup recheck required ledgers. A blocked scheduler starts
zero lane services, including recovery services that could otherwise drain new
work. No automatic ledger repair, recreation or retirement occurs.

Valid transactional claims remain authoritative when a parked manifest is missing
or corrupt. Legacy ledgers without `batch_claims`, or batches with no claim rows,
need a complete manifest with matching batch ID, stored/canonical hash, unique
item IDs and positive integer source references. Only `imported`, `retired` and
`resolved` release claims. Existing submitted/running/uncertain batches can still
be reconciled explicitly by the same ID when another peer is blocked; the normal
same-ID recovery/absence-proof policy is unchanged. A recovered worker must pass
the required-ledger gate before starting another batch.

Normal Controller construction and all subsequent writes require an existing
DB with `mode=rw`. First installation is a separate **init-only** action:

```sh
python src/kaggle_batch/cloud_bridge.py --config /path/to/fresh-lane-config.json init
# Or initialize a standalone fresh root:
python src/kaggle_batch/batch_control.py --root /path/to/fresh-root --owner OWNER init
```

These are documentation examples, not deployment commands. Initialize every fresh
required lane before dispatch. Initialization does not read tokens, invoke a
provider, extract articles or import results. An existing valid ledger is
idempotent; a corrupt ledger is rejected. A root without its DB but with any
existing evidence (including batch folders, SQLite sidecars, recovery records or
locks) is rejected as `initialization_evidence`. Missing existing state requires
operator recovery of the ledger, not init. Existing SQLite schemas are retained;
this change adds no tables, columns or migrations.

`local_state` uses the existing 660/1320/2640/3600-second capped retry policy.
The bridge failure is counted once by its supervising cycle. The scheduler reads
its own dispatch recovery record before any ledger, service or quota work; before
`retry_at` it returns a fixed `local_state_cooldown` block with zero starts and
without incrementing failures. At expiry it must complete topology/ledger
validation and the final pre-start recheck before clearing that record. Damaged
recovery JSON or non-object/counter values produce a fixed `invalid_recovery`
block, and failure recording safely resets malformed metadata. These are
infrastructure counters; article attempts and unknown claims are unchanged. Tests in
`test_required_ledgers.py` use temporary state and synthetic/mock providers,
credentials, extraction, HTTP boundaries and systemctl; no GPU or production
acceptance is implied by these tests.
