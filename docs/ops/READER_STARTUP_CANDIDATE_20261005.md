# Reader 首屏代码加载候选（2026-10-05）

本轮是从干净的 `e1c25416a7e8578d422023dec12d0db45aa7181f`（base `3349cf2`）制作的有限源码修复；上游固定为 `534eeb97723ac11025de4ec1ac56335072e3be52`。初始 early-bootstrap 候选的实际 CI `37264000688` 在 step 26 失败，不能用其构建或较早的请求时序宣称产品验收通过；#108 仍未完成。

## 改动与原因

原 React Router 初始化会等待全部匹配路由的 `lazy` 结果。内容路由的 `ContentPages` 静态引用全部六类页面，`AuthenticatedApp` 又静态引用 App，Main 静态引用设置内容。现有隔离实验显示版本请求在大内容 chunk 下载后才发起，支持测试缩短此处依赖链。

e1c 将六类页面改为独立 React.lazy 后出现新的产品过渡缺陷：主负责人读取原 query frames/PNG，All→Today 的 seq107–128 已选中 Today，但 lazy Today 代码的 fallback 没有 `.page-info`，直到约384.6ms后才恢复真实标题。原件位于 `startup116-e1c-ci-37264000688-original.log` 与 `startup116-e1c-original-artifacts/11326500377/extracted/runtime/query-result-ownership/`；这些失败原件保留，不以旧成功计数代替。

本轮把固定六页原静态映射移到 `LoadedContentPages.jsx`，轻量 `ContentPages.jsx` 只定义一个共享 lazy selector。认证/provider 仍先提交；只有首次实际内容渲染才加载六页业务图。任一内容页已挂载之后，All→Today 等现有 scope 切换使用同一个已完成的 lazy identity，不再首次下载另一个 page wrapper。标题、lens、账号时区继续由真实 `Content`/`SearchAndSortBar` 路径生成；没有第二套 loading 标题、fixture 文案或 count/rows 借用。

最终 `install_agent_status.py` 继续作为受审补丁的最后一步，生成以下拆分：

| 位置 | 候选行为 |
|---|---|
| routes / ContentPages | 六类页面的列表与 `/entry/:entryId` 深链仍使用原路径；路由匹配取得轻量包装器，六页共用一次 lazy selector |
| LoadedContentPages | 固定原六页静态映射的精确原字节；位于认证后首次内容 lazy 图，后续 scope 切换不再单独等待页面模块 |
| AuthenticatedApp | 保留静态 AppDataProvider，将 App 放入自己的 Suspense 边界；provider 可先提交并 bootstrap，App 在其内部下载 |
| HomeRedirect / AgentStatus | 使用各自的延迟渲染组件，避免对应深链的 router.lazy 阻塞认证保护层挂载 |
| Main / 设置 | 原 AccessibleModal、close/focus/pathname 行为保留；仅 visible 时渲染延迟加载的 SettingsModalContent |
| DeferredComponent | 每个表面有独立 Suspense 和可读的 loading status；内容页加载不替换已挂载 App 外壳 |

`SharedContentPage` 和 selector 只在模块首次加载时定义；六个 route wrapper 在模块初始化时各定义一次。selector 返回原 `Page` 类型：All→Today 改变真实页面类型，原 scope mount/cleanup 不复用 All 的页面 state；同一页面的列表与 detail 路由复用同一个 wrapper/Page 引用，保留原 `useParams`/context 生命周期，没有新增 `key` 强制 remount。具体浏览器帧与 detail 焦点行为仍须真实验收。

RouterProtect 原文件不变；版本检查、unsupported/401、会话键与过期请求取消继续走现有逻辑。authenticated route 仍返回原 ErrorPage 作为路由 ErrorBoundary，延迟模块失败会沿 React 树交给原边界；`main.jsx` 的 preload-error 处理不变。没有 hover 触发、全站 prefetch、接口/data 缓存改动、CI/预算/阈值改动。

## 安装边界

