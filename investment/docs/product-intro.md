# 量化交易工作台 · 产品介绍

> A 股短线量化研究 + 模拟交易平台。核心链路：**选股 → 调仓 → 做T**，覆盖因子研发、ŷ 打分、横截面排序、组合再平衡、日内做T 全流程。

---

## 一、产品概述

本系统是一个面向 A 股短线交易的量化研究与模拟交易平台，采用"单进程 Python 后端 + FastAPI Web 前端"架构，核心能力包括：

| 模块 | 定位 | 核心产出 |
|------|------|----------|
| **选股（Signal）** | 横截面因子打分 + 双预测头融合 | 候选池 Top N 股票及 ŷ 评分 |
| **调仓（Rebalance）** | 基于目标权重的组合再平衡 | 开/加/减/平仓交易单 |
| **做T（T0）** | 底仓日内高抛低吸 | 正向/反向做T 建议与盈亏 |
| **模拟盘（Paper）** | 撮合 + 持仓 + 现金 + 手续费 | 净值曲线、交易记录、做T 盈亏 |
| **Web 工作台** | 可视化研究 + 操作 + 复盘 | 因子 IC、ŷ 曲线、调仓报告、做T 分析 |

技术栈：Python 3.11+ / FastAPI / 自研数据层（AkShare + 本地缓存）/ 原生 JS 前端（无构建工具）。

---

## 二、Web 前端说明

### 2.1 整体架构

前端采用 **服务端渲染 HTML 面板 + 原生 JS 模块** 的轻量架构，无前端构建工具（无 Webpack/Vite），所有 JS 直接由浏览器加载。

- **入口**：`web/app.py` — FastAPI 应用，注册所有路由和静态资源
- **面板 HTML**：`web/static/partials/*.html` — 各功能区的 HTML 片段
- **JS 模块**：`web/static/js/*.js` — 按功能域拆分
- **样式**：`web/static/css/*.css`

### 2.2 功能面板

前端按功能域划分为多个面板（panel），每个面板对应一个 HTML 片段和一组 JS 模块：

| 面板 | HTML | 主要 JS | 功能 |
|------|------|---------|------|
| **模拟盘（Paper）** | `paper_panel.html` | `paper.js`, `paper/*.js` | 持仓表、调仓、做T、规则、日志 |
| **量化研究（Quant）** | `quant_panel.html` | `quant.js`, `quant/*.js` | 因子、ŷ、聚类、回测、观察池 |
| **观察池（Watching）** | `watching_panel.html` | `watching_table_island.js` | 候选股票池管理与打分展示 |
| **策略（Strategy）** | `strategy_panel.html` | `strategy.js` | 策略组合管理与运行 |
| **仪表盘（Dashboard）** | `dashboard_panel.html` | `dashboard.js`, `lw_charts.js` | 净值曲线、KPI、风险指标 |
| **回测（Evals）** | `evals_panel.html` | `evals.js` | 历史回测结果与对比 |
| **平台（Platform）** | `platform_panel.html` | `platform.js` | 调度 / 审计 |
| **行情回放（Replay）** | `replay_panel.html` | — | 历史行情逐 Bar 回放调试 |
| **AI 抽屉** | `ai_drawer.html` | `ai_drawer.js` | LLM 模型 + 自然语言命令 |

### 2.3 模拟盘面板（Paper）详解

模拟盘是日常交易操作的主界面，包含以下子模块：

```
paper_panel.html
├── 持仓表（holdings_ui.js）        — 实时持仓、ŷ、做T 标记、可操作列
├── 调仓执行（execution_ui.js）     — 目标权重输入、调仓报告预览与执行
├── 调仓规则（rules_ui.js）         — rank入场 / rank强 / 市值上限
├── 做T 面板（t0_ui.js, t0_table.js, t0_viz.js）— 做T 参数、实时触发、盈亏汇总
├── 日志（logs_ui.js）              — 调仓 / 做T / 错误日志
└── 北极星（north_star_ui.js）      — 组合层面目标与偏差监控
```

### 2.4 量化研究面板（Quant）详解

量化研究台覆盖因子研发到策略验证的全链路：

