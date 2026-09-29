# LightGBM + LambdaRank 接入方案

## Context

当前系统的树模型仅支持 XGBoost 与 numpy_gbm fallback（见 `core/research/tau_tree.py` 的 `resolve_tree_backend`），LTR 侧只有手写的线性 RankNet（`core/research/oo_rank_pairwise.py:fit_ranknet_linear`，pairwise softplus loss，numpy 梯度下降）。记忆 2026-09-25 已确认 LinearRankNet/Ridge 在 1 天 horizon 上 IC≈-0.02（近随机），并已规划「应用 LightGBM LambdaRank」作为优化路径之一。

本次接入目标：
1. 给 `tau_tree`（及 `t30~t90_tree`、`horizon_tree`）新增 **lightgbm 回归后端**，与现有 XGBoost/numpy_gbm 并列可选。
2. 给 `oo_rank_pairwise` 新增 **LightGBM LambdaRank 后端**，与现有线性 RankNet 并列可选。
3. LightGBM 作为可选依赖（try/except import），未安装时回退到现有后端；测试在缺依赖时自动 skip。
4. 不动 live ranking / 回测入口，保持「研究影子头」契约。

## 接入点（已核对）

- [core/research/tau_tree.py#L57-L74](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/tau_tree.py#L57-L74) `resolve_tree_backend()` — 后端分发入口
- [core/research/tau_tree.py#L337-L441](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/tau_tree.py#L337-L441) `_fit_numpy_gbm`/`_fit_xgboost`/`_predict_xgboost` — 现有后端实现模板
- [core/research/tau_tree.py#L549-L664](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/tau_tree.py#L549-L664) `fit_tau_tree_report()` — 主训练入口的 dispatch 处
- [core/research/horizon_tree.py#L100-L150](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/horizon_tree.py#L100-L150) `pack_tree_return_model()` + `serialize_xgboost_booster()` — 模型序列化与 `predict_tree_p_up` 消费侧
- [core/research/oo_rank_pairwise.py#L437-L527](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/oo_rank_pairwise.py#L437-L527) `fit_ranknet_linear()` — 线性 RankNet 主训练函数（参照模板）
- [core/research/oo_rank_pairwise.py#L261-L348](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/oo_rank_pairwise.py#L261-L348) `sample_top_bottom_pairs()` — pair 采样逻辑（LightGBM LambdaRank 不复用，改为整日 group）
- [core/research/oo_rank_pairwise.py#L813-L899](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/oo_rank_pairwise.py#L813-L899) `fit_oo_rank_report()` — LTR 主入口的 dispatch 处
- [core/research/oo_rank_pairwise.py#L547-L599](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/oo_rank_pairwise.py#L547-L599) `predict_oo_rank_from_features` / `apply_oo_rank_scores` — 推理路径
- [core/research/tau_ridge.py#L116-L164](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/tau_ridge.py#L116-L164) `_predict_rows()` — 线性推理实现，LightGBM 路径需另写并在此分发
- [core/research/oo_rank_panel.py#L179-L253](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/core/research/oo_rank_panel.py#L179-L253) `build_oo_rank_day_panels()` — 每日 group 结构（LambdaRank 直接复用）

## 实施步骤

### 步骤 1：tau_tree 新增 lightgbm 回归后端

文件：`core/research/tau_tree.py`

1. `resolve_tree_backend()`：新增分支识别 `"lightgbm"/"lgb"` → 返回 `"lightgbm"`；`"auto"` 优先级 XGBoost → LightGBM → numpy_gbm（保持 XGBoost 优先以减少 surprise）。
2. 新增 `_fit_lightgbm(x, y, w, *, n_estimators, max_depth, learning_rate, subsample, objective="regression")`，参照 `_fit_xgboost`：
   - `import lightgbm as lgb`（调用方负责异常，与 XGBoost 一致）
   - `lgb.Dataset(x, label=y, weight=w)`
   - `lgb.train({...params...}, dtrain, num_boost_round=n_estimators)`，参数对齐 XGBoost 现有项（max_depth/learning_rate/subsample/colsample_bytree/lambda/min_child_weight/seed/verbose）
   - 返回 `(booster, gain)`，gain 由 `booster.feature_importance(importance_type="gain")` 转换并归一化
3. 新增 `_predict_lightgbm(booster, x)` → `np.asarray(booster.predict(x), dtype=np.float64)`。
4. `fit_tau_tree_report()` 主入口的 backend dispatch：在现有 `if/elif` 链增加 `elif backend == "lightgbm"` 分支调用上述函数。
5. 序列化：在 `horizon_tree.py:pack_tree_return_model()` 增 `elif eng == "lightgbm"` 分支：调用新增 `serialize_lightgbm_booster(model_obj)`（`booster.save_model()` → 字符串 → base64 → `{format: "lightgbm_string", payload_b64}`），并新增 `deserialize_lightgbm_booster(blob)` 配对。
6. `predict_tree_p_up()` 在 backend 分发处增 `elif backend == "lightgbm"`：用 `lgb.Booster.load_model()` 加载并 predict。

### 步骤 2：oo_rank 新增 LightGBM LambdaRank 后端

文件：`core/research/oo_rank_pairwise.py`

1. 新增 `fit_ranknet_lightgbm(days, *, feature_names=None, n_estimators=120, max_depth=5, learning_rate=0.05, subsample=0.85, colsample_bytree=0.9, num_leaves=31, min_child_samples=20, l2=1.0, label_mode="rank_int")`：
   - 复用 `_fit_zscore(days, names)` 算 `means/stds`（保持与线性 path 一致的 z-score 输出，便于复用 `z_means/z_stds` 字段）
   - 遍历 `days`，每个 day：
     - 跳过 `len(ys) < 4` 或 `len(xs) != len(ys)`
     - `Z = _z_matrix(xs, names, means, stds)`
     - `labels = _to_rank_labels(ys, mode=label_mode)`：`rank_int` → `scipy.stats.rankdata` 或纯 numpy 实现的 int rank（0..n-1），高 ys = 高 label
     - 收集 `group_sizes.append(len(ys))`
   - 拼接所有 `Z`/`labels`，`group=group_sizes`
   - `lgb.LGBMRanker(objective="lambdarank", metric="ndcg", n_estimators=..., ...).fit(X, y, group=group_sizes)`
   - 输出 dict 与 `fit_ranknet_linear` 同形：`{success, coefficients: {name: importance}, active_features, zscore_means, zscore_stds, z_means, z_stds, standardized: True, solver: "lightgbm_lambda", booster_b64, n_pair_days, n_pairs(此处=total samples), backend: "lightgbm_lambda"}`
   - 失败路径返回 `{success: False, error: ...}`（与线性 path 一致）
2. `_resolve_oo_rank_fit()` 不改（仍按 `coefficients` 存在判断），但新增字段 `solver` 用于 dispatch。
3. `predict_oo_rank_from_features()` / `apply_oo_rank_scores()`：
   - 在 `_predict_rows()` 调用前判断 `model["solver"] == "lightgbm_lambda"` → 走新增 `_predict_rows_lightgbm(model, [row])`：z-score → `lgb.Booster.load_model` → predict → 返回浮点分数（非概率，与线性 path 同语义「相对分」）
4. `fit_oo_rank_report()` 新增参数 `backend: str = "ranknet_linear"`（默认不变，向后兼容）：
   - `backend == "lightgbm_lambda"` → 调 `fit_ranknet_lightgbm(days_tr, feature_names=feat_names, ...)`，其余报告字段（IC、TopK、OOS）复用现有评估代码（评估只看 `coefficients`/`active_features` + `predict_oo_rank_from_features`，对 solver 透明）

### 步骤 3：测试

参照 [tests/test_t90_tree.py](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/tests/test_t90_tree.py) 和 [tests/test_oo_rank_pairwise.py](file:///Users/gaokaiming/Workspace/workspace_gaokm/investment/tests/test_oo_rank_pairwise.py) 的 fixture 模式（合成 K 线 + 分钟线）：

1. `tests/test_tau_tree_lightgbm.py`：用 `unittest.skipUnless(_has_lightgbm(), ...)` 装饰；测试 `resolve_tree_backend("lightgbm")`、`_fit_lightgbm`/`_predict_lightgbm` 基本形状、`pack_tree_return_model` + `predict_tree_p_up` 的 round-trip。
2. `tests/test_oo_rank_lightgbm.py`：同 skip 装饰；构造合成 `days`，测试 `fit_ranknet_lightgbm` 返回 `success=True`、`predict_oo_rank_from_features` 返回非 None、与线性 path 在随机数据上方向一致（同向相关性 > 0）。
3. 不修改现有 `test_t90_tree.py` / `test_oo_rank_pairwise.py`（确保旧 path 不回归）。

## 关键设计决策

1. **LightGBM 为可选依赖**：所有 `import lightgbm` 在调用点 try/except；`resolve_tree_backend("lightgbm")` 在缺依赖时抛 `ImportError("未安装 lightgbm")`（与 XGBoost path 一致）。
2. **Z-score 保持一致**：LambdaRank 路径仍输出 `zscore_means`/`zscore_stds`，使 model doc 字段统一；推理侧统一走 z-score → predict。
3. **LambdaRank label**：用 `rank_int`（日截面 rank，0..n-1，等距）作为默认；这是 LightGBM LambdaRank 文档推荐的 label 形式。后续可加 `label_mode="gain"`（用 |Δy| 加权 rank）作为可选。
4. **不接 live**：保持 `oo_rank_pairwise.py` 顶层注释「不进 live ranking / rank_lots 入场」契约不变；LightGBM 后端同样仅用于研究 OOS 对照。
5. **不动 Alpha158**：本次只接 LightGBM，Alpha158 因子导入留作后续单独任务。
6. **不引入 scipy 强依赖**：`rank_int` 用纯 numpy `argsort` + 线性插值实现，避免新增依赖。

## 验证

按以下顺序跑：

1. `python -c "import lightgbm; print(lightgbm.__version__)"` — 确认依赖（若失败，测试自动 skip，代码仍可合入）
2. `python -m pytest tests/test_tau_tree_lightgbm.py -v` — 新增 lightgbm 回归后端测试
3. `python -m pytest tests/test_oo_rank_lightgbm.py -v` — 新增 LambdaRank 测试
4. `python -m pytest tests/test_t90_tree.py tests/test_t60_tree.py tests/test_t30_tree.py tests/test_oo_rank_pairwise.py -v` — 确认旧 path 不回归
5. `python -m pytest tests/test_horizon_tree.py -v` — 确认 `pack_tree_return_model` / `predict_tree_p_up` 旧 path 不回归
6. 手动 smoke：在已有 `tau_tree_last_report.json` 流程上以 `backend="lightgbm"` 跑一次，对比 OOS IC 与 XGBoost path。

## 不做的事

- 不动 live ranking / `rank_lots` / paper_replay（研究影子头契约不变）
- 不接 Alpha158（独立后续任务）
- 不替换 XGBoost 为默认后端（默认仍为 XGBoost，避免 surprise）
- 不引入 MLflow / 实验追踪（独立后续任务）
- 不改 `walk_forward.py` rolling 流程
