# 云服务器出站代理

状态：2026-09-22 已部署并接入业务。两份订阅刷新、生产出站和隔离故障切换已有实测；控制器未认证拒绝及私密权限已核验；节点全部不可用行为未演练。X 网络已通，但尚未取得帖子。详见 [生产报告](../../artifacts/proxy-acceptance.json)。

## 范围与配置

用户已授权在云服务器部署 mihomo，使用电脑现有“凌云”40 节点及 yahahasub.date 8 节点的两份订阅，实现自动故障切换。电脑侧配置仅只读获取，不调整其运行状态。服务器实际加载为凌云 40、Yahaha 8；本次健康检查分别为 2、8。不在文档、日志或仓库中保存订阅 URL、节点凭据、控制器 secret。

| 项目 | 约定 |
| --- | --- |
| 核心 | mihomo v1.19.31；runtime/mihomo/mihomo |
| 服务 | ubuntu 用户 systemd 服务 ai-news-proxy |
| 私有目录 | /home/ubuntu/ai-news/.private/mihomo；目录 700、配置与 provider 缓存文件 600 |
| mixed 入口 | 127.0.0.1:17890，仅本机使用 |
| 控制器 | 127.0.0.1:19090，随机 secret 认证，不公开监听 |
| HTTP providers | lingyun、yahaha；每 6 小时更新 |
| 健康检查 | gstatic 204 探测，每 120 秒，超时 5 秒，lazy=false |
| 业务组 | AUTO-FAILOVER，fallback，use 顺序 lingyun、yahaha |
| 订阅更新组 | SUBSCRIPTION-FAILOVER，fallback，use 顺序 yahaha、lingyun |
| 空候选配置 | REJECT，不静默直连；空 provider / 全节点不可达行为尚未实测 |

fallback 是按顺序检查并切换可用节点，不是同时使用全部节点，也不是自动寻找最低延迟的承诺。以 provider 内节点顺序和实际探测结果为准。配置中有 REJECT 不能证明故障切换已经验证；订阅未加载与所有已加载节点不可达的情况都需分别检查。

两个组均按固定节点名称限制候选：

    ^(lingyun / .*台湾.*0[67].*HiNet|yahaha / .*)

当前共 10 个候选：已验证的凌云台湾 06/07 HiNet 与 Yahaha 全部 8 个节点。provider 仍缓存原始 40+8 全量节点并持续健康检查；另用 exclude-filter 排除订阅中的说明/流量等伪节点。不能把刷新瞬间的初始 alive 状态当成 40 个节点均健康：刷新曾使未验证香港节点暂被选中并造成 X 请求失败，因此两个 fallback 组都限制候选。订阅改名后需检查名称匹配；只要另一订阅仍有匹配且健康节点，就仍可提供候选，不能无条件保证永不中断。

业务流量优先凌云，订阅更新优先 Yahaha，两者目的不同。lingyun provider.proxy 为 SUBSCRIPTION-FAILOVER，解决其直连 TLS 超时及主业务出口 EOF；yahaha provider.proxy 为 DIRECT，已单独验证直接刷新成功。这里 DIRECT 仅用于这份订阅的下载，不是业务流量静默直连兜底。两份 provider 均发送 User-Agent: clash-verge/v2.4.2，每 21600 秒更新并保留缓存；最近实际 PUT 刷新都返回 204。

## 业务接入

ai-news-web 已通过 AI_NEWS_OUTBOUND_PROXY 显式选择本机代理用于应用外网请求。Miniflux 抓取外网已配置 HTTP_PROXY、HTTPS_PROXY，并通过 NO_PROXY 排除 localhost、127.0.0.1、::1；数据库与内部回环 API 保持本机路径。

RSSHub 已接入 PROXY_URI，并配置回环地址排除正则。web、RSSHub、Miniflux 均已重启并恢复 active；与 proxy、PostgreSQL 合计五个服务 active。真实重启后应用的 production_api_x_preflight 返回 X HTTP 200，证明不是仅在独立脚本设置代理后成功。未修改全机代理或电脑代理。

代理只改善出站网络路径。X 的账号授权、第三方 API Key、GraphQL 协议适配和实际原帖返回仍需独立验证；网络探测返回 200/204 不能宣称 X 订阅已可用。详情见 [X 调查](../research/X-NO-TOKEN.md)。模型预算仍为每日 80 次请求、500,000 Token，不因代理验收提高。

## 只读排查

先看用户服务和监听，再看经过脱敏的 provider/选路报告，最后看业务请求结果。以下命令以 ubuntu 用户执行：

    timeout 10s systemctl --user is-active ai-news-proxy
    timeout 10s ss -ltn

输出监听状态时只保留目标端口；不要打印配置正文、环境变量、HTTP provider 内容或带认证参数的请求 URL。控制器查询由读取本机私密配置的维护脚本完成，secret 不放进命令行、聊天或报告。不要为排查把控制器绑定到 0.0.0.0。

服务启动不等于 provider 更新成功；健康探测成功不等于目标站点可用；选路正确不等于图片、原文或帖子实际返回。依次核对这些层次，避免用一个绿色状态代替整条链验证。

## 验收结果与剩余边界

| 检查 | 结果 |
| --- | --- |
| 二进制来源与版本 | v1.19.31；官方 GitHub release 来源及 SHA256 保存在 [release 记录](../../artifacts/mihomo-release.json) |
| 生产服务与监听 | 五服务 active；17890、19090 和 8092 均仅监听 127.0.0.1 |
| 两份 provider | 加载 40+8；实际健康 2+8；两次刷新均 HTTP 204；候选限制为已验证的 10 个节点 |
| 登录和旧系统 | 登录接口 200；FreshRSS 302 |
| 应用出站 | 重启后的真实生产 API 检测 X HTTP 200 |
| X 帖子 | 仍为 posts_returned=false、RSSHub 503，适配器未配置；不能宣称 X 订阅可用 |
| 隔离故障切换 | 主节点健康 → 主节点不可达、跨订阅切换备用 → 主节点恢复，3 项通过；生产代理未被故障注入 |
| 回归测试 | 本轮 77 项 Python 测试通过；测试数量对应本次运行 |
| 控制器与私密权限 | 无 secret 请求返回 401；.private/mihomo 为 700，配置及 provider 文件为 600 |
| 仓库秘密审计 | 最新扫描 179 个文件，0 findings；部署单元与三个业务 drop-in 存于 deploy/proxy/，不含 secret |
| 空 provider / 所有节点不可达 | 未实测；不能仅凭 REJECT 配置宣称通过 |

[隔离故障报告](../../artifacts/proxy-failover-acceptance.json) 使用 17891/19091 测试端口，跨订阅切换那一步测得 3.03 秒。生产健康检查周期是 120 秒、单次超时 5 秒，**不承诺生产在 3 秒内故障切换**；恢复耗时与探测、请求超时及网络状态有关。

生产节点的健康计数、当前选择和状态会变化，引用时保留 [生产报告](../../artifacts/proxy-acceptance.json) 时间戳。最终报告只记录时间、状态码、耗时、脱敏节点标识和目标内容是否取得，不保存原始订阅或节点配置。本任务未做全部断网演练，已完成主节点故障跨 provider 切换。后续若补全断网验证，以新证据更新，不用直连兜底掩盖业务代理异常。
