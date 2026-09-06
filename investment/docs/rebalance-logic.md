# 策略调仓产品文档

> A 股 T+1 横截面 / 持仓规则调仓：基于多目标 ŷ 排序与门禁链，在风险预算内完成持仓再均衡。非实盘、不代客下单，服务于策略验证与纸面增强。

---

## 1. 产品定位与本质

### 1.1 一句话定义

根据**多目标 ŷ 打分**（横截面排序或持仓规则），在满足买卖门槛、风控限额、换手预算与 T+1 约束的前提下，对纸面持仓进行**卖出 → 买入**的再均衡，使组合向目标权重靠拢。

### 1.2 与底仓做 T 的边界

| 维度 | 策略调仓 | 底仓做 T |
|------|----------|----------|
| 决策问题 | 持有什么、各占多少 | 既有底仓上日内往返 |
| 信号头 | ŷ_trade 排序 + ŷ_EOD 买入闸 + ŷ_τ 买入闸 | v6 收盘带宽：ĉ=ĉ_τ，破 ĉ±δ |
| 持仓寿命 | 持有约 3 日（horizon_days） | 当日往返 |
| 收益类型 | 持有期相对收益 | 已实现 round-trip 价差 |
| 仓位出处 | `origin=strategy` | `origin=t0`（`t0_batch`） |
| 频率 | 日级（日内可执行） | 每 5 分钟扫至 11:00 |

调仓是选股 Alpha 的载体；做 T 是调仓底仓上的 timing overlay。两者可独立运行，也可通过 `coupling.t0_vs_stance` 耦合。

### 1.3 A 股 T+1 硬约束

- **买入批次 FIFO 可卖**：T 日买入的股份，到下一交易日才可卖。
- **旧底仓**（无批次记录的历史持仓）视为当日可卖，避免冻结历史账本。
- **卖出受 `clip_sell_shares` 限制**：可卖量 = 非当日买入批次之和。
- 交易必须整手（100 股），不足 1 手跳过。

---

## 2. 调仓模式

| 模式 | 枚举 | 适用场景 | 卖出逻辑 | 买入逻辑 |
|------|------|----------|----------|----------|
| **横截面** | `cross_section` | 主路径（默认） | 不在 TopK 或 ŷ_trade < min_hold_score → 卖出 | 从 TopK 买入未持仓，过 ŷ_EOD + ŷ_τ 闸 |
| **持仓规则** | `holding_rules` | 逐票规则引擎 | 单票信号扫描驱动卖出 | 按规则引擎加仓 |
| ~~分池簿~~ | ~~cluster_book~~ | **已停用** | — | — |

分池簿路径已停用，保留 API 兼容但返回"已停用"错误。当前生产统一走横截面模式。

---

## 3. 信号与门槛体系

### 3.1 多目标 ŷ

| ŷ | 含义 | 调仓中的作用 |
|----|------|-------------|
| **ŷ_trade** | 日频交易分数 | 横截面排序主分；卖出主分（低于 min_hold_score 卖） |
| **ŷ_EOD** | 预估日收收益 | **买入闸**（≥ min_score 才买） |
| **ŷ_τ** | 盘中 τ 收益 | **买入闸**（`buy_passes_tau_gate`）；与 ŷ_EOD 双头校验 |
| ŷ_path | 极值时间序 | path_matrix 开/加同号闸；|y_path| 定 λ 仓位系数 |
| ŷ_on | 尾盘回补 | path_matrix 隔夜留仓折扣 |
| ŷ_nowcast | 即时对照 | y_fuse 融合分（与 ŷ_trade 加权） |

### 3.2 双轨评分（predicted / heuristic）

调仓门槛支持两条轨道，由 `rebalance_tracks` 配置驱动：

| 轨道 | 分数空间 | 买入门槛 | 卖出门槛 |
|------|----------|----------|----------|
| **predicted**（生产） | ŷ_EOD / ŷ_trade（百分比） | `predicted_buy_floor` | `predicted_hold_floor` |
| **heuristic**（回退） | 0–100 分 | `min_score`（默认 55） | `min_hold_score`（默认 45） |

`resolve_score_track(item)` 判定单票走哪条轨；`buy_gate_for_item` / `hold_decision_for_item` 统一执行。生产 ŷ 须 `allows_production_yhat` 通过。

### 3.3 核心门槛

