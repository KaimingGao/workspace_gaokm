# 策略调仓产品文档

> A 股 T+1 **rank_lots** 调仓：每个交易日 fill_clock（默认 09:30）用 τc ranking 排序，按已保存金额换算股数开仓或加仓（缺省 1 万/2 万）。非实盘、不代客下单。

---

## 1. 产品定位与本质

### 1.1 一句话定义

每个交易日 **fill_clock（默认 09:30）**，对观察池打 **ranking**（ŷ_oo 按 rot 做几何剩余，再与 ŷ_τc∘ŷ_co 加权；基准 τ→open[T+1]），门槛1 ∪ 门槛2 过入场（可选 y_oo>0 / y_oc>0）后按已保存 **lot_base_amount / lot_strong_amount** 建仓/加仓（缺省 1 万 / 2 万；股数=金额/价向下取整到一手，不够一手则买一手；live 开/加不按 `max_positions` 截断，受观察池容量与持仓市值上限约束，默认 15 万；**历史回测** 面向全部观察池，本金默认 20 万，不套 15 万帽）；已持仓且 **未过入场、缺 ranking 或 hard_reject** 则清仓（仅 T+1 可卖部分）。无「持」动作。现金用完即止，不够整手则缩到整百（最少一手），不另留地板。

### 1.2 与底仓做 T 的边界

| 维度 | 策略调仓 | 底仓做 T |
|------|----------|----------|
| 决策问题 | 持有什么、每次加几手 | 既有底仓上日内往返 |
| 信号头 | ranking=w_oo·((ŷ_oo+1)/(1+rot)−1)+w_τc·(ŷ_τc∘w_co·ŷ_co) | v6：C 相对 C_τ 破带选向，现价开第一腿 |
| 持仓寿命 | 未过入场 / 缺分 / hard_reject 清仓；过入场则开或加 | 当日往返 |
| 仓位单位 | 已保存 lot_base_amount / lot_strong_amount（缺省 1 万 / 2 万；按价换算整手） | `origin=t0`（`t0_batch`） |
| 频率 | 每个交易日在已保存「调仓时间」打分并现价一次（默认 09:30–10:00；与历史回测 fill_clock 同源） | 每 5 分钟扫至 11:00 |

调仓是选股 Alpha 的载体；做 T 是调仓底仓上的 timing overlay。两者可独立运行。

### 1.3 A 股 T+1 硬约束

- **买入批次 FIFO 可卖**：T 日买入的股份，到下一交易日才可卖。
- **旧底仓**（无批次记录的历史持仓）视为当日可卖，避免冻结历史账本。
- **卖出受 `clip_sell_shares` 限制**：可卖量 = 非当日买入批次之和。
- 交易必须整手（100 股）。配置金额不够一手则买一手；现金不够一手才跳过。

---

## 2. 调仓模式

| 模式 | 枚举 | 适用场景 | 卖出逻辑 | 买入逻辑 |
|------|------|----------|----------|----------|
| **rank_lots** | 观察池 live + `paper_replay` | **生产主路径** | 未过入场、缺 ranking 或 hard_reject 清仓 | ranking>rank入场 按分数买，过 rank强买 lot_strong_amount 否则 lot_base_amount |
| **持仓规则** | `holding_rules` | 逐票规则引擎 | 单票信号扫描驱动卖出 | 按规则引擎加仓 |

live 与历史回测统一走 `rank_lots`（`watching_matrix` / `backtest_paper_replay`）。

---

## 3. 信号与门槛体系

### 3.1 多目标 ŷ（生产 rank_lots）

| ŷ | 含义 | 调仓中的作用 |
|----|------|-------------|
| **ranking** | w_oo·((ŷ_oo+1)/(1+rot)−1) + w_τc·((1+ŷ_τc)(1+w_co·ŷ_co)−1) | 调仓排序。rot=price(τ)/open[T]−1；基准 τ→open[T+1]；ŷ_τc 已是 τ→close；w_co 默认 1 |
| **residual** | w_τc·ŷ_τc + w_oc·remaining(ŷ_oc) | 研究对照。做 T 主分是 ŷ_τc 估 C_τ；remaining(ŷ_oc) 不进破带 |
| ŷ_co | 隔夜 close[T]→open[T+1] | 经 w_co 几何叠进 ŷ_τc；w_co=0 时不进 ranking |

历史回测成交明细列序为股数 / 开盘价 / 收盘价 / 成交价 / 收益率（卖出总额/买入总额−1；未卖完时剩余按收盘市值计入卖出总额）/ **ranking** 预估(真实)（真实=(open[T+1]−price(τ))/open[T]）/ y_oo / y_τc / y_co。不挂 R_τ / y_τw / y_τ30 / y_τ60 / y_τ90。缺模型/缺分显示 —。

