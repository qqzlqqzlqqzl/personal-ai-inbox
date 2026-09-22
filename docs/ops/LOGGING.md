# 日志与只读诊断

核对日期：2026-09-22。实现依据：src/api.py、src/worker.py、src/diagnose.py。

## 请求日志怎样读

网关日志进入用户服务 ai-news-web 的 journal；已关闭 Uvicorn 默认 access log。正常经过中间件的响应带 X-Request-ID 和 Server-Timing: app;dur=...。日志字段为 request id、method、path、status、duration_ms、response_bytes。

- /mf/v1/ 请求均记录；其他路径耗时达到 **500 ms** 才记录。只记录路径，不记录查询参数、请求头、Cookie、认证信息或正文。
- duration_ms 是中间件开始到取得响应的应用耗时，不包含浏览器下载、解压、渲染，也不能当作流式响应完整传输时间。
- response_bytes 取响应的 Content-Length。经过 gzip 时通常是压缩后的响应体长度；没有该头时是 -。它不是原始 JSON 大小，也不是包含 HTTP/TLS 开销的实际网络字节数。核验 gzip 应同时看 Content-Encoding，不能拿客户端自动解压后的正文长度直接比较。
- 中间件提前拒绝的跨源写入、非法或超大 Content-Length，以及未捕获异常，不保证具有上述头或日志。日志不是完整安全审计账本。

## 后台分析

| 日志事件 | 可用于定位的信息 |
| --- | --- |
| analysis_done | 条目 ID、耗时、token 数、原文字符数 |
| analysis_reused | 条目 ID、复用来源 ID、耗时；复用不调用模型 |
| analysis_failed | 条目 ID、阶段、重试次数、耗时、脱敏错误类型；HTTP 错误仅附状态码，校验失败用固定说明 |
| worker_loop_error | 异常类型和函数名/行号组成的 frame 位置链 |

循环错误不输出异常消息、完整 traceback、局部变量或原文，避免上游错误把凭据/正文带入日志。数据库事件与 journal 名称不完全相同：分析失败的数据库事件为 fetch_error / ai_error，循环失败为 worker_error。等待模型、预算暂停、内容不足等状态也不等于出现一次失败日志。

## 一条命令先看状态

以服务所属的 ubuntu 用户运行：

    timeout 65s /home/ubuntu/ai-news/runtime/venv/bin/python /home/ubuntu/ai-news/src/diagnose.py

诊断只读 SQLite 并调用本机健康/订阅接口，内部读取所需密钥但不输出密钥，不修改设置、不触发抓取、不重启服务。结果包括：

- web、miniflux、rsshub、postgres 四个用户服务状态，以及 /healthz。
- 订阅总数和存在抓取错误的订阅数；最近 **20** 条数据库事件，仅时间、类型、条目 ID。
- worker 心跳时间和年龄；UTC 当日调用计数及 token 用量（实际值缺失时计入预留值）、每日预算。
- 最近 30 分钟 journal 中最多末尾 **5000 行**内匹配到的 API 请求数、达到 500 ms 的数量，以及最后 **10** 条慢请求。

requests_30m 是有界样本，只匹配 /mf/v1/，不是全站或完整 30 分钟统计；先看 journal_available。心跳较旧需结合正在处理的请求与轮询间隔判断，不能仅凭一次快照认定 worker 停止。依赖接口/数据库不可读时脚本可能直接失败，失败也不是“零错误”。

排查顺序：四服务与健康 → 心跳和预算 → feed 错误数/事件 → 请求 ID 与慢请求 → 对应条目分析状态。不要通过打印环境、.private 文件或上游响应正文来补日志。

analysis_reused 只证明复用到同账号、相同正文和提示配置的已有分析；不是一次新模型调用成功。核验新调用应结合 analysis_done、usage 记录及实际响应，不能用复用日志证明模型接口当前可用。
