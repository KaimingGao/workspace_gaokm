# investment/quant 代码瘦身实施计划

## 仓库调研结论

`quant/` 目录共 37 个 Python 文件，约 13340 行。核心发现按瘦身价值排序：

### 1. 最大冗余：`quant_service_factors.py` 中 5 套 horizon 特定方法（占该文件 ~61%）

[quant_service_factors.py](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/quant/services/quant_service_factors.py) 共 4393 行，其中存在 5 组（t30/t45/t60/t75/t90）几乎完全相同的方法，仅 horizon 字符串不同：

| 方法组 | 数量 | 每组行数 | 小计 |
|--------|------|---------|------|
| `run_tXX_ridge_experiment` | 5 | ~230 | ~1150 |
| `get_tXX_ridge_model` | 5 | ~50 | ~250 |
| `start_tXX_ridge_job` | 5 | ~30 | ~150 |
| `run_tXX_tree_experiment` | 5 | ~180 | ~900 |
| `get_tXX_tree_last_report` | 5 | ~50 | ~250 |
| **合计** | **25** | | **~2700** |

**背景**：core 层已在前期完成合并（`horizon_ridge.py`、`horizon_tree.py`），但 service 层仍保留 5 份拷贝。每组方法仅在 horizon key（如 `"t30"`）、任务名、job slot（`t30_ridge_job` 等）、错误消息上有差异。

**约束**：方法名（`run_t30_ridge_experiment` 等）是 Web API 与测试的公共契约，必须保留（可作为薄别名转发到泛化方法）。

### 2. 纯别名文件：`quant_service_portfolio.py`

[quant_service_portfolio.py](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/quant/services/quant_service_portfolio.py) 共 13 行，仅从 `quant_service_replay` re-export `QuantPortfolioMixin` / `QuantReplayMixin`，注释明确写"实现已迁至…本模块保留别名"。仅被 2 个测试文件和 1 个文档引用。

### 3. 重复 import 降级块：`quant_report_export.py`

[quant_report_export.py](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/quant/services/quant_report_export.py) 中 `_heuristic_from_row`（L49-58）和 `_yhat_from_row`（L83-92）包含完全相同的 `try/except import looks_like_legacy_heuristic_score` 降级块。

### 4. `factor_ols.py` 暴露 core 私有函数

[factor_ols.py](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/quant/research/factor_ols.py) L27-36 的 `__all__` 暴露了 `core.research.factor_ols_fit` 的内部函数（`_fit_ols_once`、`_ols_with_intercept`、`_prepare_complete_panel`）。测试从 `quant.research.factor_ols` import 这些私有函数。

### 5. `quant/ops/__init__.py` 的 `__all__` 与懒加载不一致

[ops/__init__.py](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/quant/ops/__init__.py)：`build_daily_health` 列在 `__all__` 但走 `__getattr__` 懒加载；`build_eval_routing_map`、`build_quant_package_info` 懒加载但不在 `__all__`。

### 6. `daily_presets.py` 遗留 flag 别名（暂不动）

[daily_presets.py](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/quant/ops/daily_presets.py) 的 `PRESET_FLAG_ALIASES`（`paper_holding_cycle`→`paper_run` 等）仍被 Web API 层（`web/schemas/strategy_ops.py`、`web/routers/daily.py`、`services/daily_service.py`）使用，属于公共接口，本次不清理。

---

## 文件与模块

- `quant/services/quant_service_factors.py`：合并 25 个 horizon 方法为 2 个泛化方法 + 25 个薄别名
- `quant/services/quant_service_portfolio.py`：删除
- `tests/test_p94_quant.py`、`tests/test_quant_actions.py`：更新 import
- `quant/services/quant_report_export.py`：提取重复 import 块为模块级 helper
- `quant/research/factor_ols.py`：从 `__all__` 移除私有 core 函数，更新测试 import
- `tests/test_p86_quant.py`：改为从 `core.research.factor_ols_fit` import 私有函数
- `quant/ops/__init__.py`：统一 `__all__` 与懒加载

## 实施步骤

### Phase 1：低风险快速清理

1. **删除 `quant_service_portfolio.py`**
   - 更新 `tests/test_p94_quant.py:20` 和 `tests/test_quant_actions.py:9`，改为 `from quant.services.quant_service_replay import QuantPortfolioMixin`
   - 删除文件

