# 服务器审查与修复交付报告

日期：2026-09-27；时区：Asia/Singapore。

## 结论与范围
Kaggle 已从固定 4337 篇历史名单切换为“当前启用订阅源的全局增量队列”。上线检查纳入 6515 篇已有文章，其中 2178 篇原本不在历史名单；两个实际提交批次各含 20 篇名单外文章。新文章经发现任务入库后，无需人工追加名单即可参与调度。
信息箱和 NewAPI 的应用层修复已部署并完成回归。仍有两项主要未完成事项：Nginx 加固补丁未应用；NewAPI 二进制内 7 个 Go 依赖版本匹配公开安全公告，需要隔离重建与兼容性验证。本次检查不能证明服务器不存在其他漏洞。
本次检查覆盖服务监听、Nginx 配置、systemd 单元、关键 FastAPI 路由、Kaggle 调度/准备链路、配置持久化、阅读埋点、X 适配、预览抓取、NewAPI 沙箱/参数/备份、相关测试及正常浏览器流程。未逐行审计 NewAPI 完整上游源码，未执行破坏性渗透或负载压测，未调用收费推理接口。
依赖检查包含信息箱 Python 环境、NewAPI 内嵌 Go 模块信息、若干顶层 npm 包，不等于所有虚拟环境和全部传递依赖的扫描。博客入口实际为 /blog 跳转科创社区，并非独立博客服务；不包含对科创社区的审计。

## 已部署的修复：信息箱与调度
| 项目 | 原问题 / 风险 | 本次处理 |
|---|---|---|
| 候选范围 | 调度器、执行准备器均受固定 allowlist 限制 | 新增 live_scope.py，读取启用订阅源，按指定用户选取文章，两层同时切换 |
| 历史恢复 | 扩范围可能影响在途批次 | 保留历史名单、不可变 manifest、claim/lease、幂等导入和既有重试 |
| 状态展示 | 历史截止日期与旧 Token 预算容易误导 | 连续队列不设日期截止；历史快照单列；主链路显示 Kaggle 增量状态 |
| 管理权限 | 全局配置仅检查登录 | 全局设置、工具、全局状态、订阅管理要求管理员 |
| 请求限制 | 单看 Content-Length 不覆盖实际流式正文 | 增加实际正文累计上限 2 MiB；现有 Nginx 64 KiB 上限尚待另行调整 |
| 失败节流 | 缺少应用层连续鉴权失败限制 | 每来源五分钟内 30 次 401 后暂时返回 429；状态表有容量上限 |
| 阅读会话 | 缺少文章/session 归属检查，时长可超过经过时间 | 验证用户、文章、会话绑定，要求先 open，时长不超过服务器观测时间 |
| 滚动深度 | 正文未加载、滚动范围为零时直接算 100% | 等待实际正文加载与可测量后累计 |
| 离页与时长 | 休眠造成跳增，离页请求可能丢失 | 限制时间跳增、前台/焦点/空闲检测、顺序提交、离页 keepalive |
| 配置写入 | 并发覆盖，失败被静默吞掉 | SQLite 事务内合并；前端顺序写阈值，显示保存中/成功/失败，防初始化覆盖刚选值 |
| 处理冲突 | 误启用旧 API 工作者与 Kaggle 重复处理 | 连续 Kaggle 接管时禁止同时开启旧 API 分析/翻译，标明备用配置 |
| 网关重启 | 原启动逻辑会重置其他进程的处理中状态 | 不再重置 Kaggle 的 fetching/analyzing；仅旧工作器启用时恢复其超时状态 |
| X 缓存 | 错误记录/空结果覆盖已有帖子 | 识别错误记录；上游空结果保留非空旧缓存并标记 |
| X 封面 | 已有原帖媒体仍反复访问 x.com | 优先原帖媒体；纯文本不再反复抓页面找封面 |

每批上限 20，不要求凑满。已完成评分不重跑，待翻译与可重试项按原规则处理。文章发现、调度轮询、GPU 排队仍会带来延迟；不承诺每篇到达立刻完成。未取消既有远端 GPU 作业。