原 routes SHA256 `90eb803fcf6feeaa32038dce6e8e8e6a14c09db6d37d51a930e3d348e39a2a34` 保留。校验同时绑定 AuthenticatedApp、ContentPages、Main 的精确固定源码。只接受原文件、精确旧 agent-status 路由补丁、精确 e1c 六 lazy 映射或精确当前生成结果；反向还原的 SHA 与重新生成结果都必须一致。已有 `DeferredComponent.jsx` 也必须与作者源码完全一致。新 `LoadedContentPages.jsx` 作者字节和既有目标都绑定原 ContentPages LF SHA `2e56638e1bf094ce43fdd36c0c620eb005b62afc1069f401c0e6b569a5db1ccf`，任何漂移在写入前拒绝。

所有输入校验完成后才开始写入。错误、重复片段及其他协作者漂移会拒绝安装。安装可以重跑，精确旧状态页补丁可以升级。固定源码测试快照来自只读 pinned-upstream JSON，校验 LF 内容 SHA；Windows 工作区 CRLF 不作为 Git blob 哈希。

## 已验证与后续验收

本地标准库命令 `python -X utf8 -B tests/test_reader_startup_installer.py` 通过11项测试。原8项包含精确输入、重复安装、旧状态页补丁升级、认证文件保留、安装前/后漂移拒绝、延迟边界结构、设置原件反向还原和作者文件缺失时先拒绝写入；新3项覆盖 e1c early-overlay 升级时 auth/provider 字节不变、单一 lazy identity 与原 Page/路由引用、loaded 作者漂移及重复 selector 在任何写入前拒绝。新 loaded 目标的安装前/后漂移也纳入原负控。所有合成目录保留，未删除文件。

实现前只读核对公开 pinned All、Today、Content 和实际 header `src/components/Article/SearchAndSortBar.jsx`，再核本地既有 scope/calendar/结果 ownership 补丁；新 installer 不修改这些业务组件。原 All/Today 仍各返回真实 Content，header 的当前 scope/lens/zone 与 query owner 继续使用现有状态逻辑。未触碰逐帧检查、原 5 秒条件、API 或 CI 阈值。主负责人另负责 dev fixture 的精确 SRC pin 更新。

本机没有此 Reader 的 pinned ReactFlux/node_modules，也没有 pytest/API 测试依赖。本地没有执行完整 pytest、pnpm/Vite 构建、浏览器 keyboard/touch/weak-network 测试或生产测试。有限源码测试不能证明异步弹窗焦点、路由错误 UI、背景 inert、列表/文章滚动和真实页面时序正确。

主 agent 在已有 Linux 隔离环境继续：

1. 用固定上游运行完整补丁链，确认所有 guard 通过、两次安装等价；执行现有相关 pytest 和真实构建。
2. 在真实生成 bundle 检查新增动态 chunk 与初始静态图，确认版本检查和 provider bootstrap 先于业务图完成；不能只凭字符串或 bundle 总体尺寸作收益判断。
3. 验证未登录、unsupported、401、重试、会话切换与迟到请求、六类页面/文章深链、代码下载失败边界。
4. 验证设置冷打开/立即关闭/再打开、Tab/Escape/焦点返回；手机背景 inert/aria-hidden、原文章/列表滚动与 keyboard/touch 交互。
5. 执行现有真实产品 A/B：原 5 pairs × 3 modes、15 秒超时、原 immutable 回归对照保持。读取完整样本与 CDP 原件后判断是否值得集成。

取舍：App 和六页共同业务图仍在认证后分阶段下载，可能增加一个首次内容加载往返；本轮恢复原有界六页静态图，也可能使首次内容 chunk 大于 e1c 的单页图。它没有 hover/全站/无界预取。版本请求提前不保证首列表或正文更快，A/B 结果允许否决候选。冻结 PR113 的原分支/HEAD、PG 候选、Kaggle operator 与生产状态未在本岗改动；本岗没有暂存、commit、push、merge 或 deploy。
