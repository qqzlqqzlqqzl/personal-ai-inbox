# Reader 首屏代码加载候选（2026-10-05）

这是从 `da1f36e7c1bf44fd3fcf3a1733e0c7cd98d7ec74` 制作的有限源码候选。上游固定为 `534eeb97723ac11025de4ec1ac56335072e3be52`。没有取得真实构建、浏览器或速度收益证据；#108 不因此完成。

## 改动与原因

原 React Router 初始化会等待全部匹配路由的 `lazy` 结果。内容路由的 `ContentPages` 静态引用全部六类页面，`AuthenticatedApp` 又静态引用 App，Main 静态引用设置内容。现有隔离实验显示版本请求在大内容 chunk 下载后才发起，支持测试缩短此处依赖链。

最终 `install_agent_status.py` 继续作为受审补丁的最后一步，生成以下拆分：

| 位置 | 候选行为 |
|---|---|
| routes / ContentPages | 六类页面的列表与 `/entry/:entryId` 深链仍使用原路径；路由匹配只取得轻量组件包装器，实际页面通过 React.lazy 加载 |
| AuthenticatedApp | 保留静态 AppDataProvider，将 App 放入自己的 Suspense 边界；provider 可先提交并 bootstrap，App 在其内部下载 |
| HomeRedirect / AgentStatus | 使用各自的延迟渲染组件，避免对应深链的 router.lazy 阻塞认证保护层挂载 |
| Main / 设置 | 原 AccessibleModal、close/focus/pathname 行为保留；仅 visible 时渲染延迟加载的 SettingsModalContent |
| DeferredComponent | 每个表面有独立 Suspense 和可读的 loading status；内容页加载不替换已挂载 App 外壳 |

RouterProtect 原文件不变；版本检查、unsupported/401、会话键与过期请求取消继续走现有逻辑。authenticated route 仍返回原 ErrorPage 作为路由 ErrorBoundary，延迟模块失败会沿 React 树交给原边界；`main.jsx` 的 preload-error 处理不变。没有 hover 触发、全站 prefetch、接口/data 缓存改动、CI/预算/阈值改动。

## 安装边界

原 routes SHA256 `90eb803fcf6feeaa32038dce6e8e8e6a14c09db6d37d51a930e3d348e39a2a34` 保留。新校验同时绑定 AuthenticatedApp、ContentPages、Main 的精确固定源码。只接受原文件、精确旧 agent-status 路由补丁或精确当前生成结果；反向还原的 SHA 与重新生成结果都必须一致。已有 `DeferredComponent.jsx` 也必须与作者源码完全一致。

所有输入校验完成后才开始写入。错误、重复片段及其他协作者漂移会拒绝安装。安装可以重跑，精确旧状态页补丁可以升级。固定源码测试快照来自只读 pinned-upstream JSON，校验 LF 内容 SHA；Windows 工作区 CRLF 不作为 Git blob 哈希。

## 已验证与后续验收

本地标准库命令 `python -X utf8 tests/test_reader_startup_installer.py` 通过 8 项测试：精确输入、重复安装、旧补丁升级、认证文件保留、安装前/后漂移拒绝、延迟边界结构、设置原件反向还原、作者文件缺失时先拒绝写入。Python AST 检查与 `git -c core.longpaths=true diff --check` 通过。

本机没有此 Reader 的 pinned ReactFlux/node_modules，也没有 pytest/API 测试依赖。本地没有执行完整 pytest、pnpm/Vite 构建、浏览器 keyboard/touch/weak-network 测试或生产测试。有限源码测试不能证明异步弹窗焦点、路由错误 UI、背景 inert、列表/文章滚动和真实页面时序正确。

主 agent 在已有 Linux 隔离环境继续：

1. 用固定上游运行完整补丁链，确认所有 guard 通过、两次安装等价；执行现有相关 pytest 和真实构建。
2. 在真实生成 bundle 检查新增动态 chunk 与初始静态图，确认版本检查和 provider bootstrap 先于业务图完成；不能只凭字符串或 bundle 总体尺寸作收益判断。
3. 验证未登录、unsupported、401、重试、会话切换与迟到请求、六类页面/文章深链、代码下载失败边界。
4. 验证设置冷打开/立即关闭/再打开、Tab/Escape/焦点返回；手机背景 inert/aria-hidden、原文章/列表滚动与 keyboard/touch 交互。
5. 执行现有真实产品 A/B：原 5 pairs × 3 modes、15 秒超时、原 immutable 回归对照保持。读取完整样本与 CDP 原件后判断是否值得集成。

取舍：App 与各内容代码现在可能在认证后分阶段下载，可能增加一个内容加载往返。版本请求提前并不保证列表或正文更快，A/B 结果允许否决此候选。冻结 PR113 的原分支/HEAD、Kaggle operator 与生产状态均未改；候选未 push、merge 或 deploy。
