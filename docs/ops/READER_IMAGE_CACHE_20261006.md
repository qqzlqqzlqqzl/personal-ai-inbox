# Reader 封面图片缓存

列表继续保留实际选中的 `ai.cover_url`。`cover_proxy_url` 先匹配当前用户可读条目的 analyses 元数据：entry ID、owner、文章 URL、选中 cover URL 必须全部一致，再调用既有 Miniflux 2.3.3 `signed_url`。缺少已有私钥、不匹配或公共 URL 检查失败时，保留同图原生签名复用/原图回退。不会创建、更改或输出私钥，不增加任意 URL 的 HTTP 接口，不在列表打开时抓取图片。

图片仍只通过 `/mf/proxy/<signature>/<encoded>?reader_width=480|960|1600` 获取。首次图片请求回源 Miniflux，连接超时 3 秒，响应头与完整 body 的网络阶段合计最多 20 秒；外层请求仍为 30 秒、2 个实际工作槽。压缩阶段沿用现有 Pillow 边界，取消 HTTP 等待不释放仍在运行的工作槽。第一次源站不可达仍失败并由前端回退原图，缓存不会解决首次回源失败。

服务端派生文件位于 `ROOT/state/reader-image-cache`，目录 0700、文件 0600。总缓存文件内容最多 256MiB，单图最多 8MiB，最多 4096 个图片文件；文件名为 URL/尺寸/Accept 的摘要，不记录原始 URL 或签名密钥。过期文件在下一次读取/写入时自动清理，超量按最近访问淘汰，临时写入也计入同一容量预留；不另建服务或数据库。目录项和文件系统块的额外开销不属于此文件内容上限。

每次命中前重新核当前私钥的 HMAC。签名不符、旧签名或无法读取私钥时，跳过缓存，仍交原生代理验证且不保存。缓存只接收原生 200、通过真实图像解码校验的 JPEG/PNG/WebP；错误、HTML/登录页、损坏图片、`no-store`/`no-cache`/`private` 不保存。请求 `no-cache`/`no-store` 也绕过服务端缓存。同图同尺寸/Accept 用有限锁去重，Linux 下覆盖同一缓存目录的进程。磁盘不可用时继续原生回源。

保留原生浏览器 Cache-Control、变体 ETag，并给磁盘命中返回 Age；存储期限不超过原生 max-age/s-maxage 的剩余时间，未指定时默认一天，最长七天。Miniflux 原有上游访问策略不变；这里不直接连接外部 URL，也不下载全站封面。

定向检查：`tests/test_reader_cover_proxy.py`、`tests/test_reader_image_cache.py`、`tests/test_reader_image_proxy.py` 与 `tests/test_api.py` 的封面场景。Windows 只运行无磁盘清理的标准库封面控和语法检查；磁盘淘汰与实际 HTTP/Pillow 场景留给已有 Linux 环境。没有以代码检查冒称上线图片速度或源站可达性通过。