## 已部署的修复：服务与 NewAPI
| 项目 | 处理与边界 |
|---|---|
| RSSHub 监控 | 9464 从全部网卡改为 127.0.0.1；关闭调试信息。未断言此前云安全组一定放行 |
| 私密文件 | NewAPI 备份/密钥目录 0700、文件 0600，修正 9 项权限偏差 |
| 公网地址 | ServerAddress 从空值改为实际 HTTPS 地址，不再显示 localhost:3000 |
| 既有防护 | 保持关闭公开注册；显式固化既有 SSRF 防护和禁止私网抓取；仅信任 loopback 代理；保持 TLS 验证和关键操作限流。不是声称修复了已证实的 SSRF 利用链 |
| 日常备份 | 新建每日 04:35 的一致性 SQLite 备份任务，quick_check 校验，保留七份完成备份；已成功执行一次 |
| 安装工具 | 信息箱 pip 24.0 升至 26.2.1；官方 PyPI wheel 核对 SHA-256；OSV 复查没有匹配 |

## 验收结果
| 检查 | 结果 | 证据文件（evidence/） |
|---|---|---|
| 信息箱单元测试 | 134 passed | final-inbox-tests.txt |
| 调度、范围与重试测试 | 40 passed，另 2 subtests passed | final-scheduler-tests.txt |
| 签到模块隔离模拟测试 | 8 passed，无实际签到请求 | newapi-checkin-tests.txt |
| 完整前端构建 | 172 文件发布、54 预压缩资源 | frontend-build.txt |
| 信息箱登录、阈值刷新 | 通过；恢复测试开始时的阈值 | browser-review.json |
| 阅读统计 | 未滚动约 9.2 秒、0%；滚动后约 21.8 秒、60%；close 已保存 | browser-review.json |
| 收藏 | 原收藏控件存在；本轮未专门验证收藏写入/跨设备同步 | browser-review.json |
| NewAPI 浏览器登录 | HTTP 200，进入 dashboard/overview，无捕获到的 JS 错误 | newapi-browser-review.json |
| 新文章实际提交 | third、fourth 各 20 篇名单外文章处于 submitted；不是完成评分回执 | live-batches.json |
| 服务状态 | 信息箱、Miniflux、RSSHub、X 两服务、调度 timer、NewAPI、备份 timer 均 active | final-service-check.json |
| NewAPI 业务配置 | 2 个启用渠道、122 个模型未改变；无收费推理调用 | final-service-check.json |
| 公开入口基线 | 保护接口 401，内部/私密路径 404，首页正常 | public-baseline.json |
| 已读取日志 | 查询参数凭证样式匹配为 0，不代表全部历史日志都不存在泄漏 | log-query-review.json |

首次全套测试中两项 X 测试还依赖旧 RSSHub 接口，结果为 132 pass / 2 fail；更新到当前 x-cli/Atom 契约后，全套 134 项通过。测试阅读记录只按该浏览器捕获的 UUID 精确清理，未按时间段删除真实历史。旧的误算 100% 数据无法反推实际值，没有篡改。

## 未完成：Nginx 与主机级加固
当前远控禁用 sudo，Nginx 生产文件未修改。仅生成 nginx-hardening.patch 和 nginx-staged/ 内六个候选文件：去除访问日志中的查询字符串、NewAPI 登录入口独立限流、安全响应头与 Cookie 标志、仅转发边缘实际来源地址、将信息箱入口请求体上限与应用的 2 MiB 对齐。NewAPI 多模态接口原有大请求上限不变。
这些是待审补丁，尚未完成 root 权限下的配置测试与部署。应用前必须与当时现网比较、备份、测试，通过后再 reload，不能当作现有防护。
主机观察到 PermitRootLogin=yes、PasswordAuthentication=yes，UFW 未启用；不能据此认定服务器被入侵或云安全组没有限制。需先确认密钥登录和控制台救援路径，再由管理员收紧 SSH 与云安全组。本轮未改密码、密钥、SSH 登录方式或全局防火墙。