未过入场（含可选 y_oo>0 / y_oc>0）的已持仓 → **清仓**。过入场则开仓或加仓。当日打不上分（缺 ranking）不能假装过门槛，按清仓处理。无「持」动作。

### 3.2 核心门槛（rank_lots）

| 参数 | 默认 | 说明 |
|------|------|------|
| `rank_enter` | 0.001 | 门槛1 ranking 入场下限（0.1%；旧 1.01 / 101% 自动换成净收益） |
| `rank_enter_alt` | 0.001 | 门槛2 ranking 入场；缺则跟随门槛1。两档 OR |
| `rank_strong` | 0.001 | 超过则买 lot_strong_amount，否则 lot_base_amount；缺省 2 万 / 1 万。与历史回测表单同一键 |
| `y_oo_gt0` | 关 | 开则入场须 y_oo>0；关=不看。缺分不拦 |
| `y_τc_gt0` | 关 | 开则入场须 ŷ_τc>0；关=不看。缺分不拦 |
| `fusion_w_oo` | 0.6 | ŷ_oo 融合权重 |
| `fusion_w_oc` | 0.4 | ŷ_τc∘隔夜 头权重（键名 fusion_w_oc） |
| `fusion_w_co` | 1 | 叠进 ŷ_τc 的隔夜系数；0=不叠 |
| `cash_floor` | 0 | 不留现金地板。现金不够该手则缩到整百（最少一手）；仍买不起才跳过。强档买不下先试基础手数。 |
| `holdings_mv_cap` | 150_000 | **live** 持仓市值上限；本笔将超则跳过该买。历史回测为 0（不限） |
| `max_positions` | 策略限额 | 仅持仓规则日循环 / 风控仍可读；**rank_lots 开/加不再用它截断** |
| 初始现金（回测） | 200_000 | 历史回测默认本金，表单可改；不留地板 |

配置写在 `execution.rebalance_timing.rank_lots`（仍认旧键 `path_matrix`）。

观察池还可按枢纽「观察池分档」A/B/C 收缩宇宙（`/replay` 勾选，同步 live 闸）。**新开/加**只进允许档；**已持仓掉出允许档 → live 硬清仓**（理由如「可预测性非 A 档 清仓」）。回测天数仍用独立 lookback。**做 T 不套分档**（只在已持底仓上 overlay；v6 估 ĉ / 选腿不吃 ŷ_oo）。未映射票在未选满三档时不进新买。

---

## 4. 生产规则（rank_lots）

实现：`core/paper/rebalance/rank_lots.py`。live：`watching_matrix.py`；历史：`backtest_paper_replay`。

### 4.1 每日 09:30

1. 信息集：窗口截至**昨收**，报价用**今开**（不把今日收盘喂进特征）。过热（mom5≥10% 等）与 live `score_stock` 一样**仍算当日 ŷ**，只标 tip（`mom_chase_risk` / overheat）；**不拦开/加**。历史回测不得把过热票踢出打分名单，否则已持仓明细预估值会冻在最后一天。
2. `ranking`：`w_oo × ((ŷ_oo+1)/(1+rot)−1) + w_oc × ((1+ŷ_τc)(1+w_co·ŷ_co)−1)`，`rot = price(τ)/open[T]−1`。两项都在 **τ→open[T+1]**；ŷ_oo 做几何剩余，ŷ_τc 已是 τ→close，不再整段减 rot。09:30 且成交价=开盘时 rot=0，左边退回 ŷ_oo。真实 label = `(open[T+1]−price(τ))/open[T]`。隔夜几何复合仍在 ŷ_τc 项里；净收益=落盘百分点÷100。缺 `y_spec_tau` 的旧行才退回开盘基准融合再减 rot。
3. 已持仓且未过入场、缺 ranking、hard_reject、或 live 分档掉出允许档 → 清仓（T+1 可卖手数）。过热不因此清仓。
4. 门槛1 ∪ 门槛2 过入场的票按分数买（live 受观察池容量与 `holdings_mv_cap`；历史回测面向观察池全名单、不套市值帽）：建仓或加仓。
5. ranking &gt; rank强 → lot_strong_amount，否则 lot_base_amount（live 与回测同一对；缺省 2 万 / 1 万）。按成交价换算整手；不够一手则买一手。买不下则缩到整百，最少一手。
6. 若缩到一手仍使现金不够（含手续费）→ 跳过该买。

