# 手机阅读空间修复与隔离验收（2026-10-02）

Refs #86。截图问题及对照生产基线为 da5a6222779517b92782bf19e2a2166801676fb4；分支 fix/mobile-reading-space，独立工作区 /home/ubuntu/ui-review-20261002-mobile。

已合入 release 的时区修复基线 64f661e3b7fa7bbc361979e365469254f44adb03，保留其所有源码和两组 CI 浏览器回归。除共享 CI、Harness 参数外 14 文件逐字节相同，见 [base-integration.json](../../artifacts/mobile-reading/base-integration.json)。排序 overlay 的 before 来自该基线经正式 patch 阶段生成的已核对源码，保留日历逻辑。

用户截图已按当前 Library 流程在执行端获取，确认 254205 字节并实际查看。原始用户图片留在 git 忽略的 runtime/library，提交的截图均为隔离 API 场景。

## 实现

常用原始 / AI精选、笔记、待处理和最低推荐分仍直接可点，当前条件保留选中状态及 ≥分数。手机用短视觉标签，保留完整可访问名称。第二行保留当前范围、计数、搜索和单一排序器。手机原生 select 保留触摸、键盘和可访问语义；当前排序字段及方向以可换行文字完整显示，200% 文字不截掉方向，焦点框落在可见控件。

长分析计数、Kaggle 详情、采样时间、读取失败/旧快照/重试、新内容提示，以及设置与快速跳转放入“运行状态与更多”。入口直接显示未确认、运行、冷却、读取失败、保存待查或有更新等当前状态。保存响应未知不声称未保存。状态、设置和导航依次打开，避免嵌套模态；关闭用 preventScroll 恢复可见入口的焦点；手机/桌面断点切换时回到当前可见的状态或直接设置/导航入口。

列表仍使用原有内部滚动。导航条回到 flex 流，底栏与全文操作栏保留至少 44px 触摸区域；底栏 safe-area 只计算一次。触摸设备横屏高度 ≤500px 时继续使用紧凑布局，包含宽度超过 768px 的手机；统一底栏单选项间距与对齐。深色最低分文字沿主题颜色。

修改最终 frontend-review/after overlay，新增 MobileReader.css；旧生产 overlay 只按精确已审查 SHA256 接纳。没有直接提交生成 upstream。补齐现有重复 overlay 测试的最后 patch_interaction_review 阶段。

AI 轮询、保存队列、查询/排序语义、后台分析、模型、预算、订阅和生产阅读记录保持原流程。未处理截图中归属未知的粉色悬浮球。

## 阅读区域实测

相同隔离场景、Noto Sans CJK SC、正常字号、状态面板关闭。浏览器与 Android 系统栏不计入网页 viewport。完整几何、初屏可见推荐文本及主题测量见 [baseline.json](../../artifacts/mobile-reading/baseline.json) 与 [mobile.json](../../artifacts/mobile-reading/mobile.json)。

| CSS viewport | 原阅读区 | 新阅读区 | 回收高度 | 原占比 | 新占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 360 × 640 | 355px | 496px | 141px | 55.5% | 77.5% |
| 390 × 844 | 559px | 700px | 141px | 66.2% | 82.9% |
| 430 × 932 | 685px | 788px | 103px | 73.5% | 84.5% |
| 390 × 576 | 291px | 432px | 141px | 50.5% | 75.0% |
| 390 × 400 | 115px | 256px | 141px | 28.7% | 64.0% |
| 844 × 390 | 201.5px | 246px | 44.5px | 51.7% | 63.1% |

新布局顶部 92px、底栏 52px（safe-area 另计）。360×640 第一屏可完整读到推荐分及理由；原隔离页面理由落在阅读区之外。200% 文字允许控件换行并保持可达，不以固定高度裁掉操作。75% 指标针对常规字级且网页高度至少 576px 的关闭状态。

