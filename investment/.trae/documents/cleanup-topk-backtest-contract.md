# 契约收口：产品回测只认 paper_replay

## Context

历史回测产品路径已经切到 `paper_replay` / `rank_lots`：

- `/api/quant/portfolio-backtest` 非 `paper_replay` 直接拒绝
- 日报摘要 `summarize_portfolio_backtest` 走 `backtest_paper_replay`
- `/replay` 隐藏字段 `engine=paper_replay`；页上无 Top K / Horizon 控件
- live Follow 走 `rank_lots`，不再用横截面 TopK 调仓

但契约层仍把 **TopK 研究引擎**（`topk_research` / `backtest_topk_equal_weight`）当成产品回测的一部分：请求模型塞满 K/h/dropout、前端还分支 `topk_research`、文档 §5 仍写「默认 TopK」。

**目标（本刀）**：产品回测契约只暴露 `paper_replay` 参数。不删 `topk_backtest.py`，不拆参数网格 / OOS 门禁 / 中性化对照。

## 不在范围内（下一刀才碰）

| 模块 | 原因 |
|------|------|
| `core/backtest/topk_backtest.py` | 组 OOS / 中性化对照仍调用 |
| `weight_oos_gate.py` / `cluster_oos.py` | 组级 OOS 对照内核 |
| `portfolio_neutral_compare.py` | 中性化 A/B 仍跑 TopK 两腿 |
| `topk_benchmark.py` | `paper_replay` 超额仍用 |
| `simulate_cross_section_rebalance` / `in_topk` | 遗留调仓链，不是回测引擎 |
| `aggregate_stock_backtests` | skills 单票聚合，不是组合 TopK |

参数网格产品面（API / Job / JS）已在 Phase 1 下线。

---

## 变更清单

### 1. 拆请求模型 — `web/schemas/backtest.py`

`PortfolioBacktestRequest` 现在被两个口共用：

- `POST /api/quant/portfolio-backtest` → 产品路径，只跑 `paper_replay`
- `POST /api/quant/portfolio-neutral-compare` → 研究路径，仍要 `top_k` / `horizon_days` / `dropout_n` / `weight_mode`

**做法**：新增 `PaperReplayBacktestRequest`，原模型留给中性化对照（可改名 `TopkResearchRequest`，非必须）。

`PaperReplayBacktestRequest` 只留产品字段：

```python
class PaperReplayBacktestRequest(BaseModel):
    codes: Optional[list] = None
    lookback: int = Field(default=30, ge=10, le=500)
    apply_costs: bool = True
    fetch_fundamentals: Optional[bool] = False
    exclude_st: bool = True
    min_avg_amount_pctile: Optional[float] = Field(default=None, ge=0, le=90)
    include_benchmark: bool = True
    benchmark_code: str = "000300"
    y_on_alpha: float = Field(default=0.0, ge=0.0, le=1.0)
    fusion_w_trade: float = Field(default=0.6, ge=0.0, le=1.0)
    fusion_w_nowcast: float = Field(default=0.4, ge=0.0, le=1.0)
    rank_enter: float = Field(default=0.012, ge=0.0, le=10.0)
    rank_strong: float = Field(default=0.012, ge=0.0, le=10.0)
```

从产品契约删掉（客户端再传忽略或 422 均可，优先 **不声明字段**）：

- `engine` — 硬编码，禁止再选 `topk_research`
- `top_k` / `horizon_days` / `min_score` / `min_predicted_score`
- `weight_mode` / `max_position_pct` / `max_sector_pct` / `dropout_n`
- `include_wf_slices` / `include_cost_compare` / `wf_n_splits`
- `include_score_ic` / `include_quantile` / `rank_mode`
- `return_model_min_samples` / `return_model_ridge_lambda`

`web/schemas/__init__.py` 导出新模型。

### 2. Router — `web/routers/quant_backtest.py`

`quant_portfolio_backtest`（约 L160–194）：

- body 类型改为 `PaperReplayBacktestRequest`
- 只把上面产品字段传给 `run_portfolio_backtest`
- **不要**再传 `engine=`；服务端写死 `paper_replay`

`quant_portfolio_neutral_compare` 继续用旧 `PortfolioBacktestRequest`（含 K/h）。

### 3. 瘦身 `run_portfolio_backtest` — `quant/services/quant_service_replay.py`

当前签名（L176–209）仍接收一整套 TopK kwargs，`paper_replay` 分支全部不用（`horizon_days` 写死 1，`top_k` 被宇宙只数覆盖）。