| 参数 | 默认值（short 策略） | 说明 |
|------|----------------------|------|
| `min_score` | 55（heuristic）/ predicted_buy_floor | 买入下限（ŷ_EOD） |
| `min_hold_score` | 45（heuristic）/ predicted_hold_floor | 持有下限（ŷ_trade，低于则卖） |
| `max_positions` | 20 | 最大持仓数 |
| `position_pct` | 0.15 | 单票目标仓位比例 |
| `min_cash_pct` | 20%（隐含） | 保留现金比例（供反 T） |
| `max_turnover_pct` | 可配 | 双边换手软上限 |
| `horizon_days` | 3 | 预期持有天数 |

### 3.4 Stance 规则引擎

由 `compute_buy_stance` 输出确定性 stance，LLM 须引用不得自行升级/降级：

| stance_code | 含义 | 触发条件 |
|-------------|------|----------|
| `insufficient` | 信息不足 | 行情不可用 / 无 predicted_score |
| `avoid` | 观望 | hard_reject 或 ŷ < t_avoid |
| `wait` | 观望 | t_avoid ≤ ŷ < t_wait |
| `probe` | 逢低分批 | t_wait ≤ ŷ < t_probe |
| `buy_light` | 轻仓试探 | ŷ ≥ t_probe |

**惩罚降级**：当日大跌(-5%×2 / -3%×1)、K线偏弱、同业偏弱、大盘超额弱、数据降级 → stance 逐档降级。

---

## 4. 调仓全流程

```
simulate_cross_section_rebalance(paper, ranking, top_k)
    │
    ├─ 1. 预取：批量行情 + 板块 breadth + 舆情先验（开环批量，禁循环内串行）
    ├─ 2. 构建 ScoreIndexes（top_items / top_codes / 各 ŷ 索引）
    ├─ 3. run_sell_leg（卖出腿）
    │     ├─ 判定卖出原因（TopK外 / 低于hold / hard_reject / 舆情缩仓 / 市场缩仓）
    │     ├─ path_matrix 卖闸（defer / λ 缩卖出）
    │     ├─ soft-hold（事件先验 / 舆情 / Y分歧 → 暂不卖）
    │     ├─ 涨跌停/停牌跳过
    │     ├─ T+1 可卖量裁剪
    │     ├─ 成交（apply_fill_price + calc_trade_fees）
    │     └─ 膨胀减仓（分池持仓 > 簿长时卸中间带）
    ├─ 4. apply_post_sell_gate（卖后门禁）
    │     ├─ 账户风控 check_account_risk
    │     ├─ 回撤硬拦（drawdown_blocks → buys_blocked）
    │     ├─ 软超限（逐笔缩量，不整批拦）
    │     └─ optimize_weights → target_w（目标权重表）
    ├─ 5. run_buy_leg（买入腿）
    │     ├─ 舆情缩仓 restore（先验恢复时补回）
    │     ├─ τ 买入门槛（resolve_tau_buy_floor_for_pool，含 breakglass）
    │     ├─ 逐票买入链：过热闸 → production ŷ → EOD 闸 → τ 闸 → Y校验 → 舆情 → 市场prior → optimize目标仓 → 涨跌停 → path_matrix买闸
    │     ├─ sizing：min(spendable, equity × ratio)，受 target_w / prior ratio 收紧
    │     ├─ 风险预算裁剪（单票 / 行业上限）
    │     ├─ 换手预算裁剪（双边各 50%，sell 侧可溢出给 buy）
    │     └─ 换手背包重试（首轮跳过的候选半仓补入）
    └─ 6. finalize_report（生成调仓报告）
```

---

## 5. 卖出腿详解

### 5.1 卖出原因（reason_tag）

| reason_tag | 触发条件 | 说明 |
|------------|----------|------|
| `hard_reject` | 硬拒绝标记 | 直接卖出 |
| `not_in_topk` | 横截面：不在 TopK | 横截面模式特有 |
| `below_hold` | ŷ_trade < min_hold_score | 分池滞回主卖因 |
| `sentiment_trim` | 舆情先验看空缩仓 | 不改 ŷ，只缩量 |
| `market_trim` | 市场级 prior 缩仓 | 不改 ŷ，只缩量 |

### 5.2 卖出保护（soft-hold）

以下场景即使满足卖出条件，也**暂不卖出**：

