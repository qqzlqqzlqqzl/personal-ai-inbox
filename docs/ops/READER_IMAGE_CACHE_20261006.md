# Reader 图片缓存与整篇动态预热

列表使用当前条目绑定的 /mf/proxy 签名封面。整篇预热只读取 Miniflux 已保存的正文，沿用 core.decorate(include_source_fallback=True) 的 prepared/source fallback 口径。不会重新抓外站全文，不建立正文数据库，不恢复翻译功能，也不改变前端延迟加载。

## 同一份 1GiB 缓存

ROOT/state/reader-image-cache 保存经 Pillow 验证并实际解码的 JPEG/PNG/WebP/GIF，以及运行环境 Pillow 支持的 AVIF。原图 width=0 保留字节和动画，不缩放；已有480/960/1600显示变体继续使用。单图8MiB、解码总像素1200万、动画最多256帧；SVG、音视频与无法安全验证的图片不主动下载或缓存。

缓存文件总字节上限1GiB，最多8192图；统计包含每图header与临时写入，目录0700/文件0600。过期内容清除；按文章发布时间优先保留新图，同图的最新引用提升priority，旧文或普通点击命中不降低priority。后台只淘汰更旧图片；同优先级也保护，避免超大文章尾部来回挤掉自己的图片。普通点击仍可缓存。

后台发起图片HTTP前保守预留一张最大图片的空间，最多留约8MiB余量，避免下载之后才发现无法保留。容量不足停止更旧图片和正文读取；新文章或缓存过期释放空间后自动继续。不会为了凑满1GiB下载无用内容。

每次读取缓存仍验证当前HMAC签名。仅原生200、有效图片及允许缓存的响应写入；private/no-store/no-cache、错误页、损坏图片不缓存。Miniflux明确public且没有冲突策略的匿名会话Cookie不会转发或存储；其余Cookie响应不缓存。依据原生代理暴露的策略，不声称知道其未转发的源站策略。

正文原始URL的缓存key为同一签名目标、width=0、统一NATIVE_IMAGE_ACCEPT。原始 /mf/proxy GET 仅严格无query、无Range且图片请求时可查写该缓存；未命中继续原来的native代理。HEAD、其他媒体、其他query与签名/auth边界保留原行为。后端封面变体仍要求唯一合法reader_width；不会把reader_width=0开放为额外URL接口。

## 有界、可续的全局队列

warm_reader_covers.py --once 使用全部当前analyses中done且score>=8文章，按发布时间新到旧。无固定240条总上限。每页24条keyset分页，每轮最多16页、60次实际图片未命中或约110秒；达到限额保留游标，下轮从中断文章继续，先完成该篇封面和正文，再进入更旧文章。

每轮先核对最新一页，之后续跑已保存的深页游标，最终扫描到末尾后重新开始。已完成文章只存短期哈希回执，不保存正文或URL；回执最长10分钟，且不会长于刚确认的图片剩余TTL，避免30秒一轮重复GET同篇已准备正文。分析版本、元数据或当前用户变化失效后重新核实。状态上限128KiB，含有界文章回执、分页游标及失败重试记录。

以当前账号的原生 /v1/me、feed/category 可见性、轻量metadata及单篇已有正文核验所有权、URL和feed绑定，排除隐藏源与明确不合格正文。只有正文img/srcset/picture中真实验证通过的签名图片地址可预热；直接外站URL、伪造签名、视频source与SVG不进入下载队列。封面去重，封面与正文共享的同图按实际需要保留变体和原图，同图同宽度不重复下载。

失败按图片与慢源指数退避，跨页保留，坏源不阻断其他来源。进程中的decorate及其依赖使用同一mode=ro/query_only数据库连接，不执行迁移、WAL设置、翻译排队或业务DB写入。

沿用ai-news-reader-covers.service/timer，不新建服务：开机3分钟后启动，每次oneshot结束30秒后续跑（AccuracySec=5s）；低优先级、现有资源限制与120秒服务超时保留。单进程锁阻止重叠。部署时需要同步现有timer配置并确认真实缓存命中。

## 验证

定向测试覆盖550条分页续跑、60miss逐篇顺序、正文签名抽图、只读数据库、跨页退避、满容量停止、publication priority/共享图提升、字节上限、GIF/AVIF验证和raw0浏览器/预热共用key。本地现有环境可运行stdlib unittest；完整pytest/API路径验证由同一提交的CI执行，不能将未运行项标记为通过。