- 删掉未使用参数；或留 `**kwargs` 并 log 一次「忽略遗留 TopK 字段」后丢掉（兼容旧客户端一两个版本）
- `engine` 不再当入参；函数内固定 `paper_replay`，去掉 L214–218 的引擎 switch（只留一种）
- Mixin docstring（L174）「历史 TopK 推演」改为「历史 rank_lots / paper_replay」
- `persist_curve` meta（L458–465）不要再写 `top_k` / `horizon_days` / `min_score`；改为 `lookback` / `rank_enter` / `y_on_alpha` / `apply_costs`

`include_wf_slices` / `include_cost_compare` 在 paper_replay 路径里已经是死参数（收尾只做成本假设 / 源审计 / 落盘曲线），随签名一起删。

### 4. 前端 — 去掉 `topk_research` 分支

#### `web/static/js/quant/domain_backtest.js`

| 位置 | 改法 |
|------|------|
| `readPortfolioBtParams` L844–894 | 回测 payload 不再读 `quant-horizon` / `quant-weight-mode` / `quant-dropout-n` / `quant-bt-engine`。`horizon_days` / `weight_mode` / `dropout_n` 只留给 **中性化对照**（`runNeutralCompare` 仍要） |
| `runPortfolioBacktest` L1621–1668 | payload 去掉 `horizon_days` / `weight_mode` / `dropout_n` / `rank_mode` / `engine` / `include_wf_slices` / `include_cost_compare` / `include_score_ic` / `include_quantile`。busy 文案去掉 `engine === "topk_research"`，固定「rank_lots · y_fuse/y_on · 09:30」 |
| L1689 超时提示 | 删「horizon=1 / 改成 horizon 3」——产品回测已无持有期 |
| `frozenBtCaliber` L386–395 | 保留 paper_replay 行；`K{h}` 回退只服务旧日报快照，加注释「仅兼容冻结摘要」 |

中性化对照 `fetch("/api/quant/portfolio-neutral-compare")`（约 L1747）**继续**传 `top_k` / `horizon_days` / `dropout_n` / `weight_mode`。

#### `web/static/js/quant/bt_result.js` L244–257

- 默认引擎已是 `paper_replay`
- 删 `eng === "topk_research" ? " · 引擎 研究Top-K"`；旧快照若带 `topk_research` 可显示「研究 Top-K（已下线）」一行，不要再当可选引擎
- `dropoutNote` 对产品回测恒为 0，可只在 `params.engine === "topk_research"` 时显示

#### `web/static/partials/replay_panel.html` L133–137

隐藏字段：

```html
<input type="hidden" id="quant-horizon" value="1" />
<input type="hidden" id="quant-weight-mode" value="score_budget" />
<input type="hidden" id="quant-dropout-n" value="0" />
<input type="hidden" id="quant-rank-mode" value="predicted_score" />
<input type="hidden" id="quant-bt-engine" value="paper_replay" />
```

产品回测不再读它们。`quant-bt-engine` 可删。`quant-horizon` / `quant-weight-mode` / `quant-dropout-n` 若中性化对照仍 `getElementById`，**先确认对照 UI 不在 replay 页**（`test_web_quant_js_guards` 已断言 replay 无「中性化对照」）——对照在 `/quant`，replay 这四个 hidden 可删。`quant-rank-mode` 一并删。

保留：`quant-lookback` / `quant-on-alpha` / `quant-w-trade` / `quant-w-nowcast` / `quant-rank-enter` / `quant-rank-strong` / `quant-benchmark-code`（若页上仍用）。

改完 bump `web/asset_version.py` 的 `ASSET_V`。

### 5. 文档

#### `docs/quant.md`

重写 **§5 回测链**（L1323–1357），标题改为「回测链（paper_replay / rank_lots）」：

- 入口：`backtest_paper_replay`（Web：`run_portfolio_backtest`；日报：`summarize_portfolio_backtest`）
- 对照表改为 **历史 rank_lots vs live Follow**（都是 y_fuse/y_on · 09:30；历史宇宙=观察池、live 开加受持仓市值上限，不按 `max_positions` 截断）
- 删「默认 K=3 · ŷ_EOD · 关 τ 闸」作为产品回测口径
- 用一小段标明：`topk_research` 仅研究探针（参数网格 / OOS / 中性化），**不进 `/replay`**

同步改：

- L103「组合 TopK 回测」→ 横截面排序仍可中性化；产品回测不走这条
- L995「跑分组 / TopK 回测」→ 跑分组 / 历史回测
- L1400 / L1450 验证链「TopK 回测」→「paper_replay」
- L1568 代码表：产品回测指向 `paper_replay.py`；TopK 标「研究探针」

#### `docs/architecture.md`

