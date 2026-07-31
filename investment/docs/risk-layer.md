# 风控模型（Risk Layer）

[← 文档索引](README.md) · 策略进攻侧见 [strategy-layer.md](strategy-layer.md) · 因子/stance 见 [quant.md](quant.md) · 数据输入见 [data-layer.md](data-layer.md)

**定义**：风控模型 = 把组合与市场风险量化后，输出**限额 / 预警 / 干预意图**的确定性（或可拟合）规则。  
与 Alpha（打分找收益）形成 **双轮驱动**：Alpha 负责进攻，Risk 负责防守。

本仓库当前以 **静态规则 + 轻量动态（regime 降分）+ 账户调仓前门禁** 为主；**不是** 多风险因子加权 + ML 拟合的完整 Risk 引擎。输出多为建议或纸面模拟动作，**现行不接实盘强平**（策略验证阶段）。

在产品因果链中，Risk 负责估计「已发生敞口」对组合的影响（能买多少、要不要停），与 Alpha 的「相对吸引力」估计并列——见 [design-spine · 因果链](design-spine.md#因果链已发生--影响估计--动作)。

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
| 策略限额 UI | `/strategy` 风控与敞口折叠 | 暴露矩阵 + 拦截汇总；人审 promote，不静默改 |
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
4. 与 RL 的关系（风控 → Reward 翻译；勿与 LLM Agent 混淆）见 [rl-layer.md](rl-layer.md)。

---

## 与策略设计模板的衔接

填 [策略设计文档 · §5 风险控制](strategy-layer.md#策略设计文档模板填空) 时，尽量写成可量化条目，例如：

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
| sector_map 对齐 CLI | `research/sector_map_sync_run.py` · `core/sector_map_sync.py` |
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
| [strategy-layer.md](strategy-layer.md) | 选股择时 / 仓位 / 风控在策略中的位置 |
| [rl-layer.md](rl-layer.md) | 风控规则 → Reward；Policy/Env 映射 |
| [quant.md](quant.md) | score · stance · 纸面规则细节 |
| [quant-concepts.md](quant-concepts.md) | 测试类型（风控要在回测/模拟里验） |
| [roadmap.md](roadmap.md) | 与专业系统差距 |
| [architecture.md](architecture.md) | 决策执行层 · 组合风控产品目标 |
