# X 无个人账号 Token：当前结论与接入边界

核对日期：2026-09-22。**尚未部署并验证稳定的无个人 X Token 订阅方案。** “无需个人 X Token”不等于“无需第三方 API Key”，也不等于服务器能直连 X。

## 已有证据

artifacts/x-no-token-investigation.json 保存了本次服务器探测（账号 OpenAI）：

| 检查 | 结果 | 能证明什么 |
| --- | --- | --- |
| 直连 X | ConnectTimeout | 本次直连失败；不是所有网络环境均不可达 |
| 本机 RSSHub /twitter/user/OpenAI | HTTP 503，适配器未配置 | 本次没有返回帖子，不能宣称订阅可用 |
| twitterapi.io 候选接口 | 未认证请求 HTTP 403 | 有 HTTP 响应，但未验证鉴权成功或获取帖子 |

这份文件是一次快照，不是持续可用性报告。不能把“接口可访问”“配置变量存在”或“订阅创建成功”当成成功获取原帖。

## RSSHub 第三方接口并非任意 REST URL

本机版本记录在 upstream.lock.json 的 rsshub 字段。该记录值为 a84b41faa31581bacac4cea374fcf909f784bab9；目录不是独立 Git 仓库，不能用目录内 git rev-parse HEAD 冒充上游版本。本次直接核对当前源码：

- upstream/rsshub/lib/routes/twitter/api/web-api/api.ts
- upstream/rsshub/lib/routes/twitter/api/web-api/utils.ts

thirdPartyApi 分支拼接 GraphQL operation 路径，传入 variables / features 等参数，读取相应 GraphQL 数据结构。因此需要协议兼容服务或显式转换适配，不能把 twitterapi.io 的 REST 根地址直接填进去。[RSSHub 官方源码目录](https://github.com/DIYgod/RSSHub/tree/master/lib/routes/twitter/api/web-api)

twitterapi.io 的官方接口是 /twitter/user/last_tweets，要求 X-API-Key，返回 tweets 和分页字段。这与上述契约不同；尚未编写转换层或验证付费/认证调用。[官方接口文档](https://docs.twitterapi.io/api-reference/endpoint/get_user_last_tweets)

## 可选方向

| 路线 | 前提与边界 | 当前状态 |
| --- | --- | --- |
| guest/匿名抓取经可用出站代理 | 先验证出站与实际时间线能力；代理只解决网络，不保证 guest 权限、限流或长期可用 | 未部署、未验证 |
| 第三方数据服务 | 第三方 API Key、明确费用与授权，再实现数据/分页/媒体转换；不需要把个人 X 登录 Token 交给本项目 | 仅候选，未购买、未认证验证 |
| 搜索发现帖子 | 只作发现入口，必须保留原链接、时间和来源；搜索摘要不能作为完整原帖，也不保证时间线覆盖 | 不能替代订阅成功证据 |
| Nitter 实例或自建 | 是否提供 RSS、后端抓取权限、限流与维护状况均需实测；用户不用 Token 不代表服务端不用账号资源 | 未部署稳定实例 |

Nitter 官方 GitHub 页面在核对时标注仓库于 **2026-09-11 归档**；README 同时写明项目会继续，不能据此断言永久停止。README 还说明 RSS 取决于实例，可能因滥用而关闭。它不是当前可用性的保证。[Nitter 官方仓库](https://github.com/zedeus/nitter)

## 产品实际行为与验收

POST /mf/v1/ai/x/probe 已加入认证后的预检：分别返回 adapter_configured、network_reachable、posts_returned、post_count。网络返回 HTTP 响应只表示可达；RSSHub 返回 200 后还要解析 RSS，并找到含 X/Twitter /status/数字 链接的条目才算取得帖子。预检总等待上限 18 秒，单请求超时 12 秒，RSS 读取上限 2 MiB；源码为 src/x_source.py。

界面可先检测再订阅；服务端对携带 x_handle 的订阅再次探测，没有帖子返回 HTTP 409，不创建该 X 订阅。普通 URL 订阅路径不是这一专用检查的覆盖范围。当前帖子判定是 RSS/链接级检查，不等于逐条内容真实性、完整性和更新持续性都已验证。

下一次接入验收必须有真实公开帖子、正确账号/原始链接/时间、分页或连续刷新与去重结果；再检查阅读器入库、图片和分析链。稳定性要跨多次刷新观察。未获得这些证据前保持实验状态。本次未购买服务、安装代理或索要个人 X Token。
