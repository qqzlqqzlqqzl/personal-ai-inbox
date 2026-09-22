# HTTPS 入口与维护备用通道

## 日常访问

直接打开 **https://106.53.40.6/inbox/**。用户名 `reader`；实际密码保存在服务器 `.private/miniflux.env`，只由维护者自行查看，不输出到聊天、Git、日志或截图。

公网 HTTPS 已通过完整浏览器验收，包括两个独立桌面/移动会话的登录、正文图片、已读/收藏、AI 设置和工具同步。Windows 本机也已验证 HTTPS 200。移动测试为 Chromium 390×844 模拟布局，未进行物理手机实机验收。

应用继续只监听服务器 `127.0.0.1:8092`。复用现有 Nginx 443 和 IP TLS 证书；HTTP 80 保持原策略，`http://106.53.40.6/inbox/` 返回 426，请使用 HTTPS。

## SSH tunnel：维护或故障备用

日常无需 SSH 隧道。需要维护时，核对服务器主机指纹，使用原有可信 known_hosts 与已有授权，在自己的终端建立回环转发：

```sh
ssh -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -N -L 127.0.0.1:8092:127.0.0.1:8092 ubuntu@106.53.40.6
```

随后在运行转发的电脑打开 `http://127.0.0.1:8092/inbox/`；`/healthz`、`/readyz`、`/deployment` 是网关回环维护接口，不经本次 Nginx 配置公开。

不要把 8092 改为 `0.0.0.0`，不要新增云安全组端口，不要忽略 SSH 主机校验。电脑的回环地址不是手机地址；手机日常使用公网 HTTPS 入口。

证据：[公网浏览器验收](../../artifacts/browser-acceptance-public.json)、[HTTP 与服务检查](../../artifacts/public-https.json)。