- **path_matrix defer**：`pending_exit` 本窗推迟，等更好卖点
- **事件先验**：开盘缺口达标（主题日）且 ŷ_τ 为正 → `should_soft_hold_for_low_score`
- **舆情先验**：`should_soft_hold_from_sentiment` 看多
- **Y 双头分歧**：ŷ_EOD 与 ŷ_τ 异号 → `y_check=conflict`

### 5.3 成交与约束

- 成交价：`apply_fill_price("sell", quote_price)` 含滑点
- 跌停/停牌 → 跳过（`_sell_match_block_reason`）
- T+1：`clip_sell_shares(h, sell_shares)` 只卖可卖旧仓
- 整手：不足 100 股跳过
- 路径 λ 缩卖出：`path_matrix` 返回 `sell_fraction` 时按比例卖

### 5.4 膨胀减仓（分池）

持仓数 > 簿长时，优先卸**中间带**（不在 TopK 的持仓），若中间带不可卖则卸簿内最低分。已卸的簿内票本轮**禁止买回**（`force_trim_no_rebuy`）。

### 5.5 风控超额减仓

卖腿后、买腿前执行 `check_account_risk`，若超限则主动减仓：

- **单票超限**：`仓位% > max_position_pct` → 减至限额以下
- **行业超限**：`行业仓位% > max_sector_pct` → 行业内按 ŷ 从低到高部分卖出

---

## 6. 卖后门禁与目标权重

### 6.1 账户风控（check_account_risk）

| 检查项 | 默认限额（short） | 超限行为 |
|--------|-------------------|----------|
| 最大回撤 | 20%（target 12%） | `drawdown_blocks` → **整批拦买** |
| 单票上限 | 25% | `soft_block` → 逐笔缩量 |
| 行业上限 | 40% | `soft_block` → 逐笔缩量 |

- 回撤恢复（current_dd < target_dd）→ 解除加仓封锁
- 软超限不整批拦买，文案进 warnings

### 6.2 optimize_weights

卖腿后调用 `optimize_weights` 生成目标权重表 `target_w`：

- 输入：ranking + 风险限额（max_position_pct / max_sector_pct / max_positions）
- `weight_mode`：默认 `score_budget`（按分数分配预算）
- 横截面：`target_w` 未分配的票**跳过买入**
- 分池：合并簿即目标集，不因 optimize 漏配而整票跳过

---

## 7. 买入腿详解

### 7.1 买入门槛链（顺序固定）

逐票按以下顺序校验，任一不通过则跳过：

1. **持仓数上限**：横截面 `len(holdings) >= max_positions` 停；分池看「已持簿内只数」
2. **force_trim_no_rebuy**：膨胀减仓卸的簿内票不买回
3. **hard_reject**：硬拒绝跳过
4. **过热闸**：`paper_overheat_block`（mom5 / 当日大涨等）
5. **production ŷ 闸**：predicted 轨须 `allows_production_yhat`
6. **ŷ_EOD 买入闸**：`buy_gate_for_item`（双轨 min_score）
7. **ŷ_τ 买入闸**：`buy_passes_tau_gate`（floor = `resolve_tau_buy_floor_for_pool`）
8. **Y 双头校验**：`buy_passes_y_check`（EOD 与 τ 分歧 / 缺 τ → 不新开）
9. **舆情先验**：`apply_prior_to_buy`（gate 时 skip / 缩 ratio）
10. **市场级 prior**：`apply_market_priors_to_buy`（gate 时 skip / 缩 ratio）
11. **optimize 目标仓**：横截面须在 `target_w` 中
12. **涨跌停 / 停牌**：`_buy_match_block_reason`
13. **path_matrix 买闸**：`buy_execution_gate`（同号闸 + λ）

### 7.2 τ 买入门槛（tau_floor）

`resolve_tau_buy_floor_for_pool` 对候选池计算有效 τ 门槛：

- 正常模式：取池内 ŷ_τ 的合理分位
- `freeze_breakglass` 模式：候选池无人过基线时**冻结放宽**门槛，回注 `paper.rules.t0.y_tau_enter_effective`（与做 T 共用）
- 回撤硬拦时清掉 breakglass 回注，避免做 T 误用

### 7.3 仓位 sizing

