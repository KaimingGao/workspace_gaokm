# 策略调仓产品文档

> A 股 T+1 **rank_lots** 调仓：每个交易日 09:30 用 y_fuse / y_on 排序，按 200/500 股开仓或加仓。非实盘、不代客下单。

---

## 1. 产品定位与本质

### 1.1 一句话定义

每个交易日 **09:30 开盘**，对观察池打 y_fuse（预期 **open[T]→close[T]**）与 y_on（预期隔夜 close[T]→open[T+1]），按 `ranking = (1 + y_fuse/100) × (1 + α × y_on/100) − 1` 排序（展示百分数；α 默认 0，隔夜不参与；α=1 时 ranking ≈ open[T]→open[T+1]），过 rank入场以 **200 或 500 股** 建仓/加仓（live 开/加不按 `max_positions` 截断，受观察池容量与持仓市值上限约束，默认 15 万；**历史回测** 面向全部观察池，不套 15 万帽）；已持仓且 **ranking &lt; 0** 则清仓（仅 T+1 可卖部分）。现金用完即止，不另留地板。

### 1.2 与底仓做 T 的边界

| 维度 | 策略调仓 | 底仓做 T |
|------|----------|----------|
| 决策问题 | 持有什么、每次加几手 | 既有底仓上日内往返 |
| 信号头 | y_fuse（trade⊕nc）+ y_on 进 ranking | v6 收盘带宽：ĉ=ĉ_τ，破 ĉ±δ |
| 持仓寿命 | 直到 ranking&lt;0 清仓（未买不卖） | 当日往返 |
| 仓位单位 | 200 / 500 股 | `origin=t0`（`t0_batch`） |
| 频率 | 每个交易日 09:30 打分；live 09:30–10:00 现价一次；历史回测可选 09:30–10:00 每 5 分钟 5m 成交 | 每 5 分钟扫至 11:00 |

调仓是选股 Alpha 的载体；做 T 是调仓底仓上的 timing overlay。两者可独立运行。

### 1.3 A 股 T+1 硬约束

- **买入批次 FIFO 可卖**：T 日买入的股份，到下一交易日才可卖。
- **旧底仓**（无批次记录的历史持仓）视为当日可卖，避免冻结历史账本。
- **卖出受 `clip_sell_shares` 限制**：可卖量 = 非当日买入批次之和。
- 交易必须整手（100 股），不足 1 手跳过。

---

## 2. 调仓模式

| 模式 | 枚举 | 适用场景 | 卖出逻辑 | 买入逻辑 |
|------|------|----------|----------|----------|
| **rank_lots** | 观察池 live + `paper_replay` | **生产主路径** | 仅 ranking&lt;0（或 hard_reject）清仓 | ranking&gt;rank入场 按分数买，过 rank强买 500 否则 200 |
| **持仓规则** | `holding_rules` | 逐票规则引擎 | 单票信号扫描驱动卖出 | 按规则引擎加仓 |

横截面 TopK（`simulate_cross_section_rebalance` / λ 同号闸）与分池簿已删除。live 与历史回测统一走 `rank_lots`（`watching_matrix` / `backtest_paper_replay`）。

---

## 3. 信号与门槛体系

### 3.1 多目标 ŷ（生产 rank_lots）

| ŷ | 含义 | 调仓中的作用 |
|----|------|-------------|
| **y_fuse** | w_trade·ŷ_trade + w_nc·ŷ_nowcast，再按缺口映剩余 | 预期 **open[T]→close[T]**（百分点）。ŷ_trade/ŷ_nowcast 仍是昨收口径；09:30 缺口已实现，remaining=(1+cc)/(1+gap)−1 |
| **ŷ_on** | 预期隔夜 close[T]→open[T+1] | 乘进 ranking：`(1+y_fuse/100)×(1+α×y_on/100)−1`；α 默认 0；α=1 对齐 T 开→T+1 开 |
| **ranking_score** | 上式（净收益，展示百分数） | &lt;0 清仓；&gt; rank入场 才开/加；&gt; rank强 买 500 股 |
| ŷ_trade / ŷ_nowcast | 融合分量 | 只进 y_fuse，不再单独做 path/on 同号闸 |
| ŷ_path | 极值时间序 | **不做**策略调仓闸（仅底仓做 T 仍用） |

未买但 ranking ≥ 0 → **续持**，不因排名靠后而卖。

### 3.2 核心门槛（rank_lots）

| 参数 | 默认 | 说明 |
|------|------|------|
| `rank_enter` | 0.012 | ranking 选股下限（1.2%；旧 1.01 / 101% 自动换成净收益） |
| `rank_strong` | 0.012 | 超过则买 500 股，否则 200 股（1.2%） |
| `y_on_alpha` | 0 | 隔夜系数 α∈[0,10]；ranking=(1+y_fuse/100)×(1+α×y_on/100)−1 |
| `cash_floor` | 0 | 不留现金地板。现金不够该手则跳过（强档买不下先退基础手数）。旧 50 万配置忽略。 |
| `holdings_mv_cap` | 150_000 | **live** 持仓市值上限；本笔将超则跳过该买。历史回测为 0（不限） |
| `max_positions` | 策略限额 | 仅持仓规则日循环 / 风控仍可读；**rank_lots 开/加不再用它截断** |
| 初始现金（回测） | 200_000 | 历史回测默认本金，表单可改；不留地板 |