```
quant_panel.html
├── 因子面板（factor_*.js）         — 因子 IC、相关性、元数据管理
├── ŷ 可视化（yhat_viz.js）— 双预测头曲线、截面分布
├── 聚类（cluster_*.js）            — 信号簇构建与回测
├── 回测（bt_*.js, domain_backtest.js）— 交易明细、结果对比
├── 观察池（watching_*.js）         — 候选池构建、DQ、持仓
└── 研究网格（research_grid.js）    — 多维度参数扫描
```

### 2.5 实时通信

- **WebSocket**：`web/routers/live_ws.py` — 推送行情、ŷ 更新、做T 触发、持仓变化
- **轮询**：`job_poll.js` — 长任务（调仓、回测）进度轮询

---

## 三、选股策略说明

选股系统采用 **多因子横截面打分 + 双预测头融合** 架构，输出 1～3 天周期的候选股票排序。

### 3.1 整体流程

```
观察池（watching.json）
    ↓
逐股 score_stock()
    ├── 取日线（本地缓存优先，不足时 DataService 补远端）
    ├── 因子计算（compute_configured_factors）
    ├── 前置风控检查（pre_trade_check）
    └── scorer.score_bars() → predicted_score
    ↓
横截面排序（rank_cross_section）
    ├── 按 predicted_score 降序
    ├── 质量门禁过滤（production_ok）
    └── Top N 输出
```

### 3.2 因子体系

因子库位于 `core/signal/factors/`，按维度分类：

| 类别 | 因子 | 说明 |
|------|------|------|
| **动量** | `momentum`, `idio_momentum` | 价格趋势、特质动量 |
| **量价** | `volume_price`, `money_flow` | 量价配合、资金流向 |
| **相对强弱** | `relative_strength`, `ma_slope` | 相对大盘强弱、均线斜率 |
| **波动** | `volatility`, `amihud` | 波动率、流动性冲击 |
| **反转** | `reversal`, `weekly_confirm` | 短期反转、周线确认 |
| **技术形态** | `technical_pattern`, `overheat` | K线形态、过热检测 |
| **基本面** | `value`, `quality`, `growth`, `earnings_yield`, `dividend` | 估值、质量、成长、股息 |
| **情绪** | `alt_sentiment`, `llm_sentiment` | 另类情绪、LLM 情绪 |
| **风险** | `risk`, `gap_risk`, `tail_anomaly` | 风险因子、缺口风险、尾部异常 |
| **流动性** | `liquidity`, `cost` | 流动性、交易成本 |

因子元数据（系数、相关性、健康度、风险归因）由 `core/signal/factors/meta/` 统一管理。

### 3.3 打分流水线

`score_bars()` 仍算因子 `sub_scores`（ŷ 输入）。**生产 `score` / `predicted_score` 是组 OLS/Ridge 的 ŷ%**，不是 0–100 规则加权总分。规则综合分仅研究对照（`heuristic_score`）。

入池地板认 `scoring.min_predicted_score`（现网 0.4%）。`rank.min_score=55` 只服务启发式遗留，不进 ŷ 买入门。

### 3.4 双预测头融合（Dual Score）

`core/signal/dual_score/` 维护独立标签的预测头；**调仓 ranking** 与 **盘中簿 ŷ_trade** 不是同一套权重。

| 预测头 | 含义 | 用途 |
|--------|------|------|
| **ŷ_oo** | 预估 open[T+1]/open[T]−1 | 主字段 / 入池地板；收盘后排序；调仓 ranking 成分 |
| **ŷ_oc** | 预估 close[T]/open[T]−1 | 调仓 ranking 成分；盘中 ŷ_trade；做 T 估 C_τ |
| **ŷ_τc** | 预估 close[T]/price(τ)−1 | 研究拟合仍保留；做 T / 数据中心表列已下线 |
| **ŷ_co** | 预估 open[T+1]/close[T]−1 | 经 w_co 叠进 ŷ_oc（调仓默认 w_co=0） |
| **调仓 ranking** | fusion_w_oo·ŷ_oo + fusion_w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1) | 纸面 rank_lots（默认 0.6 / 0.4） |
| **盘中 ŷ_trade** | dual_score blend：w_oo·ŷ_oo + w_tau·(缺口∘ŷ_τ) | 现网 **w_oo=0, w_tau=1**；收盘 eod_next 回到 ŷ_oo |

