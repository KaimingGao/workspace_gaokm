## 强化学习视角（RL Layer）

[← 文档索引](../README.md) · [§ 策略层](./strategy.md#策略层strategy-layer) · [§ 风控层](./risk.md#风控模型risk-layer) · [quant.md](../quant.md)

**一句话**：传统「策略 + 风控」≈ RL 里的 **Policy + Reward**；市场 / 回测 / 纸面 ≈ **Environment**。  
RL 追求的是 **最大化长期累积奖励**（赚得稳），不是单纯「预测下一根涨跌」。

本仓库 **未实现** 在线 RL / PyTorch TradingAgent。当前是 **规则 Policy + 规则 Risk**；回测与纸面可当作未来 RL 的 **离线环境**。  
勿与投顾里的 **LLM `InvestmentAgent`** 混淆：后者选 Skill、组织话术；RL Agent 学的是交易动作。

架构已标明：反馈层是半闭环，**不是**在线 RL 自动调权（见 [本章 § 控制论视角](#架构总览) · [design-spine.md · 路线图](../design-spine.md#能力评估与升级规划路线图视角)）。

---

## 深度映射

| 传统量化 | 强化学习 | 本仓库今天 |
|----------|----------|------------|
| 打分策略 / stance → 买多少 | **Policy**：State → Action | `score_bars` + `compute_buy_stance` + `paper.rules`（确定性） |
| 风控红线（止损、降仓、限开仓） | **Reward** 里的惩罚项 + 约束 | `hard_reject` · `invalidation` · 纸面止损；惩罚未统一成 reward |
| 历史行情 + 账户反馈 | **Environment** 步进 | `backtest` 引擎 · `paper` 日循环 |
| 净值 / 回撤 / 换手 | 逐步 **Reward** 与 episode 统计 | 回测 metrics · 纸面 snapshots（评估用，不训练） |

```text
State（行情 · 持仓 · 现金 · 可选风险特征）
   │
   ▼
Policy（规则今天 / 神经网络远期）──► Action（买/卖/仓位意图）
   │
   ▼
Environment（回测或纸面记账）──► 新 State + Reward
   │
   └──────── 累积回报最大化（RL 训练目标；本仓库未开）
```

### 三个对应关系

1. **策略 ↔ Policy**  
   传统：因子分 → 买/卖/持有。  
   RL：网络直接输出动作（如目标仓位）。本仓库仍是可审计规则链。

2. **风控 ↔ Reward（及约束）**  
   传统：触线则惩罚（平仓、禁开仓）。  
   RL：把红线写成负奖励，让策略为「拿高分」自发控仓——见下文奖励拆解。  
   注意：Reward 塑造行为，**不能替代** 实盘硬约束（账户熔断仍应规则保底）。

3. **市场 ↔ Environment**  
   动作之后返回新 K 线、盈亏与奖励。  
   本仓库：`core/backtest` / `core/paper` 已是「步进记账」雏形，缺的是 Gym 式 API 与训练环。

---

## 为何常被称作「终极形态」

| 阶段 | 做法 | 优化目标 |
|------|------|----------|
| 监督学习拟合风控/权重 | 预测风险或收益标签 | 预测准 |
| **强化学习** | 在环境中试错，最大化累积奖励 | **赚得稳**（收益 − 回撤 − 成本 …） |

RL 可以把进攻与防守揉进同一套可学习 Policy；代价是：样本效率、非平稳、过拟合历史、可解释与合规更难。  
因此路线是：**规则保底 → 数据与环境扎实 → 再考虑 RL 研究台**，而非一上来端到端实盘。

与 [quant.md](../quant.md) 同一产品原则：生产决策须可引用 `stance_label`；黑盒上线需人工冻结 artifact。

---

## 传统风控 → 奖励函数（翻译表）

智能体只优化长期累积奖励。把 if-else 红线译成「发奖金 / 扣工资」：

| # | 成分 | 传统规则直觉 | RL 翻译（示意） |
|---|------|--------------|-----------------|
| 1 | **基础收益** | 赚钱就好 | \(R_{profit} = \Delta equity / equity_{prev}\)；可改为夏普类风险调整收益 |
| 2 | **回撤惩罚** | 回撤超 15% 强平 | \(R_{dd} = -c \cdot drawdown^{2}\)（回撤越大越痛） |
| 3 | **波动惩罚** | 波动过大减仓 | \(R_{vol} = -\sigma(\text{近 N 步收益})\) |
| 4 | **交易成本** | 限制换手 | \(R_{cost} = -\text{手续费}\)（抑制刷单） |

示意合计（文档级，非生产代码）：

```text
R = R_profit + R_drawdown + R_volatility + R_cost
```

伪代码：

```python
def calculate_reward(current_equity, previous_equity, max_equity, trade_cost):
    profit = (current_equity - previous_equity) / previous_equity
    drawdown = (max_equity - current_equity) / max_equity
    # 平方放大：小回撤轻罚，大回撤重罚
    dd_penalty = -(drawdown ** 2) * 5.0
    cost_penalty = -trade_cost
    return profit + dd_penalty + cost_penalty
```

| 本仓库规则 | 可映射到 |
|------------|----------|
| `stop_loss_pnl` / 账户回撤限制（目标） | 回撤惩罚或 episode 提前终止 |
| `position_pct` · 高波打折（risk 演进） | 动作空间限制；或波动惩罚 |
| 佣金/滑点（回测成本模型） | 交易成本惩罚 |
| `hard_reject` / stance `avoid` | 动作掩码（禁止买）或大额负奖励 |
| `invalidation` 文案 | 不宜单独当 reward；应落到可计算的跌破/持有期条件 |

**硬约束 vs 软惩罚**：实盘对接时，监管与爆仓线应用 **不可学习的硬规则**；Reward 只塑造偏好。本系统不接实盘，纸面阶段也应保留硬止损作保底。

---

## 与现有栈的衔接（若将来做研究）

```text
① 规则 Policy + 规则 Risk（当前）
② 统一 Environment 适配：回测/纸面 → step(action)→(state, reward, done)
③ 离线评估：固定 Policy 在 Environment 上算累积回报（已有 metrics 雏形）
④ 可选：Actor-Critic / PPO 等在模拟中训练（新目录，不默认进生产 stance）
⑤ 人工对比 OOS + 纸面影子盘 → 才考虑 blend；永不静默覆盖 signal_config
```

前置条件（与拟合模型类似）：live/backtest 同源、PIT、成本模型、可复现种子与 evals。  
**不**默认引入 PyTorch/Gym 依赖；不把 LLM Agent 改成 RL 训练器。

经典 Actor-Critic 骨架（仅作概念，不入库实现）：

```text
TradingAgent(state) → action_probs（Actor≈综合策略）
                   → state_value（Critic≈状态价值，≠本仓库 Risk 模块）
Reward = 收益 − 回撤惩罚 − 成本 …
```

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [risk.md · 风控层](./risk.md#风控模型risk-layer) | 风控因子与规则演进（Reward 的原料） |
| [strategy.md · 策略层](./strategy.md#策略层strategy-layer) | Policy 的规则形态 |
| [quant.md](../quant.md) | 特征/评估；offline policy eval 表述 |
| [data.md · 数据层](./data.md#数据层data-layer) | Environment 的行情燃料 |
| [architecture.md](../architecture.md) | 反馈层非在线 RL |
| [design-spine.md · 路线图](../design-spine.md#能力评估与升级规划路线图视角) | 生产路径不含在线 RL |

---

