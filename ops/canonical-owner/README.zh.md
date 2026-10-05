# Canonical-only owner/status operator v2

用途：围绕精确 e87f4ccef4fc600bafda28b559eed6742e297742 collector，准备一个新具名、有限、只读的身份与既有目标状态观察。它解决既有逻辑 token 路径包含合法 alias、collector 自身 no-follow 拒绝该路径的接入问题。collector 三文件逐字保留；这不是停止、claims、absence 或 Controller 准入的替代实现。

默认运行输出 DRY_RUN、零 provider 请求，退出 2。仅主执行者在本地私有环境、审定后使用下面两项显式模式。云端开发测试未读取真实 token、未执行任何账号/provider 请求。

## 输入与本地绑定

stdin 是单个严格 JSON 对象，不能有重复键、NaN、额外字段；最大 128 KiB。不传 token 值或代理值。字段：

- `attempt_id`：主创建的新 UUIDv4。不得复用旧失败 attempt，不得自动创建另一个 ID 重试
- `evidence_root`：主预先创建的当前 UID 私有目录绝对路径，权限不允许 group/other。程序只在其下新建具名目录和 O_EXCL 文件
- `expected_root_count`：主从完整配置拓扑核明的不同 canonical state roots 数量，严格整数 1–32。通常五 lane 是五个 root
- `sole_executor_no_alias_changes`：必须为 true。表示主声明本轮唯一执行者且不改 alias。这只是执行承诺，不是所有 actor 共同锁、不是无 ABA 证明
- `lanes`：1–5 项，每个固定 lane 名仅一次，每个 binding UUID 仅一次。主可以仅先包含一个 lane；未成功项之后不继续其他 lane

每项 lane 严格包含：

- `config_path`：该 lane 原 JSON 配置的绝对逻辑路径。读取原 `owner`、`kaggle_python`、`state_root`、`peer_state_roots`、`token_file`，不写配置。`kaggle_python` 必须逐字等于本次启动的 `sys.executable`；不要复制 cfg 或换解释器凑通过
- `context`：精确 e87 私有输入，只有 `lane`、`expected_owner`、`target`。lane 为 primary/secondary/third/fourth/fifth；owner 字面匹配原配置，后续 token owner 仍由官方 identity 原始响应证明
- `target`：`binding_id` UUIDv4、账本原 `claimed_ref`（owner/slug）、数值 `ledger_observed_at` 原观察时间。可选 `version_receipt` 必须只有同 UUID `binding_id`、已核非零十进制字符串 `version_label`、数值 `verified_at`。无原版本证据不能构造版本。status 无版本回显，所以仍不宣称 specific submission version 已证明
- `ledger_before`：完整六字段对象 `id,state,remote_status,updated,error,manifest_hash`。id 必须是原 ref 的 slug，state 仅 submitting/submit_unknown，remote_status 必须 null；updated 为原数值时间，error 为原字符串或 null，manifest_hash 为原 64 小写 hex。不得使用新的当前时间替换旧快照字段

operator 只用 SQLite `mode=ro`、`query_only=ON` 和读事务，不导入产品模块、不构造 Controller。每个声明 root 的 batches/claims 全表读取并计算完整有序摘要与数量，最多各 100,000 行，超限直接拒绝，不截断；本目标须真实存在且仍有 claims。原六字段、任意其他 batch 或 claim、数据库 inode、配置内容、root、token leaf 或 alias 图变化使回执无效。`peer_state_roots` 全纳入；已知 kaggle-month 五 lane 布局要求五个 sibling roots 全部声明。

所有递归 alias，包括 readlink target 内再次出现的 alias，记录 lstat dev/ino/uid/gid/mode/size/nlink/mtime_ns/ctime_ns 和 readlink 文本。解析路径父目录记录 dev/ino/uid/gid/mode，目录 mtime/ctime不用于比较，避免把本机其他无关文件/SQLite sidecar 生命周期当作 alias 漂移。常规叶文件包含两个时间。前后重新解析 logical→canonical 并比较。原数据库文件不能 hardlink。token metadata 使用 e87 原私有/no-follow要求，preflight 不读 token 内容。

证据 root/attempt 另持有逐父 NOFOLLOW 打开的目录 fd，创建/读取/写入全相对该 fd，文件 O_EXCL/NOFOLLOW、文件及目录 fsync，每次前后检查原路径和 fd 身份。root 或 attempt 被替换时拒绝；即使在检查与 open 之间交换目录，也不会把私有文件写到替换的 symlink 目标。preflight 将证据目录身份写入私有绑定，execute 拒绝将旧 preflight 复制到新 inode 的目录。a035050 v1 的 leaf-only 检查反例已经保留；此 v2 不接受旧 v1 preflight。

## 两步执行

