# core — 领域层

确定性量化逻辑（信号 · 回测 · 纸面 · 研究内核）。**无 LLM、无 HTTP Handler**。  
live / 回测 / 纸面共用同一套打分与调仓规则。

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
- [工程结构轨 A0–A4 / B0–B4](../docs/internal/engineering-track.md)
- [框架债务台账](../docs/internal/framework-review.md)

---

## 边界

| 做 | 不做 |
|----|------|
| 打分、ŷ、调仓规则、纸面账本、回测引擎 | 实盘下单 / OMS（N6） |
| Domain Facade：DS · SS · BS | 直连 HTTP / Agent prompts |
| 经 `ports` + `adapters.bind` 读行情 | 硬 import `adapters.*` / `services.*` / `quant.*`（registry / bind 钩子除外） |

**命名**：对外「模拟」· `/follow` → 对内 `paper`；观察池 ≠ 账本。

---

## 分层出口

```text
Application（services · quant/services）
        ↓
Domain Facade：DS · SS · BS
        ↓
packages（signal / paper / backtest / research / t0 / …）
        ↓
ports + persistence
        ↓
adapters.bind（出站）
```

| Facade | 入口 | 委托 |
|--------|------|------|
| **DS** | `data/facade.py` | `data/` · ports |
| **SS** | `signal_service.py` | `signal/` |
| **BS** | `backtest_service.py` | `backtest/`（研究探针；产品回测 = `paper_replay`） |

---

## 包索引

### 主轴

| 包 | 职责 | 要点 |
|----|------|------|
| `signal/` | 打分 · 因子 · dual_score · 门禁 | 生产路径经 SS；因子扩展走 `factors/meta/registry` |
| `yhat/` | ŷ 契约：特征键 · 模型加载 · 指纹 | signal/backtest/t0 **读**模型经此包；research **写** artifact |
| `paper/` | 模拟账本真相源 | `ledger` · `exec` · `cycle` · `rebalance/` |
| `backtest/` | 研究探针 + **产品回测** | 见下表 `replay_*` |
| `research/` | Ridge / panel / 宇宙加载 | 拟合与面板；被 yhat 转发，不与 signal 顶层互引 |
| `t0/` | 做 T 内核 | 与 replay 共享 `intraday/` 成交钟 / 价空间 |
| `watching/` | 候选宇宙 | `policy` · `repository` · `sources` · `sync_paper`（`store.py` 为兼容门面） |
| `market/` | 代码 · 日历 · 上下文 · **priors** | priors 在 `market/priors/`（旧根路径 shim） |
| `risk/` | 调仓前门禁 · 敞口 | 不 re-export 舆情 prior |

### 回测主路径（`backtest/`）

| 模块 | 职责 |
|------|------|
| `paper_replay.py` | 产品回测主循环 + 公共 API re-export（≤1200 行目标） |
| `replay_fill.py` | 成交钟 / 成交价 / price space |
| `replay_minute.py` | 分钟加载（经 ports / store，无 adapters 硬依赖） |
| `replay_calendar.py` | 交易日日历 |
| `replay_contrib.py` | 日腿与分票贡献 |
| `replay_scoring.py` | 开盘打分 · 前缀重算 · 对接 `replay_score_cache` |
| `replay_sim_rows.py` | 模拟行 · fuse hit |
| `replay_score_cache.py` | replay **私有**打分缓存（勿被 live 依赖） |
| `universe_filters.py` | 纯过滤（无 I/O） |
| `topk_probe.py` | 默认 `topk_oos_curve`；`full_engine=True` 才进 `legacy/` |
| `cost_port.py` | 费率权威源（与 `paper/costs` 同源） |

宇宙日线/基本面加载：`research/portfolio_bars.py`（经 DS；常量取自 `watching.policy`）。

### 基础设施

| 包 / 模块 | 职责 |
|-----------|------|
| `ports/` | 出站契约：`market` · `signal` · `paper_cycle` · `registry` |
| `data/` | MarketDataService · 质量门禁 · 类型化 Protocol |
| `persistence/` | Bars / JSON / 回测结果快照门面（`store.py` 等仍为兼容入口） |
| `platform/` | Job · schedule · manifest · decision / memory / feedback |
| `intraday/` | 成交钟 · 价空间（replay 与 t0 共用） |
| `paths.py` · `env.py` | 路径与环境变量 |
| `live_config_manifest.py` | 多 JSON 指纹一致性 |

根目录大量 `*.py` 仍为兼容 re-export（如 `sentiment_prior.py` → `market/priors/`，`schedule_jobs.py` → `platform/`）。新代码优先进对应包。

---

## 依赖纪律（守卫测）

`tests/test_framework_hardening.py` 强制：

1. `core` 不 hard import `skills` / `services` / `quant`（`ports/registry` lazy bind 除外）
2. 业务读行情经 **DS**，不直调 `ports.query_quote` / `fetch_daily_bars`
3. 业务打分经 **SS**（`signal_service.score_one`），不直 `score_stock`
4. `signal/` 顶层不 import `research.*_ridge` / `*_panel`（经 `yhat` 或函数内 lazy）

Workers（调仓 / 做 T）经 `ports.paper_cycle.get_paper_cycle()`，由 `services.paper_cycle_bind` 注入。

---

## 环境变量（常用）

| 变量 | 含义 | 默认 |
|------|------|------|
| `QUANTLAB_BARS_BACKEND` | `sqlite` \| `json` | `sqlite` |
| `QUANTLAB_STORE_DIR` | 缓存根 | `data/store` |

Bars 迁库细则：[sqlite-migration.md](../docs/internal/sqlite-migration.md)

---

## 相关文档

| 主题 | 文档 |
|------|------|
| 产品主轴 / ŷ | [quant.md](../docs/quant.md) · [design-spine.md](../docs/design-spine.md) |
| 调仓 | [rebalance-logic.md](../docs/rebalance-logic.md) |
| 做 T | [t0-logic.md](../docs/t0-logic.md) |
| 数据层 | [component/data.md](../docs/component/data.md) |
| Service 命名 | [architecture · Service 命名约定](../docs/architecture.md#service-命名约定) |
