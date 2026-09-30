# 来源历史范围与干净重建验收

2026-09-30，在隔离云端 checkout 完成；未连接生产阅读器、实时来源或模型，未部署。

## 已实现（#10）

- 来源目录补全阅读器中的手动订阅；展开某来源才查询历史，避免打开设置即批量请求全部 feed。
- 存储条数及最旧/最新 `published_at` 来自该用户可访问的 Miniflux 来源，覆盖已读、未读及仍保留的已移除条目；不以 AI 分析表代替阅读器存储。
- 单独展示 RSS/Atom/RDF 单次窗口条数、日期范围、跨度与检查时间。无日期、Atom 更新时间代替发布时间、空 feed、失败及正在变化的存储结果都有明确状态，不把未知显示成零。
- 无归档完整性承诺，也未执行可选历史回补。需要站点 archive/API/sitemap 适配时应另外实现。
- 独立来源探测不传递阅读器认证或来源密码/Cookie。公开地址解析后固定到经过验证的公网 IP，每次重定向重新验证，保留原 Host/TLS SNI；禁用连接复用，避免同 IP 不同域名共享 TLS 验证。仅允许已有的本机专用 RSSHub/X 适配器地址。
- 探测直连、不复用出站代理，直连不可达及需认证来源显示未知，存储查询仍可用。限制为 2 MiB、3 次重定向、总计 15 秒；快照最多缓存 5 分钟。

## 干净重建修复（#3）

- `patch_scope_ai_filters.py` 补足实际前序阶段的分数方向按钮到统一排序选择器的规范化，兼容已有完整排序选择器，不再依赖未提交的中间步骤。
- `build_frontend.py` 直接调用 Vite 前执行上游标准 `node src/scripts/version-info.js`，生成首次构建必需的版本文件；失败即停止。
- 新回归从固定上游 commit `534eeb97723ac11025de4ec1ac56335072e3be52` 导出到临时目录，运行全部十个 overlay 阶段两次并检查字节一致性；也确认分页初始 offset 未丢失。

## 测试证据

- 应用测试：250 passed。命令 `PYTHONPATH=src:src/kaggle_batch .venv/bin/python`，执行：导入 `initialize_secrets`、`pytest`、`Path`，将 `initialize_secrets.ROOT = Path.cwd()`，然后 `pytest.main(['-q', 'tests'])`。安全配置测试仍只使用测试自己替换的合成 PRIVATE 数据，不读真实密钥。
- 可运行 Kaggle 回归：102 passed、21 subtests passed。命令 `PYTHONPATH=src:src/kaggle_batch .venv/bin/pytest -q src/kaggle_batch --ignore=src/kaggle_batch/test_lane_scheduler.py --ignore=src/kaggle_batch/test_live_scope.py`。
- 最终合并运行：同一安全 ROOT wrapper 下执行 `pytest.main(['-q', 'tests', 'src/kaggle_batch', '--ignore=src/kaggle_batch/test_lane_scheduler.py', '--ignore=src/kaggle_batch/test_live_scope.py'])`，352 passed、21 subtests passed。
- JavaScript：`node --test tests/test*.mjs tests/source_history_component_acceptance.mjs`，10 passed，包括现有分页、scope 状态、抓取内容、阅读会话及新增历史格式/组件生命周期测试。可选组件测试依赖隔离安装的 `jsdom@26.1.0`，不增加生产依赖。
- 新 Python 文件 Ruff 检查及 `git diff --check` 通过。
- 从上述精确上游 commit 的独立干净 clone 应用全部 overlay，执行标准版本 prebuild 后完成 `/inbox/` Vite 构建与 `validate_reader_bundle` 校验。构建同时包含历史范围与额度保护 UI；未发布构建产物。

## 明确未验收

- 完整 Kaggle 收集仍因仓库缺少 `recovery_policy.py` / `exception_audit.py` 无法运行上述两个测试文件，未声称全仓零缺口。
- 原生 Chromium 被执行环境拒绝创建 singleton socket；云浏览器也拒绝 localhost，因此桌面/移动真实浏览器布局验收未完成。已通过的组件生命周期测试覆盖延迟展开、重复点击、失败重试、关闭中止及重开后的旧响应隔离；不能替代视觉验收。
- 提供 `tests/history_browser_acceptance.py` 供允许本地 Chromium 的环境继续验证。它只接受本地构建目录，并 mock 所有 API/外网请求，不应改成生产地址。