OOS 失败组禁止新开/加仓。配置写在 `execution.rebalance_timing.rank_lots`（仍认旧键 `path_matrix`）。

观察池还可按拟合档收缩宇宙：`cluster_scoring.universe_fit_tiers`（`A`/`B`/`C` 可多选，默认三档=不过滤）。只影响 **rank_lots 新开/加**；已持仓仍可卖/持。历史回测 `/replay` 调仓腿可按次另选，便于 A vs A+B vs 全档对照。**做 T 不套分档**（只在已持底仓上 overlay；v6 估 ĉ / 选腿不吃 ŷ_EOD）。未映射票在未选满三档时不进新买。OOS 失败禁买与分档过滤独立。

---

## 4. 生产规则（rank_lots）

实现：`core/paper/rebalance/rank_lots.py`。live：`watching_matrix.py`；历史：`backtest_paper_replay`。

### 4.1 每日 09:30

1. 信息集：窗口截至**昨收**，报价用**今开**（不把今日收盘喂进特征）。
2. `y_fuse`：先 `w_trade × ŷ_trade + w_nc × ŷ_nowcast`（昨收口径），再按今开缺口映成 **open[T]→close[T]**（百分点）。
3. `ranking = (1 + y_fuse/100) × (1 + α × y_on/100) − 1`；缺 y_on 视为 0；α 默认 0。α=1 时对齐 **T 开→T+1 开**。展示百分数。
4. 已持仓且 ranking &lt; 0 → 清仓（T+1 可卖手数）。
5. ranking &gt; rank入场 的票按分数买（live 受观察池容量与 `holdings_mv_cap`；历史回测面向观察池全名单、不套市值帽）：建仓或加仓。
6. ranking &gt; rank强 → live 500 股 / 回测默认 200 股（表单「强档股数」），否则 live 200 / 回测入场股数（默认 100）；回测强档买不下则退入场手数。
7. 若本笔买入会使现金不够（含手续费）→ 跳过该买。
8. 未买且 ranking ≥ 0 → 持有。

历史回测成交与 live 不同：分数仍是 **09:30 开盘信息集**；买卖价取所选 **调仓时间**（09:30–10:00 每 5 分钟）的 **5 分钟 K**——09:30 用首根开盘（无分钟则日开盘），其后用该档收盘。缺该根分钟则跳过该票（`reason`＝无有效报价；09:30 可回退日开盘，09:35–10:00 不回退）。live 自动调仓仍是 09:30–10:00 现价一次。

### 4.2 关键参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `rank_enter` | 0.012 | ranking 选股下限（1.2%） |
| `rank_strong` | 0.012 | 超过买 500 股（1.2%） |
| `y_on_alpha` | 0 | 隔夜系数 α∈[0,10]；0=不乘 y_on |
| `cash_floor` | 0 | 不留现金地板；现金不够则停 |
| `fusion_w_trade` | 0.5 | ŷ_trade 融合权重 |
| `fusion_w_nowcast` | 0.5 | ŷ_nowcast 融合权重 |
| `holdings_mv_cap` | 150_000 | live 持仓市值上限；历史回测为 0 |
| `fill_clock` | 09:30 | **仅历史回测**：5m 成交钟 09:30–10:00 |

配置键优先 `rebalance_timing.rank_lots`，仍认旧键 `path_matrix`。旧 λ / 同号闸 / pending_exit 已删除。

### 4.3 动作码

`open` / `add` / `exit` / `hold` / `skip`（无有效报价、现金不足、T+1 不可卖、OOS）。历史回测 `/replay` 成交账动作列展示 `reason`，可下 CSV；跳过腿不计命中率。净值图下方 **分票贡献** 表按窗口盯市盈亏排序（贡献%=盈亏/回测本金）。

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

- 每个交易日 **09:30–10:00** 现价成交一次；过点不补跑、不挂开盘单（与 §5.2 挂单开盘窗 09:15–10:00 分开）。
- 开关与上次落账写 `data/rebalance_auto_worker.json`。
- API：`GET/POST /api/paper/rebalance/worker`，返回 `worker` + `desk`。
- 盯盘：落账前按持仓占位「监视」；落账后开 / 加 / 清 / 持。摘要芯片格式同做 T（`持仓 n`，为零的阶段不显示）；表列标的 / 动作 / 阶段 / 手数 / rank / 类别 / 说明 / 操作。
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
- 过热闸（追涨保护）
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
│   │   ├── rank_lots.py            # 生产调仓：y_fuse/y_on · 200/500 股
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

## 11. 已下线设计（避免误用）

| 旧机制 | 现状 |
|--------|------|
| 分池簿调仓（cluster_book） | 已删除 |
| 横截面 TopK（`simulate_cross_section_rebalance` / λ 同号闸） | 已删除；Follow 与回测统一 `rank_lots` |
| 横截面 sizing 用 `cash × ratio` | 已改为 `min(spendable, equity × ratio)` |
| 软超限整批拦买 | 已改为逐笔缩量 |
| 循环内串行 AkShare | 已改为开环批量预取 |

---

*文档版本：v1.1 · 2026-09-10*
