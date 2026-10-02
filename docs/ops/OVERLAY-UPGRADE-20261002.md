# 历史 selector 升级准入修复（2026-10-02）

Refs #86。独立工作区 `/home/ubuntu/ui-review-20261002-upgrade`，分支 `fix/reader-overlay-upgrade`；目标基线 `15e0c8d5236dc006ff97a3846c03feaf710dc13b`，旧服务基线 `da5a6222779517b92782bf19e2a2166801676fb4`。

## 原因与修复

旧生成 SearchAndSortBar 的 selector 使用 `article-sort-select / 文章排序`。da5 的已提交 `patches/reactflux.patch` 记录这一行，但 patch_scope_ai_filters 的 pristine 创建分支生成 `ai-sort-select / 排序方式`。已有 selector 时没有 `scoreOrder`，创建分支被跳过；后续只规范化逻辑，没有规范化旧标签和 class。目标最终 overlay 按整个 before 精确准入，因此旧生成树升级到第十一阶段时拒绝。

在 selector 的所属 scope patch 阶段新增一个精确 opening tag 的 normalize：仅接受已提交历史补丁中的完整旧 tag，转成已有 canonical tag。现在旧树和 pristine 都得到同一已审 before；当前目标重复运行时 canonical tag 已存在，保持幂等。最终 installer、before/after、previous-hashes 均未修改；额外或未知漂移仍拒绝，没有增加准入 hash。

## 取证与独立复现

已通过当前 Library 支持流程在消费端实际获取精确取证包，确认大小和父线程给定 SHA256；四份源码 hash 分别核对。取证 ZIP、源码、服务日志和服务器状态全部保留在忽略的 runtime，未提交。

直接以捕获基线字节跑隔离目标阶段，独立重现同一拒绝。再只用已提交 da5 补丁对 pinned ReactFlux 的 SearchAndSortBar hunk 重建，并跑旧正式阶段，生成组件与捕获基线逐字节一致；此路径也重现同一失败。CI 使用这条源自仓库的重建路径，不需要服务器捕获文件。见 [仓库历史补丁复现证据](../../artifacts/overlay-upgrade/historical-patch-reproduction.json)。

- 历史基线 SHA256：`65a3e7ce6a2c75b31994a5442054267beea9d8e7a602d371cd9787d7a9e18977`。
- 修复前第十一阶段输入：`e1945240023db0ad4efb6ef59be6b314f1ca22ea4ae129f6f38b29ae940de22d`，与已审 before 只有 selector 那一行不同，最终 installer 拒绝。
- 修复后输入：`59cccf7ed8134de914ee7688a7fa80f87a2fa428f29b0ad255010445c40d1d36`，等于已审 before。
- 修复后最终：`25e801d1c55b2597f614d1a2e5a7a141d6217d417044f999a0d6d493f6559f2a`，等于已审 after。

## CI 覆盖缺口与回归

原 CI 从 pinned pristine 应用当前 patch，再重复当前 patch，验证了当前创建分支与当前状态幂等性；没有验证已记录历史 selector 被保留时的升级。单独用 da5 当前脚本从 pristine 生成旧树，也会生成 canonical selector，不能重现实际历史问题，见 [初始 pristine 脚本路径阶段 hash](../../artifacts/overlay-upgrade/public-baseline-stage-hashes.json)。

新回归参数化两条基线：旧脚本 pristine 与历史记录 patch。各自保留整个生成树及 `runtime/reactflux-original`，仅更新应用 authoring，然后按正式十一阶段升级；验证 before、全部原始备份保留、最终整个源树与目标 pristine 逐字节一致及重复升级幂等。历史 patch 用例在修复前明确失败，修复后通过。

各基线在最终 overlay 前注入未知额外漂移，验证 installer 拒绝且整个前端源树零写入，再恢复已审 before 验证成功。另验旧 class／新标签混用、旧标签／新 class 混用、额外属性与重复 tag：严格 normalize 拒绝，未知组件不被改写；调用最终 installer 也零写入拒绝。已知 tag 周围的未知源码允许 tag 自身规范化，但保留未知内容，整个文件仍被最终准入拒绝。早期所属阶段可以刷新其负责的组件，零写入保证针对最终 installer，不声称整个管线失败前零写入。CI pinned checkout 获取完整历史对象，保持 persist-credentials:false；缺少不可变基线时测试失败。早期 draft 的独立 git fetch 因未保留认证失败，已经改为由既有认证 checkout 获取历史，不添加凭据或权限。

## 验证与边界

专项：10 passed，包括 mixed-label、mixed-class、extra-attribute、duplicate-tag 与 adjacent-source 五种未知邻近变体。修复前历史负向证明：1 failed、1 passed，失败发生在预期 before 等价断言；捕获字节与仓库重建路径均得到同一最终 installer 拒绝。修复后两条真实历史复现都通过，最终 SHA 与已审 after 相同，重复整个源树相同。按正式 prepare 后完整 Python：800 passed、161 subtests passed；最终 exact-head 全套 Reader CI 结果见 PR。

仅修改前端生成脚本，不修改已审 UI、AI 后台或模型预算。手机 UI 仍以 PR88 的 261 项窄屏/触摸模拟验收为已有证据，本次全套 CI 会重跑；不声称实机验证或生产重试成功。

主线程独立审查并明确批准 source commit `a545a4088414a192bacd8745a7021298ee73a69a`，src tree `c4b08ffd148d64e0bee060f172b398a1c360263d`。据此将 notes_pair_acceptance.py 的 paired CODE 推广到此不可变候选；运行时 src 字节没有后续变化。实际默认源码准入在本工作区通过，负向 source/container guard 单测通过；严格配对集成仍需最终 HEAD 的 Miniflux metadata compatibility CI，不把 guard 单测当成实际配对验收。

没有执行生产命令、发布、合并或关闭 Issue，也没有操作时区工作区。保持 draft，由主线程独立审查并处理后续源码 pin 与部署。