## 未完成：NewAPI 内部依赖安全更新
按版本查询 174 个依赖记录，初始 8 个组件匹配公告：pip 加 7 个 Go 模块。pip 已修复；Go 编译器为 go1.27.1，标准库单独查询未返回匹配。
| Go 模块 | 当前记录版本 | 所列公告对应修复版本 / 动作 |
|---|---|---|
| github.com/gorilla/websocket | v1.5.0 | 至少 v1.5.3；WebSocket masking 随机数相关 |
| github.com/klauspost/compress | v1.18.3 | 至少 v1.18.7；s2 相关 |
| go.opentelemetry.io/otel | v1.41.0 | 避开受影响范围，建议至少 v1.44.0；baggage 解析相关 |
| golang.org/x/crypto | v0.52.0 | SSH 相关至少 v0.56.0；过时 OpenPGP 不能仅凭升级认定解决 |
| golang.org/x/image | v0.41.0 | 所列问题对应修复至 v0.45.0；重点验证 WebP/VP8L |
| golang.org/x/net | v0.55.0 | 至少 v0.56.0；DNS 解析相关 |
| golang.org/x/text | v0.37.0 | 至少 v0.39.0；Unicode normalization 相关 |
版本匹配不是外网可利用性证明。二进制只读字符串/符号初筛看到 WebSocket、WebP/VP8L、DNS、Unicode normalization 包路径；若干 s2、OpenPGP、SSH 路径未看到。该初筛不能替代调用图与具体入口验证。
定制 NewAPI 二进制 v1.0.0-rc.40+pool-web.1 未替换。后续需在隔离环境更新依赖，验证数据库、登录、流式输出、Responses、Gemini、图像/视频路由后再部署，避免直接换官方程序丢失现有定制。鉴权、内存上限、Landlock/seccomp、SSRF 设置不能替代依赖升级。
证据：dependency-review.json、dependency-advisories.json、newapi-dependency-triage.json、pip-upgrade.json、pip-post-upgrade-check.json、go-toolchain-review.json。

## 其他产品与架构边界
X guest 是匿名可见窗口，可能按热度采样；有内容不等于完整最新帖子。本轮已纠正成功提示，但未解决上游完整性限制；博客/GitHub/YouTube 备用源也不等于作者全部 X 内容。
Miniflux 为访问本机 RSSHub/n8n，仍有内部网络能力。此次限制管理权限，但未证明全部第三方抓取路径都不存在 SSRF；进一步隔离需统一目标地址/跳转校验以及服务身份和网络边界。
首页、信息箱、NewAPI 同 HTTPS origin，分路径不构成浏览器存储隔离；独立子域名为后续方案，未擅自变更 DNS。
最低分持久化已修复，既有精选查询仍含模型 worth_reading 条件，本轮未改评分规则。阅读时长只是前台行为估计，不是理解程度，也未追踪跳到外站后的停留。

## 交付与恢复
交付目录：/home/ubuntu/server-review-20260927/。证据在 evidence/，本轮相对开始工作区的源码差异在 inbox-review-only.patch，NewAPI 备份保留策略差异在 newapi-backup-retention.patch。Nginx 候选配置明确标记为未应用。
before/ 含旧源码、数据库和配置快照，其中可能有凭证，不得公开或加入分享包。恢复应按变更逐项进行，不能整目录覆盖正在运行的 Kaggle WIP。历史 allowlist、远端批次均保留。
源码提交仅包含本次审查变更与必要的 Kaggle 状态桥接依赖；其他正在修改的 Kaggle 文件、来源目录、工具目录没有批量纳入。NewAPI 本身的目录不是 Git 仓库，其备份脚本变化另存差异文件。

## 外部核验依据
- OSV 按包版本查询：https://google.github.io/osv.dev/post-v1-querybatch/
- NewAPI 环境变量与限流：https://docs.newapi.pro/en/docs/installation/config-maintenance/environment-variables
- NewAPI rc.40 SSRF 配置：https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.40/setting/system_setting/fetch_setting.go
- NewAPI rc.40 代理信任：https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.40/common/trusted_proxies.go
- Go 公告举例：https://api.osv.dev/v1/vulns/GO-2026-6222；完整公告响应在 evidence/dependency-advisories.json。
所有现网数值都是对应检查时刻的快照，而非后续时刻的实时保证。