- L1168 Replay 列：「② 回溯（paper_replay；中性化对照仍 TopK）」
- L209 / L1229 / L1265 `run_topk` 保留为研究门面，注明 ≠ 产品回测

#### `docs/rebalance-logic.md`

已写 live/历史统一 `rank_lots`。本刀只需交叉链接 §5，不必改调仓逻辑。

### 6. 报告文案 — `quant/services/quant_report_export.py`

- L1144 `engine_label = engine or "topk_research"` → 默认 `"paper_replay"`
- L589–593 保留 `else` 分支：旧日报快照仍可能是 TopK
- 函数名 `_topk_run_config_line` / `_topk_score_axis_note` 本刀可不改名（范围蔓延）

### 7. Skill 文案 — `skills/quant/tool_config.json`

- L3 description「观察池 TopK 回测」→「观察池 rank_lots 历史回测」
- `top_k` 参数：产品回测不再截 K。若该 skill 实际调的是 `/portfolio-backtest`，删 `top_k`；若走研究口则留并写明「仅研究网格 / 中性化」

### 8. 测试

| 文件 | 改法 |
|------|------|
| `tests/test_t3_weight_mode.py` L117–122 | `PortfolioBacktestRequest.engine` 断言：中性化模型可保留 `engine` 或不声明；新产品模型断言无 `engine` 字段 |
| `tests/test_n_star_path.py` L370–376 | `include_wf_slices` 当前 **schema 默认 False，测试却 assertTrue**（已过期）。随字段删除改测 `PaperReplayBacktestRequest` 默认 `apply_costs=True`，去掉 wf 断言 |
| `tests/test_web_quant_js_guards.py` | `id="quant-bt-engine"` / `value="paper_replay"`：若删 hidden，改断言 `engine` 不出现在 payload / JS 写死 rank_lots；`assertNotIn("topk_research"` 可加到 `domain_backtest.js` / `bt_result.js`（旧快照文案「已下线」除外） |
| `tests/test_paper_replay.py` `TestTopkEngineTag` | 本刀保留：引擎文件还在。不要删「`engine == topk_research`」单测 |
| `tests/test_daily_topk_strengthen.py` | 日报已是 paper_replay，本刀不动；`resolve_daily_topk_backtest_kwargs` 仍给中性化对照 |

新增 1～2 个契约测：

```python
def test_portfolio_backtest_rejects_engine_field():
    # PaperReplayBacktestRequest 无 engine；旧 JSON 多传 engine 被忽略或校验失败

def test_replay_route_does_not_call_run_topk():
    # mock run_topk，POST /api/quant/portfolio-backtest 不得命中
```

---

## 验证

```bash
cd /Users/gaokaiming/Workspace/workspace_gaokm/investment

python -m pytest tests/test_t3_weight_mode.py tests/test_n_star_path.py tests/test_web_quant_js_guards.py tests/test_paper_replay.py tests/test_daily_topk_strengthen.py tests/test_p12_quant.py tests/test_daily_ops.py -q
```

可选：起 web 后 `POST /api/quant/portfolio-backtest` 只带 `lookback` / `rank_enter`，确认不再要求 `top_k`；`POST` 带 `engine=topk_research` 应忽略或 422，**不得再跑独立腿**。

`/replay` 点「跑回测」：busy 文案无「研究 Top-K」；成交表引擎注为 rank_lots。

---

## 本刀完成后的状态

```
产品：/replay · 日报摘要     → paper_replay（契约干净）
研究：OOS / 中性化           → 仍 topk_research（显式研究口）
引擎文件                      → 保留
参数网格产品面               → 已下线（POST /api/quant/param-grid → 410）
```

## Phase 1（已做）· 参数网格产品面下线

不迁轴到 `rank_enter`×lookback。产品面没有网格按钮（HTML 早已无 `quant-param-grid-run`），JS/API/Job 槽一并拆掉。

- `POST /api/quant/param-grid` → 410
- 删除 `ParamGridRequest`、`run_param_grid` / `start_param_grid_job`、`quant-param-grid` Job 槽、`param_grid_ui.js`
- `persist_curve` 北极星单测迁到 `tests/test_paper_replay.py`

**仍保留**：`topk_backtest.py`、`weight_oos_gate.py`、`cluster_oos.py`、`portfolio_neutral_compare.py`、`topk_benchmark.py`。
横截面调仓链 `simulate_cross_section_rebalance` 已删除（Follow / 回测走 `rank_lots`）。

下一刀候选：删 `topk_backtest.py` 只在 OOS / 中性化 / 分池有替代或明确改走 paper_replay 之后。
