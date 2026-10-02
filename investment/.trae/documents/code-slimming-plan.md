# 系统代码瘦身规划

## 调研结论

对 `core/`、`adapters/`、`services/`、`quant/`、`research/`、`web/`、`skills/`、`scripts/`、`evals/` 进行了全面扫描，识别出以下几类可清理对象：

1. **确认死代码**：定义并导出但从未被调用的函数/模块
2. **已规划遗留清理**：已有清理文档但尚未执行的遗留兼容层
3. **遗留兼容层**：为旧文件名/旧参数保留的兼容代码，新文件已就位
4. **废弃 API**：返回 `deprecated=True` 的接口
5. **待确认 CLI 脚本**：未被任何脚本/代码引用的顶层 research 脚本

### 已确认无死代码的区域
- `core/signal/dual_score/` 全部子模块均被外部引用
- `core/signal/factors/` 全部因子文件均被使用（部分通过 registry，部分直接 import）
- `web/routers/` 全部 15 个 router 均在 `app.py` 注册
- `adapters/` 全部通过 `skills/` 或 `bind.py` 接入
- `core/signal/` 大部分模块在回测/评分/调仓链中使用

---

## Phase 1：确认死代码（低风险，直接删除）

### 1.1 `core/research/oos_slim.py`

**现状**：`slim_oos_gate()` 函数仅在 `core/research/__init__.py` 中导出，全仓库无任何调用点。

**清理动作**：
- 删除 `core/research/oos_slim.py`
- 从 `core/research/__init__.py` 移除 `from core.research.oos_slim import slim_oos_gate` 及 `__all__` 中的 `"slim_oos_gate"`

**验证**：`python -m pytest tests/ -q` 全绿；`grep -r slim_oos_gate` 无结果。

---

## Phase 2：已规划遗留清理（低风险，文档已就绪）

> 来源：`.trae/documents/cleanup-legacy-strategy-code.md`

### 2.1 策略 ID 别名清理

**文件**：`core/backtest/strategies.py`
- 删除 `STRATEGY_ALIASES` 字典
- 简化 `resolve_strategy_id()` 为直接返回 canonical 名

### 2.2 遗留纸面评分键清理

**文件**：`core/strategy.py`
- 删除 `_LEGACY_PAPER_SCORE_KEYS` 元组
- `apply_strategy_to_paper()` 移除 legacy 键剥离循环
- `promote_strategy()` 移除 legacy 键跳过分支

### 2.3 数据文件清理
- `data/strategy_promoted.json`：删除 `paper_rules` 中 4 个 legacy 键（`min_score`/`add_score`/`min_hold_score`/`reduce_score`）
- `data/paper.example.json`：删除 `rules` 中 `min_score`/`min_hold_score`

### 2.4 Skill 配置 & 测试清理
- `skills/backtest/tool_config.json`：移除"旧名 signal_v1 仍可用"描述
- `tests/test_q2_q5_strategy.py`：删除 3 个 legacy 测试方法

**验证**：`python -m pytest tests/test_q2_q5_strategy.py tests/test_backtest.py -v` 全绿。

---

## Phase 3：遗留兼容层（中风险，需迁移验证）

### 3.1 `co_ridge.py` 旧文件名兼容

**现状**：`co_model_path_legacy()` / `co_last_report_path_legacy()` 指向旧 `on_ridge_*` 文件名。`data/live/` 中同时存在 `on_ridge_*.json`（3 个）和 `co_ridge_*.json`（3 个）。

**清理动作**：
- 确认 `co_ridge_model.json` / `co_ridge_last_report.json` / `co_ridge_model_research.json` 内容与 `on_ridge_*` 一致后，删除 `on_ridge_*.json`
- 删除 `co_model_path_legacy()` / `co_last_report_path_legacy()`
- `save_co_last_report()` 移除 legacy 双写
- `load_co_last_report()` 移除 legacy 回退读
- `persist_co_model()` 移除 legacy 双写/回退

**风险**：若 `co_ridge_*` 文件非最新，删除 `on_ridge_*` 后可能读到旧模型。需先比对文件内容和 mtime。

### 3.2 `run_portfolio_neutral_compare` 废弃接口

**现状**：`quant/services/quant_service_replay.py` 中 `run_portfolio_neutral_compare()` 直接返回 `{"deprecated": True}`。但 `web/routers/quant_backtest.py` 仍暴露 `/api/quant/portfolio-neutral-compare`，且 `services/daily_service.py` 仍有 `portfolio_neutral_compare` flag。