历史回测成交与 live 不同：ŷ_oo 仍是 **09:30 开盘信息集**。ŷ_τc 随所选 **调仓时间**：09:30 用开盘 Z（`use_minute_tau=False`）；09:35–10:00 用截至该钟的 5 分钟前缀重算（与做 T `rescore_scores_at_fixed_prefix` 同路径，`use_minute_tau=True`）。前缀注入分钟小包/截面后按 `features_tau` 重拆 ŷ_τc，成交明细组成表与做 T 扫描该钟同口径。买卖价取该钟 5 分钟 K——09:30 用首根开盘（无分钟则日开盘），其后用该档收盘。**买入**缺该根则跳过（`reason`＝无有效报价）。**清仓**缺该根则回退：该钟之后～10:00 下一根 → 09:30 / 日开盘（账上 `缺HH:MM回退…`；`constraints.sell_px_fallback`）。日分价闸只用 09:30–10:00 窗口分钟（尾盘残缺仓不当开盘锚）。**有窗口分钟时**复用做 T 日分价闸（`resolve_t0_price_space`）：共用框「日分价闸」默认开；`|日昨/分昨−1|` 超阈（默认 5%，跟做 T 配置；0=关）则该票当日 skip（`price_space_mismatch`）；比的是昨收锚，**不是**成交价。关闸不拦，仍记错位次数。live 自动调仓 / 手动预演窗口 = 已保存 fill_clock～10:00（默认 09:30）；到点后现价成交一次，ŷ_τc 用因果末根 5m（≤10:00），不走此闸。

### 4.2 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `rank_enter` | 0.001 | 门槛1 ranking 入场（0.1%） |
| `rank_enter_alt` | 0.001 | 门槛2 ranking 入场；OR 门槛1 |
| `rank_strong` | 0.001 | 超过买 lot_strong_amount，否则 lot_base_amount |
| `lot_base_amount` | 10000 | 入场金额（元）；按价换算整手，不够一手则买一手；与历史回测表单同一键 |
| `lot_strong_amount` | 20000 | 强档金额；不少于 lot_base_amount |
| `fusion_w_oo` | 0.6 | ŷ_oo 权重 |
| `fusion_w_oc` | 0.4 | ŷ_τc∘隔夜 头权重（键名 fusion_w_oc） |
| `fusion_w_co` | 1 | 叠进 ŷ_τc 的隔夜系数 |
| `y_oo_gt0` | 关 | 开则入场须 y_oo>0 |
| `y_τc_gt0` | 关 | 开则入场须 ŷ_τc>0 |
| `cash_floor` | 0 | 不留现金地板；现金不够该手则缩到整百（最少一手） |
| `holdings_mv_cap` | 150_000 | live 持仓市值上限；历史回测为 0 |
| `fill_clock` | 09:30 | **仅历史回测**：5m 成交钟 09:30–10:00；>09:30 时 ŷ_τc 用该钟前缀重算 |
| `price_space_gate` | 开 | **仅历史回测**：共用框「日分价闸」。有分钟时 |日昨/分昨−1| 超阈则 skip；阈跟做 T（默认 5%，0=关）。关则不拦，仍记错位次数 |

配置键优先 `rebalance_timing.rank_lots`，仍认旧键 `path_matrix`。

### 4.3 动作码

`open` / `add` / `exit` / `skip`（无有效报价、日分价错位、现金不足、T+1 不可卖）。历史回测 `/replay` 成交账动作列展示 `reason`，可下 CSV；跳过腿不计命中率。命中率对照 `sign(ranking)=sign(realized_ranking)`，最后一个交易日无次日开、不进分母。净值图下方 **分票贡献** 表按窗口盯市盈亏排序（贡献%=盈亏/回测本金）。

---

## 5. 成交时机（rebalance_timing）

### 5.1 execution_mode

| 模式 | 行为 |
|------|------|
| `next_open`（默认） | **交易时段内按现价成交**；收盘后挂次日开盘单（09:15–10:00） |
| `close` | 确认即成交 |

> 产品口径：纸面盘中是准实盘（A 股连续竞价可买卖），不是"收盘决策、开盘才执行"。回测默认 `next_open` 是研究侧防未来函数，与纸面盘中成交分开。

### 5.2 开盘窗与追价

- `open_fill_after_hm` / `open_fill_until_hm`：次日开盘成交窗（默认 09:15–10:00）
- `pending_chase_interval_min`：未成交挂单盘中追价间隔（默认 10 分钟）
- `pending_chase_eod_hm`：近收盘改用现价强平（默认 14:50）