```
spendable = cash − equity × min_cash_pct   # 保留现金供反 T
ratio = position_pct
ratio = min(ratio, sentiment_prior_ratio)
ratio = min(ratio, market_prior_ratio)
ratio = min(ratio, target_w[code] / 100)   # optimize 目标仓
budget = min(spendable, equity × ratio)
shares = int(budget // price // 100) × 100
shares = shares × path_matrix_λ            # λ ∈ [0, 1]
```

- 横截面与分池统一 `budget = min(spendable, equity × ratio)`（历史横截面曾用 `cash × ratio` 导致欠仓）
- 分池 optimize 未返回时回退 `1 / max_positions`

### 7.4 风险预算裁剪

`clip_buy_to_risk_budget` 逐笔检查：

- 单票市值 ≤ `equity × max_position_pct`
- 行业市值 ≤ `equity × max_sector_pct`
- 超限则裁剪手数或跳过

### 7.5 换手预算

`max_turnover_pct` 为双边换手软上限：

- 买卖各占 50% 预算；sell 侧未满可溢出给 buy
- 超额时先按剩余预算裁剪手数；仍不足 1 手进**重试池**
- 重试池：高分离票优先，**半仓**（base_ratio × 0.5）榨干剩余预算

### 7.6 舆情 restore

舆情缩仓后（持仓带 `sentiment_trim_base_shares`），若先验不再要求缩仓，则补回至基准仓位。restore 也受风险预算与换手预算约束。

---

## 8. 早盘路径矩阵（path_matrix）

### 8.1 目标与执行分离

- **目标层**：`y_fuse = w_trade × ŷ_trade + w_nowcast × ŷ_nowcast` 排序 Top-K / 定 w*
- **执行层**：开加仓要求 ŷ_path、ŷ_on 与方向同号；|ŷ_path| 定 λ

### 8.2 两种模式

| 模式 | 行为 |
|------|------|
| `linear`（L0） | 缺口立刻补 |
| `path`（L1，默认） | path + on 同号闸 |

### 8.3 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `path_enter` | 0.1% | |ŷ_path| 横盘门槛 |
| `path_half` | 0.5% | ≥half → λ=0.5 |
| `path_full` | 1.0% | ≥full → λ=1.0 |
| `y_on_allow` | 0.5% | ≥allow → 隔夜满留 |
| `y_on_half` | 0.1% | ≥half 且 <allow → 隔夜半仓 |
| `fusion_w_trade` | 0.5 | ŷ_trade 权重 |
| `fusion_w_nowcast` | 0.5 | ŷ_nowcast 权重 |
| `require_path_on_same_sign` | True | 开/加：path、on 与方向同号 |
| `allow_pending_exit_on_path_low` | True | path>0 时清仓可推迟 |

### 8.4 动作码

`open`（开仓）/ `add`（加仓）/ `reduce`（减仓）/ `exit`（清仓）/ `hold`（持有）/ `skip_window`（本窗不调）/ `pending_exit`（必清但等更好卖点）

---

## 9. 成交时机（rebalance_timing）

### 9.1 execution_mode

| 模式 | 行为 |
|------|------|
| `next_open`（默认） | **交易时段内按现价成交**；收盘后挂次日开盘单（09:15–10:00） |
| `close` | 确认即成交 |

> 产品口径：纸面盘中是准实盘（A 股连续竞价可买卖），不是"收盘决策、开盘才执行"。回测默认 `next_open` 是研究侧防未来函数，与纸面盘中成交分开。

### 9.2 开盘窗与追价

- `open_fill_after_hm` / `open_fill_until_hm`：次日开盘成交窗（默认 09:15–10:00）
- `pending_chase_interval_min`：未成交挂单盘中追价间隔（默认 10 分钟）
- `pending_chase_eod_hm`：近收盘改用现价强平（默认 14:50）

---

## 10. 成本模型

与做 T 同源（`cost_params` / `resolve_cost_model`），默认 `simple_cn`：

| 项 | 默认值 | 说明 |
|----|--------|------|
| 佣金 | 2.5 bps | 最低 5 元 |
| 印花税 | 5 bps（仅卖出） | |
| 滑点 | 3 bps | 研究回测默认 |

每腿成交写入 `net_cash_delta`（含手续费与滑点）。

---

## 11. 风控体系

### 11.1 组合级