关键逻辑：
- **买入闸**：`buy_passes_tau_gate()` — 仅当 ŷ_τ 超过阈值时才允许买入
- **EOD gate**：`eod_gate_score_for_item()` — 开/加仓方向确认（读 raw ŷ_oo）
- **ŷ_oo**：主字段 `y_oo` / `predicted_score_oo`（旧键 `predicted_score_eod` / `predicted_score` 可读）

### 3.5 横截面排序

`cross_section.rank_cross_section()` 对观察池批量打分：

- **输入**：`watching.json` 的 watchlist（**上限 200**；现网可到 160 只）
- **处理**：逐股 `score_stock()`，质量门禁过滤
- **入簿截断**：`cluster_scoring.max_names`（默认 40，硬顶 80）——这是簿长，不是训练宇宙
- **配置**：生产门槛 `min_predicted_score`；`horizon_days`（现网 1）

### 3.6 质量门禁

`production_ok` 字段标识打分是否满足生产质量要求：

- 数据完整性（日线不足、ST、停牌）
- 因子覆盖率（关键因子缺失则降级为 heuristic 分）
- 分数范围合理性
- 未通过时 `scale = "heuristic_0_100"`，标记为启发式分数

---

## 四、调仓策略说明

> 详细数学推导见 [rebalance-logic.md](./rebalance-logic.md)。

调仓系统按 **ranking = w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)** 以已保存手数开仓或加仓（缺省 200/500），未过入场则清仓，现金用完即止。

### 4.1 核心概念

| 概念 | 含义 |
|------|------|
| **ŷ_oo** | 预期 open[T]→open[T+1] |
| **ŷ_oc** | 预期 open[T]→close[T] |
| **ŷ_co** | 预期隔夜 close[T]→open[T+1] |
| **ranking** | w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)；w_co 默认 0 |
| **rank入场 / rank强** | 选股下限 / 500 股门槛 |
| **持仓市值上限** | live 默认 15 万；本笔将超则跳过。历史回测不限 |

### 4.2 决策流程

调仓在 `core/paper/rebalance/rank_lots.py` 中实现，观察池与历史回测共用：

```
每个交易日 09:30 打分；历史回测按所选调仓时间用 5 分钟价成交：
  ranking = w_oo·ŷ_oo + w_oc·((1+ŷ_oc)(1+w_co·ŷ_co)−1)
  已持仓且未过入场、缺 ranking 或 hard_reject → 清仓（T+1 可卖）
  ranking > rank入场（可选 y_oo>0 / y_oc>0 / y_hl>0）→ 开仓或加仓
  ranking > rank强 → lot_strong 股，否则 lot_base 股（缺省 500 / 200）
  现金不够该手 → 缩到整百（最少一手）；仍买不起才跳过
```

### 4.3 手数与现金

- ranking &gt; rank强 → **lot_strong** 股，否则 **lot_base**（缺省 500 / 200；与历史回测表单同一键，保存规则写入交易执行）；买不下整手则缩到整百，最少一手
- 不留现金地板：现金不够该手则缩到整百（最少一手）。live 另受持仓市值上限（默认 15 万）；**历史回测** 本金默认 20 万（表单可改）、不套市值帽
- 未过入场的已持仓 **清仓**；缺 ranking 清仓。无「持」动作。

### 4.4 风控约束

| 约束 | 说明 |
|------|------|
| **T+1** | 当日买入不可卖 |
| **持仓市值上限** | live 默认 15 万，本笔将超禁买 |
| **OOS 失败组** | 禁止新开/加仓 |
| **手续费/滑点** | 按账户成本模型从现金扣除 |

### 4.5 调仓执行

live：`watching_matrix.simulate_watching_matrix_preview`；历史：`backtest_paper_replay`。

1. 09:30 对观察池打 y_fuse / y_on
2. `plan_rank_lot_day` 出买卖清单
3. 现金是否够该手
4. 预演或落账（仅已保存调仓时间～10:00 现价；过点不补跑、不挂开盘单）
5. 输出调仓报告

### 4.6 模式

- **预演（dry-run）**：只出清单不写 paper.json
- **确认落账**：改持仓与现金
- **历史回测**：每个交易日开盘价成交，初始资金 100 万