**清理动作**（需确认日报不再依赖中性化对照后）：
- 从 `web/routers/quant_backtest.py` 移除该路由（或返回 410）
- 从 `services/daily_service.py` 移除 `portfolio_neutral_compare` flag 及相关逻辑
- 从 `quant/services/quant_interpret.py` 移除中性化对照格式化逻辑
- 从 `quant/services/quant_report_export.py` 移除 `build_neutral_compare_export_section`

**前置条件**：确认日报/导出报告中无中性化对照节的消费者。

### 3.3 回测请求遗留参数

**文件**：`web/schemas/backtest.py`、`quant/services/quant_service_replay.py`
- `universe_fit_tiers` 字段标注 `Deprecated/ignored`，仅 API 兼容
- `legacy_kw` 旧引擎参数处理（`engine != paper_replay` 时记录 ignored）

**清理动作**：
- 从 `PaperReplayBacktestRequest`（或对应产品回测模型）移除 `universe_fit_tiers`
- 移除 `run_portfolio_backtest` 中 `legacy_kw` 处理逻辑

### 3.4 `rank.min_score` 废弃字段

**文件**：`core/signal/config.py`
- `rank.min_score` 标注 deprecated，生产改用 `scoring.min_predicted_score`

**前置条件**：需先确认 `skills/signal/engine.py` 中 `rank_candidates(min_score=...)` 的迁移路径。

### 3.5 `include_legacy_probe` 遗留探针

**文件**：`quant/services/quant_service_ops.py`
- `include_legacy_probe` 默认关闭，开启时运行旧式 factor report/OLS 并标记 `deprecated_for_scoring=True`

**清理动作**：移除 `include_legacy_probe` 分支及相关 appendix 逻辑。

---

## Phase 4：待确认 CLI 脚本（需用户确认）

以下 `research/` 顶层脚本未被任何 `.sh` 脚本或 Python 代码引用，可能是废弃的一次性脚本：

| 脚本 | 用途推测 |
|------|---------|
| `research/backtest_scan.py` | 回测扫描 |
| `research/cache_cli.py` | 缓存 CLI |
| `research/factor_experiment.py` | 因子实验 |
| `research/factor_ols_run.py` | 因子 OLS |
| `research/paper_rebalance_run.py` | 纸面调仓 |
| `research/quant_export_run.py` | 量化导出 |
| `research/sector_map_sync_run.py` | 板块映射同步 |
| `research/signal_diff_export_run.py` | 信号差异导出 |
| `research/threshold_suggest_run.py` | 阈值建议 |

**处理方式**：请用户确认是否仍手动使用，未使用的可删除。

---

## Phase 5：前端 TopK 契约收口（已部分完成）

> 来源：`.trae/documents/cleanup-topk-backtest-contract.md`

Phase 1（参数网格产品面下线）已完成。本刀（产品回测契约只暴露 `paper_replay`）待执行：

- 新增 `PaperReplayBacktestRequest`，从产品契约删除 TopK 参数字段
- `quant_portfolio_backtest` 路由改用新模型，服务端写死 `paper_replay`
- 前端 `domain_backtest.js` / `bt_result.js` 移除 `topk_research` 分支
- `replay_panel.html` 清理 hidden 字段
- 文档 `docs/quant.md` §5 重写

---

## 实施顺序与依赖

```
Phase 1 (死代码) ──────────────── 无依赖，最先执行
Phase 2 (策略遗留) ────────────── 无依赖，可并行
Phase 3.1 (co_ridge 文件名) ───── 需先比对 live 数据文件
Phase 3.2-3.5 (兼容层) ────────── 需确认消费者已迁移
Phase 4 (CLI 脚本) ────────────── 需用户确认
Phase 5 (前端契约) ────────────── 独立，可随时执行
```

## 验证策略

每阶段完成后运行：
```bash
# 核心测试
python -m pytest tests/ -q

# 编译 + lint
make check

# 前端 JS 守卫
python scripts/check_frontend_js.py
```

## 不在本次范围

- `core/backtest/topk_backtest.py` 及相关 OOS/中性化引擎：仍被研究路径调用
- `adapters/market/baostock_minute.py` / `sina_minute.py`：作为分钟数据 fallback 仍在使用
- `core/signal/factors/` 下非 registry 因子（adaptive/correlation/cost 等）：作为工具模块直接被 exec/scorer 调用
- `data/` 下的 `.lock` 文件：运行时锁文件