对照：[360 原版](../../artifacts/mobile-reading/before-360x640-light.png)、[360 新版](../../artifacts/mobile-reading/after-360x640-light.png)、[390 深色原版](../../artifacts/mobile-reading/before-390x844-dark.png)、[390 深色新版](../../artifacts/mobile-reading/after-390x844-dark.png)。横屏对照：[844 原版](../../artifacts/mobile-reading/before-844x390-dark.png)、[844 新版](../../artifacts/mobile-reading/after-844x390-dark.png)。另见[状态面板](../../artifacts/mobile-reading/status-390x844-dark.png)、[200% 文字](../../artifacts/mobile-reading/text-200-390x844.png)、[全文文末](../../artifacts/mobile-reading/full-text-390x844.png)。

## 验证与自审

- 完整 Python：691 passed、161 subtests passed。保留原有测试，未知漂移仍在写入前拒绝。
- 全部 JavaScript 脚本及 source_history_component_acceptance 通过。
- 手机：320×640、360×640、390×844、412×915、430×932、390×576、390×400、640×360、844×390，明暗主题共 18 场景、261 项检查。
- 模拟 touch；直接筛选、搜索/排序及底栏目标 ≥44×44；最低分文字对比度 ≥4.5。
- 虚拟列表滚至真实末项，末卡片推荐/标签可达；全文文末段落、原文入口、笔记及底部按钮另行验证。
- 反复开关、Esc、焦点恢复与状态→设置/导航单一模态；未修改查询时列表锚点偏移 ≤2px、无新增列表查询；另测状态、设置、导航跨手机/桌面断点后的可见焦点恢复。桌面快速跳转 → AI设置与资源看板 → 切至手机 → 关闭设置的真实路径，确认旧快速跳转按钮仍连接但已隐藏，返回可见状态入口且无后台写入；直接设置入口回归保留。此路径在旧HEAD 5aaec317明确复现焦点断言失败，新实现只选可见、启用且可聚焦的返回目标，见 [palette-focus-regression.json](../../artifacts/mobile-reading/palette-focus-regression.json)。
- 搜索提交/清除、合成 IME Enter、键盘高度缩为 400px 后恢复、排序/辅助筛选重载持久化及浏览器 Back/Forward。
- 状态失败保留旧快照与重试；最低分保存异常明确“结果未确认”，后续成功只写 minimum_score；新内容仍需用户明确刷新。
- 模拟 20px 顶部 / 24px 底部 safe-area，确认只预留一次并保留末项可达。
- 200% 文字：320/390/430 宽度的 10 种排序字段与方向各自验证可见字形片段完整落在控件与 viewport 内，选中项显示等于原生 option 标签，不仅检查横向溢出。
- 200% 文字、640/844 横屏、横屏全文文末与 44px 操作目标、无横向溢出；阅读高度、末项与实际文字可见性共同验收。
- 五组既有浏览器回归：history、console、reading、search/IME、sort；原断言保留，手机入口改为实际状态面板路径，响应式焦点检查指向当前可见入口。另跑新基线 calendar 与 calendar-identity 浏览器验收。
- CJK 门检：1440 与 390 均由 Noto Sans CJK SC 渲染全部 10 个中文字符。
- 两次正式隔离构建：221 个前端源文件、118 个发布文件逐字节 SHA256 相同，见 [repeat-build.json](../../artifacts/mobile-reading/repeat-build.json)。

测试环境为 Playwright 1.63.0 对应 Chromium headless shell 153.0.8010.12，见 [environment.json](../../artifacts/mobile-reading/environment.json)。早期本地字体门检发现文泉驿与 CI 预期不一致，随后在本工作区验证 APT 包 SHA256/大小、隔离提取 Noto 字体并重跑；没有更改系统或生产字体配置。

## 验收边界

这是 Linux Chromium 窄屏、触摸、文本缩放与受控 viewport/inset/IME 模拟。没有真实 Android/iOS、Safari、手机地址栏伸缩、物理输入法或真实软键盘证据。notes_pair_acceptance.py 为需独立 Miniflux 二进制和 PostgreSQL 容器的后端配对集成脚本；本前端任务没有另建其测试环境，未声称该项通过，其代码与新基线逐字节相同。

主线程最终审查、合并部署和用户手机生产复验仍待完成；本任务保持 draft，不 merge/deploy/close。

CI 保留全部原步骤和权限，新增手机专项及证据归档；最终 GitHub exact-head 完整结果以 PR run 为准。