### 5.3 自动调仓 Web Worker 与盯盘

Follow「策略调仓」运行卡与「做 T」同结构：进程面板 → 面板外盯盘框。

- 每个交易日 **fill_clock～10:00** 现价成交一次（默认 09:30）；**手动预演 / 确认落账与自动 Worker 同一窗口**；过点不补跑、不挂开盘单（与 §5.2 挂单开盘窗 09:15–10:00 分开）。
- 开关与上次落账写 `data/rebalance_auto_worker.json`。
- API：`GET/POST /api/paper/rebalance/worker`，返回 `worker` + `desk`。
- 盯盘：落账前按持仓占位「监视」；落账后开 / 加 / 减 / 清 / 持。摘要芯片格式同做 T（`持仓 n`，为零的阶段不显示）；表列标的 / 动作 / 阶段 / 手数 / rank / 类别 / 说明 / 操作。
- 上次落账：`时间 · 自动|手动 · 卖 n · 买 n`（无 `last_run_ts` 则「尚无记录」）。
- 做 T worker 在调仓未完成且仍在开盘窗内会等待（`t0_wait_for_rebalance`）。

---

## 6. 成本模型

与做 T 同源（`cost_params` / `resolve_cost_model`），默认 `simple_cn`：

| 项 | 默认值 | 说明 |
|----|--------|------|
| 佣金 | 2.5 bps | 最低 5 元 |
| 印花税 | 5 bps（仅卖出） | |
| 滑点 | 3 bps | 研究回测默认 |

每腿成交写入 `net_cash_delta`（含手续费与滑点）。

---

## 7. 风控体系

### 7.1 组合级

| 指标 | 默认限额（short） | 超限行为 |
|------|-------------------|----------|
| 最大回撤 | 20% | 硬拦全部买入（`buys_blocked`） |
| 目标回撤 | 12% | 回撤低于此值恢复加仓 |
| 单票上限 | 25% | 主动减仓至限额 + 买入逐笔缩量 |
| 行业上限 | 40% | 行业内最低 ŷ 先减 + 买入逐笔缩量 |
| 最大持仓数 | 20 | 买入前检查 |

### 7.2 单笔级

- 整手约束（100 股）
- T+1 可卖量约束
- 涨跌停 / 停牌跳过
- 过热因子（提示 / ŷ 对照列）：`signal_config.hard_reject` 阈值仍用于标注 mom5/mom3/当日涨幅；**不拦开/加**。生产 `hard_reject` 只用于日线不足等质量门。
- production ŷ 质量闸

### 7.3 换手级

- 双边换手软上限（`max_turnover_pct`）
- 卖出侧未满可溢出给买入侧
- 半仓背包重试榨干剩余预算

---

## 8. 关键参数速查

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

## 9. 代码结构

```
core/
├── execution.py                    # ExecutionSpec 解析（t0/rebalance/coupling/timing）
├── stance.py                       # Stance 规则引擎
├── strategy.py                     # 策略 Spec 注册表
├── paper/
│   ├── rebalance/
│   │   ├── __init__.py             # 再导出 match / turnover / force_trim / 配置
│   │   ├── orchestrator.py         # 日循环 holding_rules（Follow 不走这里）
│   │   ├── watching_matrix.py      # 观察池 live：算分 + rank_lots
│   │   ├── rank_lots.py            # 生产调仓：τc ranking · 已保存金额换手
│   │   ├── auto_worker.py          # 09:30–10:00 自动调仓（Web 进程守护）
│   │   ├── desk.py                 # 今日盯盘状态（对齐做 T worker desk）
│   │   ├── path_matrix.py          # rank_lots 配置读写（仍认旧键 path_matrix）
│   │   ├── force_trim.py           # 膨胀减仓（live-align / 单测）
│   │   ├── turnover.py             # 换手预算
│   │   ├── cash_reserve.py         # 现金保留
│   │   └── match.py                # 涨跌停/停牌匹配
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

## 10. 与做 T 的耦合

通过 `coupling.t0_vs_stance` 控制：

| 模式 | 行为 |
|------|------|
| `independent`（默认） | 调仓与做 T 互不影响 |
| `skip_if_avoid` | stance=avoid 时跳过做 T |
| `only_if_hold` | 仅 stance ∈ {wait, probe, buy_light} 时允许做 T |

调仓卖出时若做 T 已开 leg1（持仓被做 T 占用），会跳过该票的调仓卖出（`load_rebalance_t0_sell_blocks`），避免与做 T 抢仓。

---

*文档版本：v1.1 · 2026-09-10*
