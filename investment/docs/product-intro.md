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
├── 调仓规则（rules_ui.js）         — rank入场 / rank强 / 现金地板
├── 做T 面板（t0_ui.js, t0_table.js, t0_viz.js）— 做T 参数、实时触发、盈亏汇总
├── 日志（logs_ui.js）              — 调仓 / 做T / 错误日志
└── 北极星（north_star_ui.js）      — 组合层面目标与偏差监控
```

### 2.4 量化研究面板（Quant）详解

量化研究台覆盖因子研发到策略验证的全链路：

```
quant_panel.html
├── 因子面板（factor_*.js）         — 因子 IC、相关性、元数据管理
├── ŷ 可视化（yhat_viz.js, y_path_viz.js）— 双预测头曲线、路径拟合
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

`scorer.score_bars()` 对因子做加权聚合：

1. **因子计算**：`compute_configured_factors()` 按配置计算各因子原始值
2. **交互项奖励**：`_interaction_bonus()` — 动量×量价、反转×波动等交叉项
3. **非线性压缩**：`_sigmoid_score()` — 将线性分数压缩到更平滑的分布
4. **加权汇总**：按配置权重输出 0-100 的 `predicted_score`

### 3.4 双预测头融合（Dual Score）

核心创新：`core/signal/dual_score/` 实现两层独立预测头：

| 预测头 | 含义 | 用途 |
|--------|------|------|
| **ŷ_EOD** | 预估 close[T]/close[T-1]−1 | 排序、开仓方向判断 |
| **ŷ_τ** | 预估 close[T]/open[T]−1 | 买入闸（buy gate）、做T 信号 |
| **ŷ_trade** | w·ŷ_EOD + w·(缺口∘ŷ_τ) | 交易决策综合分 |
| **ŷ_nowcast** | 顺序 Kalman（EOD→open→分钟τ） | 默认影子，不替换 predicted_score |

关键逻辑：
- **买入闸**：`buy_passes_tau_gate()` — 仅当 ŷ_τ 超过阈值时才允许买入，过滤"方向对但买入时机差"的票
- **EOD gate**：`eod_gate_score_for_item()` — 调仓开/加仓时的方向确认
- **nowcast 影子**：`nowcast_kf.py` 实现 Kalman 滤波，仅作对照不参与决策

### 3.5 横截面排序

`cross_section.rank_cross_section()` 对观察池批量打分并排序：

- **输入**：`watching.json` 中的 watchlist（最多 50 只）
- **处理**：逐股 `score_stock()`，质量门禁过滤
- **输出**：按 `predicted_score` 降序的 Top N（默认 10，最多 30）
- **配置**：`min_score`（最低分阈值）、`horizon_days`（预测周期 1-10 天）

### 3.6 质量门禁

`production_ok` 字段标识打分是否满足生产质量要求：

- 数据完整性（日线不足、ST、停牌）
- 因子覆盖率（关键因子缺失则降级为 heuristic 分）
- 分数范围合理性
- 未通过时 `scale = "heuristic_0_100"`，标记为启发式分数

---

## 四、调仓策略说明

> 详细数学推导见 [rebalance-logic.md](./rebalance-logic.md)。

调仓系统按 **y_fuse / y_on ranking** 以 200/500 股加减仓，现金不得低于地板。

### 4.1 核心概念

| 概念 | 含义 |
|------|------|
| **y_fuse** | 预期今日收益（trade⊕nowcast） |
| **y_on** | 预期明日收益 |
| **ranking** | (1 + y_fuse/100) × (1 + α × y_on/100) − 1，展示百分数；α 默认 0 |
| **rank入场 / rank强** | 选股下限 / 500 股门槛 |
| **现金地板** | 买完后现金下限（默认 50 万） |

### 4.2 决策流程

调仓在 `core/paper/rebalance/rank_lots.py` 中实现，观察池与历史回测共用：

```
每个交易日 09:30：
  y_fuse = w_trade·ŷ_trade + w_nc·ŷ_nowcast
  ranking = (1 + y_fuse/100) × (1 + α × y_on/100) − 1  # 展示百分数；α 默认 0
  已持仓且 ranking < 0 → 清仓（T+1 可卖）
  ranking > rank入场 → 开仓或加仓（live 受观察池容量与持仓市值上限；历史回测用全部观察池）
  ranking > rank强 → 500 股，否则 200 股
  买完现金 < 地板（默认 50 万）→ 跳过该买
  未买且 ranking ≥ 0 → 持有
```

### 4.3 手数与现金地板

