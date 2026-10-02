# 历史 overlay 升级覆盖与当前阻碍（2026-10-02）

Refs #86。独立工作区 `/home/ubuntu/ui-review-20261002-upgrade`，分支 `fix/reader-overlay-upgrade`，目标基线 `15e0c8d5236dc006ff97a3846c03feaf710dc13b`。

## 已确认的覆盖缺口

已有 CI 从 pinned ReactFlux pristine 源码应用当前 patch，再重复当前 patch。重复构建等价只证明当前状态的幂等性，没有验证旧版本生成树和 `runtime/reactflux-original` 备份一起保留时的版本升级。

新增回归从不可变的公开应用 commit `da5a6222779517b92782bf19e2a2166801676fb4` 和 ReactFlux pin `534eeb97723ac11025de4ec1ac56335072e3be52` 生成旧 overlay；只替换应用 authoring 的 `src`、`patches`、`frontend-review`，保留生成前端和所有原始备份；按 `build_frontend.py` 的十一阶段顺序升级，再验证当前版本重复构建与 pristine 目标完整源树的逐字节一致性。CI 显式获取精确历史 commit；不可用时测试失败，不能静默跳过。

在最终 overlay 前向 SearchAndSortBar 注入未知变体，验证 installer 拒绝且整个前端源树零写入；恢复公开 before 后重新执行成功。没有添加任何准入 hash，也没有放宽 drift 检查。

## 独立复现结果

公开 da5 基线升级到 15e0 **通过，未重现生产失败**。完整阶段 hash 见 [公开来源证据](../../artifacts/overlay-upgrade/public-baseline-stage-hashes.json)。

- da5 最终 SearchAndSortBar SHA256：`91b9d316610966db26b5cb5e6d1278ed0b94f53bbe397c5d19955352d21a3734`。
- 15e0 最后 overlay 前 SHA256：`59cccf7ed8134de914ee7688a7fa80f87a2fa428f29b0ad255010445c40d1d36`，等于已审查的 `frontend-review/before`。
- 升级、pristine 与重复最终 SHA256：`25e801d1c55b2597f614d1a2e5a7a141d6217d417044f999a0d6d493f6559f2a`。

这份公开重建不能替代真实失败输入。本 PR 当前只补已确认的回归覆盖，不能声称已修复此次生产准入失败。

## 尚需取证

等待父线程/sole operator 提供此次拒绝前的精确 SearchAndSortBar.jsx、SHA256、实际阶段顺序和拒绝日志，消费端需确认文件存在并核对 hash。捕获文件仅放在忽略的 runtime 内，不提交服务器或私有数据。取证后对比公开重建，重现真正的历史差异，再实施最小、有公开来源证据且 fail-closed 的修复。

没有运行生产命令、发布、合并或关闭 Issue；未操作时区任务工作区。源代码准入和后端 paired CODE pin 均未改变。独立审查及部署由主线程负责。

## 验证

目标回归（pristine、历史升级、未知漂移与 installer）：4 passed。按正式 CI prepare 阶段准备源树后，完整 Python：794 passed、161 subtests passed；exact-head Reader CI 结果以本 PR 记录为准。本次未修改 UI；没有新增实机验证证据。