---

## 五、做T 策略说明

> 现行配方是 **v6 收盘带宽**。规则与盈亏计算见 [t0-logic.md](./t0-logic.md)。

做 T 是在**已持底仓**上的日内 timing overlay，不是独立选股。正 T = 先买后卖，反 T = 先卖后买（腿顺序，不是「低吸高卖」保证）。

### 5.1 核心概念

| 概念 | 含义 |
|------|------|
| **底仓** | 调仓已持有的仓；做 T 不改选股主线 |
| **正 T** | 先买后卖（`buy_then_sell`） |
| **反 T** | 先卖后买（`sell_then_buy`） |
| **C_τ** | `O×(1+clip(ŷ_oc×scale)/100)` 估的目标收价 |
| **δ** | 超额带宽（默认 3%）：C 破上带反 T、破下带正 T |

### 5.2 现网策略（v6）

**网格挂单 / 均线偏离不是现网做 T。** 现网只跑一套：

1. 每 5 分钟用截至该根前缀重算 ŷ_oc，估 C_τ
2. 该根收价相对 C_τ×(1±δ) 破带才开第一腿（截止 11:00）
3. 第一腿为触发根收盘价；第二腿冻结为 C_τ
4. 破带后过 HL 同号闸与 ŷ_τw 入场（全弃权计 0 票）；|y_hl| 过大须与 y_τ 同号
5. 每轮默认可卖量的 40%，累计不超过底仓 100%

### 5.3 执行流程

`core/t0/` · Follow Worker **5 分钟**盯盘；缺分钟则跳过该日。纸面 `POST /api/paper/t0` 默认 dry_run，`confirm=true` 才写账。

```
持仓底仓 → 5m 前缀估 C_τ → 收价破带选向 → 开 leg1
        → 共用止损 / 冻结 C_τ 挂 leg2 → 11:00 后只收第二腿
```

### 5.4 风控参数（与 t0-logic 对齐）

| 参数 | 说明 | 默认 |
|------|------|------|
| 破带 δ | 相对 C_τ 的超额带宽 | 3% |
| 开仓截止 | 此后不开 leg1 | 11:00 |
| `t0_round_ratio` | 每轮占日初可卖 | 40% |
| `t0_max_position_pct` | 累计上限 | 100% |
| 止损 | 相对成交价 | 1.2% |
| `fill_mode` | 回测成交 | `trigger`（触价假设） |

### 5.5 盈亏

已实现 round-trip 价差（手续费后），不是底仓浮动盈亏。正/反 T 应与 `origin=strategy` 调仓 PnL 分开归因。未平腿按规则挂目标 / 止损 / 收盘处理。

### 5.6 与调仓的关系

- 做 T **不改变选股结构**；在既定底仓上 overlay
- 未平腿时调仓跳过该票卖出
- 资金回流会进次日现金 / 净值，归因必须分层
- ŷ_τ **模型共用、决策不重复**：调仓侧是买入闸 + ŷ_trade 成分；做 T 侧是估 C_τ

---

## 六、关键代码索引

| 功能 | 位置 |
|------|------|
| 选股打分 | `core/signal/score_stock.py`, `core/signal/scorer.py` |
| 因子库 | `core/signal/factors/` |
| 双预测头 | `core/signal/dual_score/` |
| 横截面排序 | `core/signal/cross_section.py` |
| 调仓引擎 | `core/paper/rebalance/rank_lots.py`, `watching_matrix.py` |
| 做T 引擎 | `core/t0/`（`close_band.py` · `score_policy.py` · `slots.py`） |
| 模拟盘撮合 | `core/execution.py`, `core/paper/` |
| Web 后端 | `web/app.py`, `web/routers/` |
| Web 前端 | `web/static/partials/`, `web/static/js/` |
| 数据层 | `core/data/`, `core/ports/market.py` |

---

## 七、相关文档

- [量化研究指南](./quant-ui.md) — Web 量化研究台使用说明
- [调仓数学说明](./rebalance-logic.md) — 调仓策略的完整推导
- [做T 策略说明](./t0-logic.md) — 做T 规则与盈亏计算详解
- [ŷ 全链路规范](./quant.md) — 双预测头与打分链路
- [文档索引](./README.md)