2. **消除 `quant_report_export.py` 重复 import 块**
   - 在模块顶部定义 `_resolve_legacy_heuristic_checker()` helper（封装 try/except import + 降级函数）
   - `_heuristic_from_row` 和 `_yhat_from_row` 改为调用该 helper

3. **修正 `quant/ops/__init__.py`**
   - 将 `build_eval_routing_map`、`build_quant_package_info` 加入 `__all__`，或移除懒加载改为顶部 import（统一风格）

### Phase 2：合并 5 套 ridge horizon 方法（`quant_service_factors.py`）

4. **新增泛化 ridge 方法**：
   - `_run_horizon_ridge_experiment(self, horizon, *, ...)` — 接收 horizon key 参数，内部用 `f"{horizon}_ridge"` 构造 task/path/job 名
   - `_get_horizon_ridge_model(self, horizon)`
   - `_start_horizon_ridge_job(self, horizon, *, ...)`
   - 提取 job slot 映射表：`{"t30": t30_ridge_job, "t45": t45_ridge_job, ...}`
   - 保留 `@records_experiment` 装饰器（需将 key 参数化）

5. **保留 15 个公共方法名作为薄别名**：
   - `run_t30_ridge_experiment` → `self._run_horizon_ridge_experiment("t30", ...)`
   - 其余 t45/t60/t75/t90 同理
   - 确保 `@records_experiment("t30_ridge")` 等装饰器保留在别名上（或迁移到泛化方法内通过参数记录）

6. **验证**：`tests/test_t30_head.py`、`tests/test_t60_head.py`、`tests/test_t90_head.py` 通过

### Phase 3：合并 5 套 tree horizon 方法

7. **新增泛化 tree 方法**：
   - `_run_horizon_tree_experiment(self, horizon, *, ...)`
   - `_get_horizon_tree_last_report(self, horizon)`

8. **保留 10 个公共方法名作为薄别名**：
   - `run_t30_tree_experiment`、`get_t30_tree_last_report` 等转发到泛化方法

### Phase 4：清理 `factor_ols.py` 私有函数暴露

9. **从 `quant.research.factor_ols.__all__` 移除 `_fit_ols_once`、`_ols_with_intercept`、`_prepare_complete_panel`**
   - 保留 import 语句（内部使用），仅从 `__all__` 移除
   - 更新 `tests/test_p86_quant.py` 中对这些函数的 import 路径为 `core.research.factor_ols_fit`

## 依赖与注意事项

- **API 契约不可破**：`run_tXX_*`、`get_tXX_*`、`start_tXX_*` 方法名被 Web 路由（`web/routers/quant_research.py`）和测试直接调用，必须保留为公共方法（即使内部是薄转发）
- **`@records_experiment` 装饰器**：当前硬编码 key（如 `"t30_ridge"`），合并时需在别名方法上保留装饰器，或改造装饰器支持动态 key
- **job slot**：`core.job_progress` 中 `t30_ridge_job` 等是独立的 slot 实例，泛化时需通过映射表分发
- **`factor_ols` 私有函数**：测试 `test_p86_quant.py` 依赖这些内部函数做白盒测试，需同步更新 import 路径
- **不在本次范围**：`daily_presets.py` 的 flag 别名（Web API 公共接口）、`shim_audit.py`（审计工具本身）

## 验证

- 运行 `python -m pytest tests/test_p94_quant.py tests/test_quant_actions.py tests/test_p86_quant.py tests/test_t30_head.py tests/test_t60_head.py tests/test_t90_head.py tests/test_factor_ols_pool.py tests/test_export_report.py -q`
- 运行 `python -m quant.ops.shim_audit` 确认无 legacy import
- `python -c "from quant.services.quant_service import QuantService; print([m for m in dir(QuantService) if 't30' in m])"` 确认公共方法名仍存在
- 检查 `quant_service_factors.py` 行数从 4393 降至约 1700-1900

## 风险

- **装饰器迁移风险**：`@records_experiment` 若实现依赖方法名自动推导，移到泛化方法可能丢失记录。处理：在别名方法上保留装饰器，泛化方法不加装饰器。
- **参数签名差异**：需确认 5 套方法的参数签名是否完全一致（初看一致，合并前逐一比对）。若有差异，以并集为准并在别名中透传。
- **job slot 硬编码**：`t30_ridge_job` 等 slot 在 `core.job_progress` 中可能有独立配置，泛化映射表需完整覆盖 5 个 horizon。