- ranking &gt; rank强（默认 0.012 / 1.2%）→ **500 股**，否则 **200 股**
- 买完后现金不得低于 `cash_floor`（默认 50 万；live 账户小于该值时按净值 20% 缩放）；否则跳过该买
- 未买但 ranking ≥ 0 **不卖**

### 4.4 风控约束

| 约束 | 说明 |
|------|------|
| **T+1** | 当日买入不可卖 |
| **现金地板** | 默认 50 万，破地板禁买 |
| **OOS 失败组** | 禁止新开/加仓 |
| **手续费/滑点** | 按账户成本模型从现金扣除 |

### 4.5 调仓执行

live：`watching_matrix.simulate_watching_matrix_preview`；历史：`backtest_paper_replay`。

1. 09:30 对观察池打 y_fuse / y_on
2. `plan_rank_lot_day` 出买卖清单
3. 现金地板校验
4. 预演或落账（盘中现价；收盘后可挂次日开盘）
5. 输出调仓报告

### 4.6 模式

- **预演（dry-run）**：只出清单不写 paper.json
- **确认落账**：改持仓与现金
- **历史回测**：每个交易日开盘价成交，初始资金 100 万

---

## 五、做T 策略说明

> 详细规则与盈亏计算见 [t0-logic.md](./t0-logic.md)。

做T（T+0）是在持有底仓的前提下，利用日内波动做高抛低吸，降低持仓成本。

### 5.1 核心概念

| 概念 | 含义 |
|------|------|
| **底仓** | 已持有的股票仓位，做T 不改变底仓数量 |
| **正向T** | 先买后卖（低买高卖），日内完成 |
| **反向T** | 先卖后买（高卖低买），日内完成 |
| **触发** | 价格/信号满足条件时生成做T 单 |
| **手数** | 单次做T 的股数，受底仓和资金约束 |

### 5.2 策略类型

| 类型 | 逻辑 | 适用场景 |
|------|------|----------|
| **ŷ_τ 驱动** | ŷ_τ 超过阈值触发正向T，跌破阈值触发反向T | 信号明确的票 |
| **网格** | 固定价格间隔挂买卖单 | 震荡行情 |
| **均线偏离** | 价格偏离均线超过阈值时回归做T | 趋势行情 |

### 5.3 执行流程

做T 在 `core/t0/` 中实现，由 `execution.py` 统一编排：

```
定时轮询持有底仓的股票：
  1. 取实时行情（最新价、买卖盘）
  2. 计算 ŷ_τ 或技术指标
  3. 判断触发条件：
     - 正向T：信号强 + 有可用现金 + 未达日内上限
     - 反向T：信号弱 + 有底仓可卖 + 未达日内上限
  4. 生成做T 单（限价/市价）
  5. 提交撮合执行
  6. 配对平单：检测反向条件，平掉做T 仓位
  7. 记录盈亏
```

### 5.4 风控参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `t0_enabled` | 总开关 | True |
| `t0_max_lots_per_day` | 日内最大做T 手数 | 3 |
| `t0_profit_target` | 单笔止盈比例 | 0.3% |
| `t0_stop_loss` | 单笔止损比例 | 0.5% |
| `t0_min_spread` | 最小价差要求 | 0.1% |
| `t0_cooldown_sec` | 两次做T 间隔 | 300s |
| `t0_max_position_pct` | 做T 仓位占底仓比例上限 | 50% |

### 5.5 盈亏计算

做T 盈亏在 `core/t0/pnl.py` 中实现，采用 **配对清算**：

- 正向T 盈亏 = (卖出价 − 买入价) × 股数 − 手续费
- 反向T 盈亏 = (卖出价 − 买回价) × 股数 − 手续费
- 未平单按收盘价估值（浮盈浮亏）
- 已实现盈亏计入当日 PnL，影响做T 专用余额

### 5.6 与调仓的关系

- 做T **不改变底仓数量**，调仓的目标权重仍按底仓计算
- 做T 仓位单独管理（`t0_position`），不计入持仓权重
- 做T 盈亏独立核算，与底仓盈亏分离
- 调仓时若有未平的做T 仓位，优先平仓再执行调仓

---

## 六、关键代码索引

| 功能 | 位置 |
|------|------|
| 选股打分 | `core/signal/score_stock.py`, `core/signal/scorer.py` |
| 因子库 | `core/signal/factors/` |
| 双预测头 | `core/signal/dual_score/` |
| 横截面排序 | `core/signal/cross_section.py` |
| 调仓引擎 | `core/paper/rebalance/rank_lots.py`, `watching_matrix.py` |
| 做T 引擎 | `core/t0/strategy.py`, `core/t0/engine.py` |
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