| 指标 | 默认限额（short） | 超限行为 |
|------|-------------------|----------|
| 最大回撤 | 20% | 硬拦全部买入（`buys_blocked`） |
| 目标回撤 | 12% | 回撤低于此值恢复加仓 |
| 单票上限 | 25% | 主动减仓至限额 + 买入逐笔缩量 |
| 行业上限 | 40% | 行业内最低 ŷ 先减 + 买入逐笔缩量 |
| 最大持仓数 | 20 | 买入前检查 |

### 11.2 单笔级

- 整手约束（100 股）
- T+1 可卖量约束
- 涨跌停 / 停牌跳过
- 过热闸（追涨保护）
- production ŷ 质量闸

### 11.3 换手级

- 双边换手软上限（`max_turnover_pct`）
- 卖出侧未满可溢出给买入侧
- 半仓背包重试榨干剩余预算

---

## 12. 关键参数速查

| 参数 | 默认（short） | 说明 |
|------|---------------|------|
| `max_positions` | 20 | 最大持仓数 |
| `position_pct` | 0.15 | 单票目标仓位 |
| `min_score` | 55 | 买入下限（heuristic） |
| `min_hold_score` | 45 | 持有下限 |
| `horizon_days` | 3 | 预期持有天数 |
| `signal_limit` | 8 | 信号数量上限 |
| `weight_mode` | score_budget | 权重分配模式 |
| `execution_mode` | next_open | 成交时机 |
| `max_drawdown_pct` | 20% | 最大回撤硬拦 |
| `target_drawdown_pct` | 12% | 回撤恢复阈值 |
| `max_position_pct` | 25% | 单票仓位上限 |
| `max_sector_pct` | 40% | 行业仓位上限 |

---

## 13. 代码结构

```
core/
├── execution.py                    # ExecutionSpec 解析（t0/rebalance/coupling/timing）
├── stance.py                       # Stance 规则引擎
├── strategy.py                     # 策略 Spec 注册表
├── paper/
│   ├── rebalance/
│   │   ├── __init__.py             # simulate_cross_section_rebalance 编排
│   │   ├── orchestrator.py         # 模式路由（cross_section / holding_rules）
│   │   ├── sell.py                 # 卖出腿
│   │   ├── buy.py                  # 买入腿
│   │   ├── gate.py                 # 卖后门禁 + optimize
│   │   ├── path_matrix.py          # 早盘路径择时矩阵
│   │   ├── force_trim.py           # 膨胀减仓
│   │   ├── turnover.py             # 换手预算
│   │   ├── cash_reserve.py         # 现金保留
│   │   ├── match.py                # 涨跌停/停牌匹配
│   │   ├── prefetch.py             # 行情/舆情预取
│   │   ├── reasons.py              # 卖出原因常量
│   │   ├── state.py                # RebalanceState 会话态
│   │   └── report.py               # 调仓报告
│   ├── cycle.py                    # 日循环流水线（holding_rules）
│   ├── exec.py                     # simulate_sells / simulate_buys
│   ├── costs.py                    # 成本模型
│   ├── tplus1.py                   # T+1 可卖批次
│   └── open_fill.py                # next_open 成交时机
└── risk/
    ├── checks.py                   # check_account_risk
    ├── budget.py                   # clip_buy_to_risk_budget
    └── exposure.py                 # 敞口计算
```

---

## 14. 与做 T 的耦合

通过 `coupling.t0_vs_stance` 控制：

| 模式 | 行为 |
|------|------|
| `independent`（默认） | 调仓与做 T 互不影响 |
| `skip_if_avoid` | stance=avoid 时跳过做 T |
| `only_if_hold` | 仅 stance ∈ {wait, probe, buy_light} 时允许做 T |

调仓卖出时若做 T 已开 leg1（持仓被做 T 占用），会跳过该票的调仓卖出（`load_rebalance_t0_sell_blocks`），避免与做 T 抢仓。

---

## 15. 已下线设计（避免误用）

| 旧机制 | 现状 |
|--------|------|
| 分池簿调仓（cluster_book） | 已停用，统一横截面 |
| 横截面 sizing 用 `cash × ratio` | 已改为 `min(spendable, equity × ratio)` |
| 软超限整批拦买 | 已改为逐笔缩量 |
| 循环内串行 AkShare | 已改为开环批量预取 |

---

*文档版本：v1.0 · 生成日期 2026-09-06*
