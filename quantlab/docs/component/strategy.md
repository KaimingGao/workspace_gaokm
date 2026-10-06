## 策略层（Strategy Layer）

[← 文档索引](../README.md) · 入门见 [quant.md · 入门概念](../quant.md) · 因子/stance 原理见 [quant.md](../quant.md) · 数据输入见 [§ 数据层](./data.md#数据层data-layer)

**定义**：策略 = 一套将市场数据转化为**交易意图**的确定性规则。  
一句话：策略是函数 `f(x) = y` —— `x` 为行情与账户上下文，`f` 为选股/择时/仓位/风控，`y` 为标准化信号（不是直接下真单）。

本仓库 **没有** 经典 `StrategyBase.on_bar` 类体系；等价逻辑拆在 **`score_bars` → `stance` / 回测入场 → 纸面 `rules`**。  
**Q2**：唯一 canonical 策略为 **`short_conservative`**（`core/strategy.py` + `STRATEGY_SPECS[].lifecycle`）：回测参数、模拟 `paper_rules`、默认 `cost_model`、账户 `risk` 限额。研究配置经 **`POST /api/strategy/promote`** 显式晋级（API 仍在；策略中心页不再挂空壳按钮），禁止静默覆盖。

**ExecutionSpec（v1.1）**：`lifecycle.execution` 收编两类纸面动作——**结构层调仓**（持有什么、各占多少；ŷ_trade 排序 + ŷ_oo/ŷ_τ 买卖闸）与 **overlay 做 T**（底仓上 dual_y：y_τ 定方向 + 5m 往返 timing；不改变选股主线）。**ŷ_τ 模型共用、决策接口不同**，见 [quant.md · 策略调仓 vs 底仓做 T](../quant.md#策略调仓-vs-底仓做-t)。纸面 / 做 T 回测经 `core/execution.resolve_effective_execution` 合并  
`DEFAULT → Spec → paper.rules → 请求 → channel runtime_defaults`，禁止入口各自硬编码 `direction` / `path_mode`。  
Web：`GET/POST /api/paper/execution` · `GET .../diff` · `POST .../reset` · **历史回测**改调仓/做 T 规则表单；交易执行只读规格 + 预演/Worker · 策略晋升回显做 T 摘要。  
耦合：`coupling.t0_vs_stance` = `independent` | `skip_if_avoid` | `only_if_hold`（纸面预演按持仓 stance 跳过）。

**自动做 T 落账（Web Worker）**：Follow 页 Worker = 本 Web 进程内后台线程，**5 分钟轮询 + 5m 盯盘**（与 K 线周期对齐；分钟线 `use_cache` TTL ≈5min，无新 bar 跳过打网；**振幅门禁按 5m 前缀 high/low 滚动**，不足则下根 K 重试；交易时段内触达即落账，不再日终整段回放）；开关写 `data/t0_auto_worker.json`，状态写 `data/t0_intraday_state.json`，并同步 `paper.rules.t0_auto.enabled`。  
API：`GET/POST /api/paper/t0/worker`（启停 + 状态）· `GET /api/paper/t0/auto`（`last_run` 只读轮询）。  
手动补跑：Follow「手动预演 / 手动落账」· `POST /api/paper/t0`（不依赖 Worker）。`run_web.py` lifespan 启动时若 worker 开关为 ON 则自动 restore；进程退出 stop。  
外部 cron 仍可用 `schedule_jobs.run_paper_t0`；`paper_daily` 链式触发需 `t0_auto.enabled` 且 `schedule=with_paper_daily`（UI 已移除 schedule 下拉，默认 `after_close`）。

**自动调仓落账（Web Worker）**：Follow「策略调仓」运行卡与做 T 同结构（进程面板 → 面板外盯盘框）。每个交易日在已保存 **fill_clock～10:00** 现价成交一次（默认 09:30），过点不补跑。开关与 last_run / desk 写 `data/rebalance_auto_worker.json`。  
API：`GET/POST /api/paper/rebalance/worker`（`worker` + `desk`）。盯盘落账前按持仓占位监视，落账后开/加/减/清/持；上次落账文案 `时间 · 自动|手动 · 卖 n · 买 n`。手动预演 / 确认落账与 Worker 同一 fill_clock～10:00 窗口，过点不补跑、不挂开盘单。做 T worker 在调仓未完成且仍在开盘窗内会等待。
---

## 黑盒工厂直觉

```text
原材料（行情 / 账户 / 参考信息 / 时间）
        ↓
   图纸（策略规则）
        ↓
成品（买卖意图 + 日志）  →  交给回测引擎或纸面记账（不接券商）
```

---

## 三个核心部分（成熟模型）

| # | 部分 | 回答什么 |
|---|------|----------|
| 1 | **选股与择时** | 买什么 · 何时买/卖 |
| 2 | **仓位管理** | 买多少 · 如何分配资金 |
| 3 | **风险控制** | 止损/止盈 · 账户级红线 |

### 本仓库落点

| 部分 | 当前实现 | 配置 / 代码 |
|------|----------|-------------|
| **选股** | 观察池过滤 + `hard_reject` + `min_score` 排序 TopN | `watching.json` · `signal_config.hard_reject` / `rank` · `screen` |
| **择时（买入侧）** | 因子加权 `score` → stance 分档 / 回测入场阈值 | `score_bars` · `compute_buy_stance` · `stance_thresholds` |
| **择时（卖出侧）** | 部分：持有期 `horizon_days`、纸面 `stop_loss` / `max_hold_days`；`invalidation` 多为**文案参考**非自动单 | `paper.rules` · `invalidation.stop_pct` |
| **仓位** | 纸面：`position_pct` · `max_positions`；组合回测等权/规则调仓 | `paper.rules` · 组合回测引擎 |
| **风控** | 硬拒绝、stance 降档、纸面止损；**账户级**回撤/单票上限见 `core/risk`（调仓前拦截加仓） | `StrategySpec.lifecycle.risk` · `check_account_risk` |

**输出边界**：产出的是意图与模拟成交记录，**现行不代客下单**（策略验证）；实盘待成熟后另立项。

---

## 输入（原材料）

成熟策略通常以 **Context** 注入：

| 输入 | 含义 | 本仓库来源 |
|------|------|------------|
| 行情 OHLCV / 序列 | 当前与历史 K 线 | [data-layer](./data.md#数据层data-layer) → `fetch_daily_bars` / quote |
| 账户状态 | 现金、持仓、盈亏 | `paper.json`（模拟）或回测引擎内存账本 |
| 参考信息 | 名称、行业、停牌等 | 部分经 AkShare / fundamentals；不全 |
| 时间戳 | 交易日 / 是否交易时段 | bar 的 `date`；日线级为主 |

上层应经数据服务取数，策略内尽量不直接调外部源（演进约定见数据层文档）。

---

## 输出（成品）

| 输出 | 含义 | 本仓库形态 |
|------|------|------------|
| 信号列表 | 标的 · 方向 · 目标仓位/数量 · 订单类型 | `stance_label` / `hard_reject`；回测 trade list；纸面 `trades[]` · `signal_log[]` |
| 日志与状态 | 触发原因、内部状态 | `reasons` · `invalidation` · `reject_reason` · paper snapshots |

方向语义对照：

| 成熟模型 | 本仓库常见表达 |
|----------|----------------|
| BUY / SELL / HOLD | `buy_light` / `probe` / `wait` / `avoid`；回测「入场持有 N 日」；纸面买入/调仓/止损卖出 |
| 市价 / 限价 | 模拟多为规则价（现价/开盘代理）；**无**真实 OMS 订单类型 |

---

## 与 Web 五页的关系

| 页 | 策略角色 |
|----|----------|
| `/strategy` | **看图纸**：M prior（个股舆情只在观察徽章） |
| `/watching` | **划狩猎范围**（选股输入名单） |
| `/replay` | 用图纸交**历史卷**（资金模拟在引擎内） |
| `/paper` + `/follow` | 图纸 + **假账**持续记账 |
| `/quant` | 组 β → ŷ 研究与 live 启用，**不自动改**全局 weights |

测试类型与是否需要纸面：[quant.md · 入门概念](../quant.md)。

---

## 代码形态（本仓库 vs 经典）

| 经典写法 | 本仓库 |
|----------|--------|
| `class MyStrategy(StrategyBase)` + `on_bar` / `on_tick` | 无统一 Strategy 基类 |
| 策略内算指标并下意图 | `score_bars(bars) → score`；`compute_buy_stance(...) → stance_*` |
| 配置散落代码 | `data/signal_config.json` + `paper.rules` |
| 执行模块接券商 | 回测引擎 / `core/paper.py` 模拟记账 |

演进若引入 `StrategyBase`，仍应：**数字只来自确定性模块**；LLM 只解读，不改 `score` / `stance_label`。  
与 RL Policy 的关系见 [rl.md · RL 视角](./rl.md#强化学习rl视角)（当前不训练神经网络策略）。

---

## 策略设计文档模板（填空）

写代码或改配置前，先花约 10 分钟填完。填不清的格子 = 策略还没想透。

```text
策略名称：（例：双均线突破 / 短线多因子 short_conservative）
策略类型：（趋势跟踪 / 均值回归 / 多因子选股 / 事件驱动）
适用标的：（沪深300 / 全A / 自建观察池 …）
回测周期：（YYYY-MM-DD ～ YYYY-MM-DD）
交易频率：（日线 / 分钟 / Tick）

1. 股票池构建（选股）
   - 基础过滤：（剔除 ST / 次新 / 停牌 …）
   - 行业/板块：
   - 财务/因子筛选：

2. 买入信号（择时）
   - 技术/因子条件：
   - 形态/价格：
   - 组合条件（须同时满足）：

3. 卖出信号（平仓）
   - 止盈：
   - 止损：
   - 时间止损（持有 N 日）：
   - 信号反转：

4. 仓位与资金管理
   - 单票上限：
   - 建仓方式：（一次 / 分批）
   - 分配算法：（等权 / 波动倒数 / …）

5. 风险控制（账户级）
   - 最大回撤限制：
   - 单日亏损限制：
   - 持仓数量上限：

6. 交易成本假设
   - 佣金：
   - 印花税：
   - 滑点：
```

填好后：改 `signal_config` / `paper.rules`，或扩展因子与回测参数；按 [quant-ui.md](../quant-ui.md) 用 `/replay` 与 `/follow` 分别做历史回测与模拟盘。

---

## 示例：当前默认短线策略（已填）

对照生产默认配置（数值以仓库文件为准，下文为摘要）。

```text
策略名称：短线多因子 short_conservative（观察池 + stance）
策略类型：多因子选股 / 短线动能（规则加权，非 ML 拟合）
适用标的：用户配置的 watching 观察池（模拟持仓随交易产生；非整市场自动扫）
回测周期：按次回测参数（如近 120 日）；非固定长样本
交易频率：日线

1. 股票池
   - 基础过滤：日线不足 → hard_reject；近 3 日涨幅≥15% 或跌幅≤-12% → hard_reject
   - 行业/板块：未默认限制（可由 screen / watching 人工圈定）
   - 财务/因子：可解释线性加权（`factor_registry` 白名单，约 20 因子）；估值/质量/成长等缺数据中性 50；截面默认 **行业 + 规模残差** 中性化（可关）；`factor_groups` + 截面相关作去冗提示；生产分禁止 NN；网格「应用最优」须附 OOS/WF，禁止仅 IS 一键 promote

2. 买入
   - score ≥ min_score（默认 55）且非 hard_reject
   - stance：score 区间 → avoid / wait / probe / buy_light，再经 K 线/peer/index 降档
   - 纸面：另受 max_positions、position_pct 约束

3. 卖出
   - 回测：持有 horizon_days 后平仓（简化）
   - 纸面：stop_loss_pnl、max_hold_days、min_hold_score 等（见 paper.rules）
   - invalidation：约 -3% 等为建议文案，非自动下单

4. 仓位
   - 纸面默认：单票约现金 × 15%（position_pct），最多约 20 只（保守短线 15）
   - 组合回测：规则/等权调仓（详见回测模块）

5. 账户风控
   - 有：持仓数上限、硬拒绝、stance 降档、`check_account_risk`（回撤/单票仓位 → 调仓跳过加仓）
   - 弱/无：单日亏损熔断、实盘强平

6. 成本
   - 模拟账户默认 `simple_cn`（佣金+印花税）；`zero` 仅教学/调试并须锁定
   - 回测 / 调仓写出 Run Manifest（策略版本 · 成本 · 规则指纹）
```

配置入口：`data/signal_config.json` · `data/paper.example.json` → `paper.json` · `core/strategy.py` · Web `/strategy` · `POST /api/strategy/promote`。

---

## 相关代码速查

| 能力 | 路径 |
|------|------|
| StrategySpec / 晋级 | `core/strategy.py` · `core/backtest/strategies.py` |
| 因子打分 | `core/signal/scorer.py` · `score_bars` |
| 配置加载 | `core/signal/config.py` · `data/signal_config.json` |
| 买卖分档 | `core/stance.py` · `compute_buy_stance` |
| 历史验证 | `core/backtest/engine.py` · Run Manifest |
| 纸面规则执行 | `core/paper.py` |
| 账户风控 | `core/risk/checks.py` |
| 持仓加减仓规则 | `data/position_rules.json` · `core/position.py`（真仓建议，≠ 纸面策略） |

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [quant.md](../quant.md) | score → stance → 回测 → 纸面 |
| [quant.md · 入门概念](../quant.md) | 名词与测试类型 |
| [quant-ui.md](../quant-ui.md) | `/strategy` 页用法 |
| [§ 数据层](./data.md#数据层data-layer) | 策略输入从哪来 |
| [risk.md · 风控层](./risk.md#风控模型risk-layer) | 账户风控门禁 |
| [design-spine.md · 路线图](../design-spine.md#能力评估与升级规划路线图视角) | Q1–Q5 与远期缺口 |

---