本机使用配置里的既有解释器及已核安装的 kaggle 2.2.4 / kagglesdk 0.1.37，不安装、升级、改 auth、改代理或 TLS。已有 AI_NEWS_OUTBOUND_PROXY 为本次唯一代理；NO_PROXY命中、标准有效代理不一致、自定义 CA 均拒绝。token canonical 路径仅传给 child 的 KAGGLE_API_TOKEN；不改当前进程全局环境、原 cfg、原 logical alias 或原代理。

第一步：`timeout 90s /原配置/既有/python -I -B /已核/canonical_operator.py --preflight < /私有/operator-input.json > /私有/preflight-summary.json`

必须退出 0、status=PREFLIGHT_OK。它完成全部 path/ledger 绑定，使用合成 token 执行真实固定 SDK 的导入、request 对象及 client enter/exit；socket/DNS/Requests.send 临时禁止，零业务请求。真实 SDK 初始化能力必须在主本机实际通过，不能拿本包合成测试替代。私有 preflight.json 保存输入摘要、路径和完整账本内容摘要；不保存 token 字节、raw provider 响应或完整 claims 行。

第二步：主确认这是获批的新具名观察后，使用同一输入：`timeout 490s /原配置/既有/python -I -B /已核/canonical_operator.py --execute-reviewed < /私有/operator-input.json > /私有/execution-summary.json`

执行前 O_EXCL 创建 execution.marker，先消耗该具名 attempt。每 lane 开始前另写 started marker，最多一个 child，最多 identity 1 + status 1，总最多 10 个业务尝试。不重试、不跳过拒绝项、不重定向、不换 token/路线。SDK/路径或任何阶段失败都保留 marker，旧 attempt 不能再跑。程序不在不同新 UUID 间提供持久全局预算；主必须把这一份最多五 lane 输入当作本次唯一具名授权清单，不能循环换名字重试。

原 e87 identity 必须原始 HTTP 200、active=true、username 与 expected_owner 字面一致；status 必须原始显式合法 canonical 枚举，不能模型默认 QUEUED。HTTP 403/404、缺失字段、未知状态仅 UNRESOLVED。每次 connect/read timeout 为 5/15 秒；child 75 秒；operator 自身 480 秒总 deadline。超时只保留可能请求上界，不伪称远端收到零请求。

## 结果解释与持久证据

仅顶层 status=OBSERVATIONS_COMPLETE 且 final_outer_binding_verified=true，以及对应 lane=OBSERVED_CANONICAL_ONLY 才代表本次冻结 canonical 对象观察成功。lane 私有 receipt 是 provisional，必须与同 attempt 最终 result.json 一起读；最终漂移会把此前 lane 结果统一作废。REFUSED/STOPPED_UNRESOLVED 不可用于恢复动作。

输出只有固定 lane 标签、UUID、计数、有限状态/原因/时间，不输出 username/ref/token/代理/标题/失败文本。无效 child stdout/stderr 不落盘；只保留安全固定错误类别。路径、cfg/root 摘要和原六字段位于主私有 preflight 文件，禁止上传。execution.marker 和 lane started marker 在中断时仍保留。某些异常导致 result.json 未写成时，stdout 的 REFUSED 与已有 marker 是失败证据，不能采用孤立 provisional lane receipt。

`provider_requests=null` 表示只知道可能业务尝试上界；有效 child 的 request_counts 表示进入受限 send 次数，不保证 provider 已收到。失败有 `business_attempt_upper_bound`；未启动 lane 不消耗 RPC，但顶层 reserved_business_attempts 仍保留本次上限。

所有结果恒有：canonical_only=true；logical_lane_continuity_verified=false；aba_excluded=false；cas_admission=false；claim_transition_allowed=false；submission_allowed=false；absence_proof=false。没有共同 alias 维护锁时，前后相同不能证明观察窗口内没有 ABA。主的 sole-executor承诺不升级此结论。身份与状态观察之后，任何同 id 恢复、版本对应、原 manifest/runner 和六字段 CAS动作需要另审；本 operator 不写业务状态。

## 离线证据范围

作者 40 项合成测试通过，含真实本地双表 SQLite、六字段/任意 claims/root/config/token漂移、递归 alias、局部可观测 ABA、marker重放、五 lane 预算、超时、不泄漏、时间顺序、最终失效、证据父目录交换与固定 e87 字节。岗6原目录替换探针仅适配 fixture repo 路径，在原 v1 exit1，在 v2 exit0，断言逐字不变。所有测试生成文件保留。测试使用 fake child / mock sdk_preflight；没有声称在云端真实完整 SDK/账号流程通过。

运行测试：`timeout 120s python -B tests/test_canonical_operator.py`。可设置 OPERATOR_TEST_RETAIN_ROOT 为本岗新目录；默认保留在候选目录之外。不要复制或上传测试运行生成的 synthetic token 文件、私有快照或原日志。分享仅审过源、本文、manifest 和安全汇总。原 e87 独审补件与本 operator 独审是两个范围，必须分别绑定。
