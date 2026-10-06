## 风控模型（Risk Layer）

[← 文档索引](../README.md) · 策略进攻侧见 [§ 策略层](./strategy.md#策略层strategy-layer) · 因子/stance 见 [quant.md](../quant.md) · 数据输入见 [§ 数据层](./data.md#数据层data-layer)

**定义**：风控模型 = 把组合与市场风险量化后，输出**限额 / 预警 / 干预意图**的确定性（或可拟合）规则。  
与 Alpha（打分找收益）形成 **双轮驱动**：Alpha 负责进攻，Risk 负责防守。

本仓库当前以 **静态规则 + 轻量动态（regime 降分）+ 账户调仓前门禁** 为主；**不是** 多风险因子加权 + ML 拟合的完整 Risk 引擎。输出多为建议或纸面模拟动作，**现行不接实盘强平**（策略验证阶段）。

在产品因果链中，Risk 负责估计「已发生敞口」对组合的影响（能买多少、要不要停），与 Alpha 的「相对吸引力」估计并列——见 [design-spine.md](../design-spine.md)。

**Q4 已落地**：`core/risk/checks.py` · `check_account_risk` —— 账户最大回撤、单票仓位上限、持仓只数；`blocks` 非空时 `run_daily_cycle` **跳过加仓**（减仓/止损仍执行）。限额来自 `StrategySpec.lifecycle.risk`。

---

## Alpha × Risk 闭环

```text
行情 / 账户 / 组合结构
        │
        ├─► Alpha（score_bars / stance）  →  谁更值得买、倾向买/观望
        │
        └─► Risk（限额 · 止损 · 降档 · 预警）→  能买多少、要不要砍、整体敞口
                │
                ▼
         交易意图（回测记账 / 纸面）—— 非券商下单
```

| | Alpha 模型 | Risk 模型 |
|--|------------|-----------|
| 目标 | 收益 / 排序 | 生存 / 回撤 / 集中度 |
| 典型输出 | `score` · `stance_label` | 动态 `position_pct` · 风险分 · 减仓指令 |
| 本仓库重心 | **已较强**（八因子 + IC 建议） | **调仓前门禁已落地**；风格暴露/波动预算仍弱 |

---

## 成熟思路：三块

### 1. 拆解风控因子（把风险量化）

| 风险因子 | 监控什么 | 典型动作 |
|----------|----------|----------|
| **市场风险** | 大盘波动率、流动性枯竭 | 波动飙升 → 整体仓位打折 |
| **风格/行业暴露** | 行业/市值/风格是否过浓 | 超限 → 预警或强制分散 |
| **个股特质风险** | 异常波动、放量异动、与大盘背离 | 单票降仓 / 剔除 |
| **拥挤度** | 赛道或策略是否过热 | 降杠杆、回避踩踏 |

### 2. 动态权重 / 状态切换（可拟合）

静态止损（如「跌 8% 砍仓」）在风格切换时易失效。成熟做法：

- **按 regime 调权重**：震荡市抬高波动因子；趋势市更盯行业暴露  
- **非线性识别**：风险传导、隐性踩踏（ML 潜力，需数据与验证）  
- **状态识别**：HMM / 聚类判牛熊高波，再切换限额表  

与 Alpha 一样：先有可解释因子与 OOS，再谈自动拟合；**不自动静默改生产限额**（与 `signal_config` 人工合并原则一致）。

### 3. 输入与输出

| 方向 | 内容 |
|------|------|
| **输入** | 组合状态（持仓、行业/市值暴露）· 市场环境（波动、流动性）· 历史风险案例（训练用） |
| **输出** | **动态限额**（如单票上限 10%→5%）· **风险分 0～1** · **干预意图**（减仓/暂停开仓/清仓意图） |

干预交给执行/纸面层模拟；本系统 **不做** 真实强平。

---

## 本仓库对照（现状）

| 成熟能力 | 当前落点 | 说明 |
|----------|----------|------|
| 市场风险 / regime | `core/signal/regime.py` · `signal_config.regime` | 弱趋势时对 **score 降分**，非直接砍仓 |
| 个股硬门槛 | `hard_reject`（追高/急跌/日线不足） | 偏入场过滤，非持仓期风控 |
| 失效参考 | `invalidation`（约 `-3%` 文案） | **建议级**，非自动止损单 |
| 纸面止损 / 时间 | `paper.rules.stop_loss_pnl` · `max_hold_days` · `min_hold_score` | 模拟卖出 |
| 仓位上限 | `max_positions` · `position_pct` | **静态**比例 |
| 持仓集中度 | `position` 规则提示 | 真仓建议；非组合 Risk 引擎 |
| 行业/风格暴露 | `core/risk/exposure.build_exposure_matrix` | 持仓×主题行业 + 板块风格 + 仓位档；超限与门禁同源 |
| 拥挤度 | 无 | R3 可选 / 远期 |
| 风险分 0～1 | 无统一 | R3 可选 / 远期 |
| 账户最大回撤熔断 | `check_account_risk` + StrategySpec.risk | 调仓前门禁；超限拦加仓；`block_items.code` |
| 组合目标权重 | `optimize_weights` → `last_optimize` / ops_report | **分数风险预算**（默认）+ 可选贪心填仓；**高波缩放**有效单票/行业上限 |
| 波动缩放 | `core/risk/budget.market_vol_scale` | 指数近20日波动/基线 ≥1.5 → 上限×0.8；取数失败不挡调仓 |
| 拦截有效率 | `north_star.summarize_risk_blocks` | 按码/日/周；`meta.outcome` 标注后算有效率/误拦率 |
| 策略限额 | StrategySpec.risk / `paper.rules` | 调仓前门禁；UI 不在策略中心挂只读审计 |
| ML 风控拟合 | 无 | 远期 |

配置摘要见 `data/signal_config.json`（`invalidation` / `regime`）与 `data/paper.example.json`（`rules`）。

---

## 实战演进（建议顺序）

与「先静态、再简单动态、再 ML」一致：

```text
① 静态规则（已有）
   hard_reject · stance 降档 · paper 止损/持仓上限 · invalidation 文案
        ↓
② 简单动态（已落地轻量）
   市场波动抬升 → 有效 max_position/sector ×0.8；
   目标权重按 score 比例分配；可选 `risk_parity_lite` / `qp_lite`（cvxpy，未装则 unavailable 回退）
        ↓
③ 暴露可见 + 拦截可审计（R3/V3 已落地）
   持仓行业/风格矩阵 · 原因码 · 有效率（策略页标注 outcome，≥20 条才展示趋势）
   完整风格/Beta VaR / 拥挤度 → 仍远期；**QP 非闸门阻塞**
```

### 策略验证阶段风控清单（V3）

| 项 | 要求 |
|----|------|
| 调仓前硬拦 | `check_account_risk`；`risk_block` 带结构化原因码 |
| 暴露巡检 | 日更/调仓附暴露矩阵；超限与预算同源 |
| outcome 纪律 | 策略页标注真拦/误拦；`labeled_count≥20` 后才解读有效率 |
| 权重模式对照 | `score_budget` vs `risk_parity_lite`（及可选 `qp_lite`）可切换验证 |
| 不做 | 真强平、实时 VaR 引擎、独立 Bloomberg 风控台 |

**原则**：

1. Risk 输出是 **意图与限额**，不是保证不亏。  
2. 与 Alpha 一样：数字确定性计算，LLM 只解读。  
3. **策略验证阶段**：在 **回测 + 纸面** 验证动态限额；待验证成熟后再谈真账户（N6）。  
4. 与 RL 的关系（风控 → Reward 翻译；勿与 LLM Agent 混淆）见 [rl.md · RL 视角](./rl.md#强化学习rl视角)。

---

## 与策略设计模板的衔接

填 [§ 策略层 · 风险控制](#策略层strategy-layer) 时，尽量写成可量化条目，例如：

- 单票上限 / 总仓上限（可写「基础值 + 高波折扣」）  
- 止损 / 时间止损  
- 账户回撤暂停开仓阈值（若尚未实现，标「目标」）  

Alpha 条目（选股择时）与 Risk 条目分开写，避免「一个大 if」搅在一起。

---

## 相关代码速查

| 账户调仓前门禁 | `core/risk/checks.py` · `check_account_risk` |
| 暴露矩阵（R3） | `core/risk/exposure.py` · `build_exposure_matrix` |
| 行业映射 / 目标权重（N3） | `data/sector_map.json` · `core/portfolio_optimize.py` · `core/risk/budget.py`（波动缩放·分数预算·`risk_parity_lite`） |
| 拦截标注 / 有效率（R3） | `core/risk/block_outcome.py` · `north_star.summarize_risk_blocks` · `GET/POST /api/paper/risk-blocks*` |
| sector_map 对齐 | `core/sector_map_sync.py` |
| 告警出站（P2++） | `core/alert_outbound.py` · `paper_daily` → `data/alerts/` · 可选 `INVESTMENT_ALERT_WEBHOOK` |
| 策略衰减监控（N5） | `core/strategy_monitor.py`（回撤 + 滚动 IC + 行业覆盖） |
| Regime 降分 | `core/signal/regime.py` |
| hard_reject / invalidation | `core/signal/scorer.py` · `signal_config` |
| 买卖降档 | `core/stance.py` |
| 纸面止损与仓位 | `core/paper.py` · `paper.rules` |
| 真仓规则建议 | `core/position.py` · `position_rules.json` |
| 回测回撤等指标 | `core/backtest/`（评估用，非实时风控） |
| OOS / regime 报告（N4） | `core/backtest/oos_report.py` |

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [§ 策略层](./strategy.md#策略层strategy-layer) | 选股择时 / 仓位 / 风控在策略中的位置 |
| [rl.md · RL 视角](./rl.md#强化学习rl视角) | 风控规则 → Reward；Policy/Env 映射 |
| [quant.md](../quant.md) | score · stance · 纸面规则细节 |
| [quant.md · 入门概念](../quant.md#量化入门概念) | 测试类型（风控要在回测/模拟里验） |
| [design-spine.md · 路线图](../design-spine.md#能力评估与升级规划路线图视角) | 与专业系统差距 |
| [architecture.md](../architecture.md) | 决策执行层 · 组合风控产品目标 |

---

## 舆情与另类数据（Sentiment / Alt-Data）

[← 文档索引](../README.md) · 数据入口见 [§ 数据层](./data.md#数据层data-layer) · 因子侧见 [§ 策略层](./strategy.md#策略层strategy-layer) · [quant.md](../quant.md)

**定位（现行契约）**：规则舆情 **S** 是与 `predicted_score`（ŷ）**正交的先验旁路**，不是可回测、可求 β 的数据驱动因子。

```text
ŷ = ReturnScoreModel(价量 / 财务 / …)   ← 唯一生产排序轴
S = score_headlines(当日标题)            ← 观察徽章（参考）
live 调仓 = rank_lots(y_fuse, y_on)     ← 不读 S
```

本仓库 **已有**：标题拉取与缓存；观察徽章；`sentiment_prior`（产品强制 `mode=off`）；标题 history jsonl（供 as_of 诊断）；`GET /api/ops/sentiment-as-of`。  
**没有**：完整可估 β 的全市场 news 仓、用 S 拟合 OLS β、LLM 写分进 ŷ、主回测注入舆情、个股舆情拦买/缩仓。

LLM 可**解读**标题；**不得**写入 `sub_scores` / ŷ。`sentiment.include_in_score` **恒保持 false**（硬闸）；`get_sentiment_prior_cfg` 强制 `mode=off`，调仓不执行 gate/risk。

超时 / 空标题 / 过期缓存回退时标记 `degraded=true` · `prior_eligible=false`，徽章不标触发。

---

## 先验 policy（live）

个股舆情 **只展示徽章**。文件里残留的 `risk` / `gate` 配置被运行时忽略。

| 表面 | 行为 |
|------|------|
| 观察 / 持仓 / 分数 tip | 看空/看多徽章 + `risk_hints` |
| Follow `rank_lots` / `/replay` | 不读 S |
| 旧横截面编排 | 读到的 cfg 也是 `off`，不拦买不缩仓 |

`apply_prior_to_buy` / `apply_prior_to_hold` 仍保留给单测合成 pack，生产 live cfg 不会产出 block/scale actions。

**Web**：策略中心不再编辑个股舆情；`POST /api/signal/config/sentiment-prior` 若被调用也强制 `mode=off`。

配置见 `signal_config.sentiment`；实现见 `core/sentiment_prior.py`；徽章由 `score_stock` 挂到观察/持仓。

**相关**：开盘缺口等 **事件先验**（与 S 并列的 \(E\)，同属 ŷ 外旁路）及盘中剩余收益头规划见 [quant.md · 盘中剩余收益头](../quant.md#13-盘中剩余收益头intraday-residual方案)。

---

## 为何不是因子

缺可按决策日切片的历史 news → 无法合格 as_of 回测 → 估不出可信 `alt_sentiment` β。  
因此 **不进** 主回测 / OLS / promote；history 仅作将来诊断 IC，不支撑「开闸进 ŷ」。

---

## 在架构中的位置

```text
日线/财务 ──► ŷ（排序真源）
标题/规则 ──► S（先验）──► policy(ŷ,S) ──► 开仓/仓位
LLM ────────► 叙事（不进分）
```

| 层 | 关系 |
|----|------|
| Alpha / ŷ | 不含 S |
| [risk-layer](#风控模型risk-layer) | S → warn / gate / scale |
| 投顾 | `news` 供解读；不参与 stance / sub_scores |

---

## 本仓库对照

| 能力 | 现状 |
|------|------|
| 规则情绪 S | **有** · `score_headlines`（强度=`polar×标题覆盖`，防稀疏负向虚高） |
| 降级跳过 gate | **有** · `degraded` / `prior_eligible=false` |
| as_of 面板 API | **有** · `GET /api/ops/sentiment-as-of` · history 覆盖进 hygiene |
| 先验 policy | **有** · `role=prior` · `prior.mode` |
| Live 进 ŷ | **否** · `include_in_score=false` · X5 边界诚实 |
| 调仓旁路 | **有** · skip / scale；`predicted_score` 不改写 |
| 主回测注入 S | **否** |
| OLS 求 S 的 β | **否** |
| LLM 分析 | **有** · 叙事；不得进 `sub_scores` |

---

## 相关代码速查

| 能力 | 路径 |
|------|------|
| 先验 policy | `core/sentiment_prior.py` |
| Live 打分 | `core/signal/score_stock.py`（输出 `sentiment_prior`） |
| 调仓接入 | `core/paper/rebalance/` |
| 硬闸 | `sentiment.include_in_score` |
| 观察 API | `web/routers/watching.py` |

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [本文件 · 风控层](#风控模型risk-layer) | 防守侧输入 |
| [§ 策略层](./strategy.md#策略层strategy-layer) | ŷ 排序真源 |
| [quant.md · ŷ 全链路](../quant.md#predicted_scoreŷ全链路) | ŷ 主链路（门禁与口径） |
| [design-spine.md](../design-spine.md) | 先可信再变厚 |

---

