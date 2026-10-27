## 数据层（Data Layer）

[← 文档索引](../README.md) · 工程分层见 [架构总览](../architecture.md#架构总览) · 量化缓存细节见 [quant.md](../quant.md) · 日线/5m 运行时见 [行情更新、缓存与预处理](../internal/bars-update-cache.md)

数据层是量化系统的「燃料库」：屏蔽外部数据源差异，为策略 / 回测 / Skill 提供相对干净、统一的数据。  
**本仓库现状**：按需拉取 + 轻清洗 + JSON 短缓存 + **DataService 统一读口** + 观察池日线增量 / 基本面·资讯快照缓存 —— **不是** 全市场批式 ETL / Tick 仓 / 完整财务 PIT。  
**存储选型**：现行坚持本地 JSON/JSONL，不上库；理由与触发条件见下文 [存储选型](#存储选型为何是-json何时才上数据库)。

---

## 成熟模型：五个模块

| # | 模块 | 职责 |
|---|------|------|
| 1 | **采集（Ingestion）** | 从券商 / Tushare / 聚宽 / 行情站等搬运原始数据；定时任务、断点续传、重试 |
| 2 | **清洗与标准化（Cleaning）** | 缺失填充、复权、时间戳对齐、统一字段（如 `open/high/low/close/volume`） |
| 3 | **存储（Storage）** | 时序库存 K 线/Tick；关系库存标的与财务；文件存文本/PDF |
| 4 | **服务与接口（Service）** | 上层唯一窗口：如 `get_price(symbol, start, end, fields)`；**PIT**；热点缓存 |
| 5 | **监控与运维（Monitoring）** | 完整度 / 异常值检查、日志、告警 |

**初期原则**：不必一次做满五块。优先主线 **采集 → 清洗 → 存储 → 简单接口**，先保证准确与 **PIT（无未来函数）**；监控后补。

---

## 本仓库对照（现状）

| 模块 | 成熟目标 | 当前落地 | 主要入口 |
|------|----------|----------|----------|
| **采集** | 定时多源 ETL | **按需 + 观察池预热**：腾讯现价；A 股日线 / 5 分钟 / A 股·港股指数只走新浪；`bars_warmup` / `minute_warmup` / `fundamentals_warmup` / `spot_refresh` | `DataService` · `schedule_jobs` · Skills 实现 |
| **清洗** | 复权一致、日历对齐、PIT | **薄清洗**：`normalize_bars`；日线 as_of；财务/资讯标明 **non_pit snapshot** | `history.normalize_bars` · `data_pit` |
| **存储** | 时序库 / 仓 | **JSON**：日线增量合并 · `fundamentals/` · `news/` 快照；无全市场仓 | `core/store.py` → `data/store/` |
| **服务** | 稳定 `get_price` | **DataService**：领域包 `core/data`（`MarketDataService` / Ports / 信封）+ 薄门面 `facade.py`（dict 兼容） | `core/data/` · `core/data/facade.py` · `core/ports` |
| **监控** | 覆盖率 + 告警 | **覆盖率**：`data_coverage`；warmup/日更出站；`quality` good/thin/empty | `core/data_coverage.py` · `alert_outbound` |

**刻意边界**（与 [architecture · 刻意不做](../architecture.md) 一致）：不先上全市场时序仓 / Tick；**DataService + 观察池增量** 已落地；财务 PIT / 多源对齐另规划。

---

## 当前数据流

```text
Agent / Skill / Research / 纸面
        │
        ▼
   DataService（唯一读口）
        │
        ├─ get_quote ──────────────────► 腾讯 qt（内存约 60s）
        │
        ├─ get_bars (incremental)
        │     ├─ load/merge daily cache ► data/store/daily/{CN|HK|US}/{code}.json
        │     └─ AkShare（qfq）─────────► save_daily_cache + quality
        │
        ├─ get_minute_bars / fetch_minute_bars
        │     ├─ load minute cache ─────► bars.db minute_bars 或 minute/**/*.json
        │     └─ 5 分钟 ─────────────────► 只走新浪 getKLineData（不拉日线）
        │
        ├─ get_spot ───────────────────► AkShare 现货 → spot_a_em.json
        ├─ get_fundamentals ───────────► 快照缓存 data/store/fundamentals/
        └─ get_news ───────────────────► 快照缓存 data/store/news/
```

```mermaid
flowchart LR
  subgraph sources [外部源]
    TX[腾讯行情]
    AK[AkShare]
  end

  subgraph layer [数据层 · 当前]
    Q[StockAPI.query]
    H[fetch_daily_bars]
    S[core/store JSON]
  end

  subgraph upper [上层]
    SIG[signal / advise / screen]
    BT[backtest / paper / research]
  end

  TX --> Q
  AK --> H
  H --> S
  Q --> SIG
  H --> SIG
  S --> H
  Q --> BT
  H --> BT
```

环境变量：`QUANTLAB_STORE_DIR` · `QUANTLAB_DISABLE_CACHE=1`（见 [roadmap · P4.2](../design-spine.md#能力评估与升级规划路线图视角)）。

---

## 分钟线采集架构

做 T 第一触达、ŷ_hl 训练、tail_anomaly 等依赖 **5m 分钟缓存**（默认 period=`5`）。实现集中在 **adapters 层**，单票读口经 `core.ports.market.fetch_minute_bars`；观察池 **强更 / 增量补齐** 直调 `fetch_a_minute_bars_isolated`（子进程超时 kill），**不**在业务里直连数据源。

**与日线（日 K）的差异**：研究台「日线」区块——日常 **「增量补齐」**（`refresh_bars_only`：只对未齐 as-of / 无仓走 `get_bars_batch` **进程池**并行，`incremental=True` **只补新日期、已有日不动**；本地太短或前复权重锚则整窗）；兜底 **「强更日 K」**（同路径 · `incremental=False` **整窗替换**）。写入窗 **`BARS_DAILY_LOOKBACK=1000`** 个交易日（再垫 alpha_oo）；仓裁剪 **`DAILY_BARS_MAX_KEEP=1200`**（须 ≥ 拉取条数）。打新浪前占名额：同时最多 2 只，两次开工至少隔 1 秒（`QUANTLAB_SINA_DAILY_MAX_INFLIGHT` / `QUANTLAB_SINA_DAILY_PACE_SEC`）。观察池末 bar 对齐 as-of（含 **缺1 / 缺2** 交易日分桶），供 **ŷ_oo / IC / OOS** 共用。分钟线亦有 **增量补齐 / 强更 5m** 双入口（差别在跳过条件；新浪窗口都固定，同时刻覆盖）。均走 **子进程隔离**。本地仓表/路径与日 K 不同。

### 分层与入口

| 层 | 路径 | 职责 |
|----|------|------|
| **对外读口** | `core/ports/market.py` · `fetch_minute_bars` | 与日线 `get_bars` 并列；ports 由 `adapters.bind` 注入 |
| **拉取编排** | `adapters/market/minute_history.py` | 缓存 TTL · 只走新浪 5m · merge · 落盘；子进程队列只回根数 |
| **新浪** | `_fetch_sina_minute_bars` | `getKLineData` 5 分钟 JSON；不走 akshare（那个函数会先拉前复权日线） |
| **本地仓** | `core/store.py` · `store_bars_sqlite.py` | `load/save/merge_minute_cache`；默认 SQLite `minute_bars` |
| **批量预热** | `core/schedule_jobs._minute_warmup_core` | 强更：Ready 跳过；与增量共用开工闸（默认 2 只在途、间隔 2s）；单票超时 kill |
| **Web 增量/强更** | `quant/research/minute_status.py` | `GET …/status` · `POST …/refresh`（topup/full）· Job `minute-refresh` |

### 单票拉取流程（`fetch_a_minute_bars`）

```mermaid
flowchart TD
  A[fetch_minute_bars] --> B{本地缓存未过期?}
  B -->|是| C[直接返回缓存]
  B -->|否| ST[新浪 getKLineData]
  ST --> STQ{新浪有数?}
  STQ -->|是| J[merge_minute_bars_by_time]
  STQ -->|否| K
  J --> K[merge 进本地仓]
  K --> L{远端失败?}
  L -->|是| M[回退过期本地 cache:stale]
  L -->|否| N[返回 bars + meta]
```

1. **读缓存**：`use_cache=True` 且 `max_age_hours` 内有效 → 不打远端。默认 TTL **12h**（`MINUTE_CACHE_HOURS`）。`use_cache=False` 仅跳过读短路，仍打远端。
2. **远端**：只打新浪 5 分钟。成功后 `_merge_save_minute_bars`——**与入口 `use_cache` 无关，强制补拉也会落盘**。
3. **失败兜底**：远端空时 `_load_stale_minute`，`data_source` 标 `cache:stale:…`，避免做 T 回测整批挂死。
4. **裁剪**：`MINUTE_BARS_MAX_KEEP=12000`（约 250 交易日 × 48 根/日 5m）。

强更 / schedule 预热使用 `max_age_hours=0.01`，几乎总是尝试刷新；单票失败仍可回退旧缓存。

### 新浪 — 5 分钟唯一源

业务路径只使用 period=`5`。

| 项 | 约定 |
|----|------|
| 接口 | 新浪 `getKLineData`（不复权）。不调用 `ak.stock_zh_a_minute`，避免每只先拉整段前复权日线 |
| 深度 | datalen=1970（约 41 交易日）；不按回看天数切片 |
| 复权 | `adjust=""`（不复权） |
| 频控 | 批量增量与强更：同时 2 只、开工间隔 2s，子进程内不再睡。空返回 / 超时 / 断连后本轮间隔 ×2，最长 8s。单票直拉仍在拉完后睡 10s |

然后 merge：**同日整段以后到源为准**（`merge_minute_bars_by_time` 默认 `lock_calendar_day`）。

### 批量预热与 Web 强更

| 入口 | 行为 |
|------|------|
| `POST /api/schedule/run` · `kind=minute_warmup` | `_minute_warmup_core`：逐票子进程隔离，只走新浪 |
| Web 量化台 · **强更 5m** | `POST /api/quant/minute/refresh` · `mode=full`：新浪近端，单票上限 45s |
| Web 量化台 · **增量补齐 5m** | 同 Job · `mode=topup`：也只走新浪 |
| 状态 | `GET /api/quant/minute/status`（Ready 近 40 交易日无缺等）· `GET /api/jobs/minute-refresh` |

子进程内只打新浪，落盘后队列只回根数，不把整段 K 线交回父进程。增量与强更共用开工闸：同时 **2** 只、间隔 **2s**（`throttle=False`，10s 休眠不进 45s 超时）。空返回、超时或断连后，这一轮后续间隔按 2 倍拉长，最长 **8s**。单票超时 **45s** 后 kill 并继续下一只。**已 Ready** 则跳过远端。单票直拉仍在拉完后睡 **10s**。

### 环境变量（分钟 / 日线新浪闸）

| 变量 | 默认 | 含义 |
|------|------|------|
| `QUANTLAB_MINUTE_WARMUP_SKIP_IF_READY` | `1` | 本地已 Ready 则跳过远端拉取 |
| `QUANTLAB_MINUTE_WARMUP_READY_MIN_SPAN_DAYS` | `40` | Ready 闸：最近这么多个交易日无缺 |
| `QUANTLAB_MINUTE_WARMUP_STALE_HOURS` | `24` | Ready 闸：`fetched_at` 超过则重拉 |
| `QUANTLAB_MINUTE_FETCH_DELAY_SEC` | `10` | 单票直拉：新浪拉取后间隔（秒）。批量增量/强更不走这段 |
| `QUANTLAB_MINUTE_WARMUP_WORKERS` | `2` | 批量分钟同时在途，上限 4 |
| `QUANTLAB_MINUTE_WARMUP_PACE_SEC` | `2` | 增量与强更两次开工的最小间隔（秒） |
| `QUANTLAB_MINUTE_ISOLATED_TIMEOUT_SEC` | `45` | 观察池强更/增量单票子进程超时；`0` 关闭隔离 |
| `QUANTLAB_SINA_DAILY_MAX_INFLIGHT` | `2` | 日线打新浪时同时在途的股票数，上限 8 |
| `QUANTLAB_SINA_DAILY_PACE_SEC` | `1` | 日线两次开工的最小间隔（秒） |
| `QUANTLAB_AK_LOCK_TIMEOUT_SEC` | `90` | 主进程等 `ak_lock` 上限；`0`=无限等 |
| `QUANTLAB_BARS_BACKEND` | `sqlite` | 分钟与日线共用 bars 后端 |

### 与 DataService 的关系

分钟线 **尚未** 完全收入 `MarketDataService.get_bars` 形态；研究/做 T 单票经 **ports `fetch_minute_bars`** → `minute_history`；观察池批量强更/增量走 **`fetch_a_minute_bars_isolated`**（子进程），与日线 DataService 路径 **并行**。存储仍走 **A1** 同一 `bars.db` / `minute_bars` 表（或 JSON 回退）。

---

## 软合约（上层应依赖的形状）

**日线 bar**（`normalize_bars`）：

```text
{ date, open, high, low, close, volume, amount? }
```

`volume` 仅成交量；`amount` 为独立成交额（有则保留）。二者不再混用。

**实时行情**（`StockAPI.query`）：`success` + `price` / `change_*` 等；同 symbol 约 60s 内存缓存。

**质量与来源**：缓存条目带 `quality.level`（`good` / `thin` / `empty`）与 `data_source`；live 若走 `quote_fallback`，与回测完整日线 **可能不一致** —— 解读时必须说明。

**P1 生产评分门禁**（`allows_production_score` · `score_stock`）：仅 `quality.level=good` 且非 fallback 可进生产 `score`；`thin` / `empty` / `quote_fallback` → `hard_reject`（`quality_gate=True`），**不调用** `score_bars`。历史回测仍直接调 `score_bars`，不受门禁影响。研究可传 `bypass_quality_gate=True`。`get_bars(reject_quote_fallback=)` / offline 路径默认拒绝伪日线。

**行业 vs 板别（DS-R2）**：`_sector_for` 只读 `sector_map`（未映射=`未分类`）；板别启发式在 `_board_for` / exposure `styles`。行业限额与中性化只吃 sector。

**ann_missing（DS-R3）**：`fundamentals.ann_missing_policy` 默认 `zero_weight`；码占比超 `ann_missing_code_ratio_block` 时 DQ=`bad`。

**复权策略**：声明策略 `DEFAULT_ADJUST_POLICY=qfq`，写入 Run Manifest 顶层 `adjust_policy`；实际源可能为 `none` / `cached`（见 `infer_adjust`）。

**PIT 最小约定**（日线 as_of 已接线；财务 history 面板 R1 最小可用）：

| 数据类型 | PIT？ | 说明 |
|----------|-------|------|
| 日线回测窗口 | **是（研究近似）** | `core/data_pit.window_as_of`；单票/组合回测打分窗只含决策日及以前；`get_bars(..., as_of=)` 可切条 |
| 基本面 | **live+研究 as_of（X0）** | live `score_stock` 经 `resolve_live_fundamentals` 与 panel/OLS 同源；缺 ann 标 `ann_missing`；覆盖见 DQ / sample_ops |
| 资讯标题 | **否** | 实时拉取，不作历史面板；舆情因子演进见 [risk.md · 舆情层](./risk.md#舆情与另类数据sentiment--alt-data) |

### 验证宇宙约定（V0）

| 项 | 约定 |
|----|------|
| 配置 | `data/validation_universe.json`：`include_only` 非空则只用该列表，否则=观察池。屏蔽某票从观察池删除 |
| 空财务 | `GET /api/ops/empty-fundamentals`；长期拉不到的码从观察池删除，勿用 demo ladder 冒充覆盖 |
| 真实多期入库 | `python3 research/sample_ops_run.py ingest-history --codes …`；调度 `fundamentals_warmup` **默认**串联 ingest（可传 `ingest_history=false` 关闭） |
| 禁止 | `seed-ladder` / `synthetic_demo` 点不得计入「策略已验证」；闸门看 `real_multi_coverage` |

**约定**：生产决策不得依赖「当日不可见」的未来 bar / 未来财报；`pit_report.fundamentals` 汇总 resolved_ok / missing_as_of。  
这与产品本质一致：**只根据当时已发生事实做影响估计**（见 [design-spine.md](../design-spine.md)）。

---

## 存储布局（运行时）

| 路径 | 内容 |
|------|------|
| `data/store/daily/{CN\|HK\|US}/{code}.json` | 日线 bars + `fetched_at` + quality + `date_min/max`（增量合并；默认不入 git） |
| `data/store/bars.db` · `minute_bars` / `minute_cache_meta` | 分钟线（默认 SQLite）；或 `data/store/minute/{period}/CN/{code}.json` |
| `data/store/fundamentals/{code}.json` | 基本面快照 + `history[]`（真实多期 / 可选 synthetic_demo）+ `fetched_at` |
| `data/store/news/{code}.json` | 资讯标题快照 + `fetched_at`（non_pit） |
| `data/store/spot_a_em.json` | A 股现货筛选磁盘兜底 |
| `data/watching.json` · `paper.json` · `signal_config.json` … | 产品配置 / 账户（非行情仓） |
| `data/reports/` | 量化日报归档 |

详见 [data/README.md](../../data/README.md)。

---

## 存储选型：为何是 JSON，何时才上数据库

**结论（现行）**：配置与账本继续 JSON；**日线/分钟线缓存**走工程结构轨 **A1**：默认 SQLite WAL（`QUANTLAB_BARS_BACKEND=sqlite`），可回滚 `json`。账户、信号配置、交易流水仍 **本地 JSON / JSONL**——与「策略验证、观察池级规模、暂不接实盘」对齐。

与 [本章 § 刻意不做](#架构总览)、[§ 工程结构轨 A0–A4](#工程结构轨a0a4)、[§ SQLite 改造](#日分钟线缓存-sqlite-改造方案) 一致：不为「专业感」把全部状态塞进一个库；行情按规模升 SQLite，配置保持可 diff。

### 各类数据落盘对照

| 类别 | 落盘 | 典型路径 | 说明 |
|------|------|----------|------|
| **模拟账户** | JSON | `data/paper.json` | 假钱账本（现金 · 持仓 · 成交）；非券商实盘 |
| **观察池** | JSON | `data/watching.json` | 产品状态，非行情仓 |
| **信号 / 规则配置** | JSON | `signal_config.json` · `position_rules.json` | 人审可改；晋升有备份约定 |
| **日线 / 分钟线** | SQLite（默认）或 JSON | `data/store/bars.db` · 或 `daily|minute/**/*.json` | `QUANTLAB_BARS_BACKEND`；上层经 DataService / ports |
| **基本面 / 资讯** | 按标的 JSON | `data/store/fundamentals|news/` | 快照 + PIT 面板；不进 bars.db |
| **决策 / TTM 事件** | JSONL 追加 | `decisions.jsonl` · `ttm_events.jsonl` | 流水审计，轻量追加写 |
| **日报 / 告警** | JSON · MD | `quant_daily.json` · `reports/` · `alerts/` | 运行时产物 |
| **回测快照** | JSON | `last_portfolio_backtest.json` · `last_t0_backtest.json` | 刷新恢复 KPI / 做 T 结果；不重跑 |

上层统一经 **DataService** / `core/store` 读写；禁止业务层散落直接扫盘当「隐式数据库」。

### JSON 适用原因（现行）

- **规模匹配**：观察池级日线，不是全 A / Tick
- **可读可 diff**：配置与账本可 git / 手改 / 备份；零运维
- **产品阶段**：只做假设 → 回测 → 纸面；无多账户并发 OMS
- **边界清晰**：全市场数仓 / Tick / 多源对齐 — **不做（锁定）**

### 已知局限（可接受，直到触顶）

| 风险 | 何时会痛 |
|------|----------|
| **并发写覆盖** | 多进程同时改 `paper.json` |
| **聚合查询弱** | 跨票 / 跨日 SQL 式分析要自己扫文件 |
| **文件膨胀** | 全市场多年日线、分钟线、Tick |
| **事务与强审计不足** | 实盘订单、资金流水要回滚 / 对账 |

单机、单用户、策略验证阶段，上述多数尚未成为瓶颈。

### 何时再上库（分类型，而非一刀切）

| 触发条件 | 建议方向 |
|----------|----------|
| 现行（策略验证 / 纸面） | 配置/账本 **JSON**；bars **SQLite 默认**（可切 `json`）；读写收口 DataService / store |
| 观察池再变大、跨票聚合仍痛 | 可再评估 **Parquet / 按日分区**（与 bars.db 并存，不吞配置） |
| 接实盘 OMS、多账户、强审计（N6 闸门后） | 账户与成交 → **SQLite / Postgres**；行情仍可用文件或时序库 |
| 全市场批式研究 | 专门行情仓；**与产品配置 JSON 分开** |

原则：**配置与小状态继续文件；账本按一致性需求升级关系库；行情按规模升级列式/时序** —— 不要「全部迁进一个 SQLite」当银弹。

---

## 演进（M1 路径内已收口 / 仍待）

| 项 | 状态 |
|----|------|
| **M1.1 DataService 收口** | **已落地**：`get_quote/bars/fundamentals/news/spot`；quant/paper/research 经 DataService |
| **ports adapter 注入** | **已落地**：`adapters.bind` → `core.ports.registry`；实现一律来自 `adapters.*`（skills 仅 shim） |
| **M1.2 观察池覆盖率** | **已落地**：`data_coverage` · `bars_warmup` / `paper_daily` 汇总 + 出站 |
| **M1.3 基本面/资讯快照缓存** | **已落地**：`data/store/fundamentals|news` · `non_pit` · `fundamentals_warmup` |
| **M1.4 观察池日线增量** | **已落地**：`merge_bars_by_date` · `fetch_daily_bars(incremental=True)` |
| 复权多策略 `qfq\|hfq\|raw` | **D2 已落地**：`get_bars(adjust=)` · 缓存 `adjust_policy` 防混用；hfq 不可用回退 |
| 财务公告日 PIT | **D0 已加深**：ingest 写 `ann_date`/`available_as_of`；无则 `ann_missing` |
| 交易日历 lite | **D3 已落地**：`core/market/calendar.py` |
| DQ 中心 | **D4 已落地**：`GET /api/ops/data-quality` |
| **DS-R0 日线缓存 path_lock** | **已落地**：`save_*` / `merge_save_daily_cache` / snapshot 写路径持锁 |
| **DS-R1 TTL 一元化** | **已落地**：`core/data_policy.py`；store IO 计入 DQ `store_io` |
| **DS-R2 行业≠板别** | **已落地**：`_sector_for`→未分类；`_board_for`/styles 仍板别 |
| **DS-R2.1 清洗伪主题** | **已落地**：`is_board_label`；`scrub_board_labels_from_sector_map`；sync 不再写板别 |
| **DS-R2.2 现货行业补全** | **已落地**：`enrich_sector_map_from_spot`；调度 `sector_map_enrich` / `spot_refresh` 串联；风控覆盖闸门 |
| **DS-R3 ann_missing 门禁** | **已落地**：`ann_missing_policy`（默认 zero_weight）；DQ ratio→bad |
| **DS-R4 拒 quote_fallback** | **已落地**：`reject_quote_fallback`；伪日期 quality=empty；offline 默认拒 |
| **DS-R5 刷新锁/裁剪** | **已落地**：`code_refresh_lock`；日线/分钟 trim 上限 |
| **分钟线** | **5 分钟只走新浪** `getKLineData`（不拉日线）。见 [§ 分钟线采集架构](#分钟线采集架构) |
| **DS encapsulate** | **已落地**：`core/data/`（`BarsResult`/`DataEnvelope` · `MarketPorts` · `MarketDataService`/`ResearchDataService`）；`facade.py` 薄门面仍返回 dict |
| **DS-E1 读口收口加深** | **已落地**：paper/threshold OOS/`portfolio_bars` 远端补数经 DS 且拒 fallback；`get_bars_batch` · `set_research_service`；spot `mem_cache` source；空宇宙 coverage=`None`；非 bars 信封显式 `production_ok` |
| **DS-E2 评分读口 + 可观测** | **已落地**：`score_stock.fetch_daily_bars`→DS 缓存+`bars_pack_worker` 池；bars 空宇宙 coverage=`None`；`metrics_snapshot`；batch 默认 adjust；ledger/insights `resolve_market_code` 经 ports |
| **DS-E3 quote/指数/惰性包** | **已落地**：paper quote/batch 经 DS；`get_index_bars`；`portfolio_bars` 离线不打 quote；lazy `core.data.__getattr__`；DQ 暴露 `data_service_metrics` |
| **DS-E4 收口扫尾** | **已落地**：`as_dict` 保留 kind/ok；score/watching/facts/schedule/cluster/event 行情经 DS；warmup 挂 metrics；topk 指数经 DS；portfolio 最终 resolve 跟 offline 语义 |
| **DS-E5 可观测 + 锁** | **已落地**：平台 DQ/调度 last 展示 `data_service_metrics`；`spot_refresh` 挂 metrics；`reset_metrics` 导出；框架锁业务禁直 import ports 读行情；仪表盘指数经 DS |
| **A1 Bars SQLite** | **已落地**：`QUANTLAB_BARS_BACKEND` · `core/store_bars_sqlite.py` · `scripts/migrate_bars_to_sqlite.py`；见 [§ SQLite 改造](#日分钟线缓存-sqlite-改造方案) · [§ 工程结构轨 A0–A4](#工程结构轨a0a4) |
| 全市场数仓 / Tick / 多源对齐 | **不做**（锁定） |

### 采集运维

- `POST /api/schedule/run` kinds：`bars_warmup` · `minute_warmup`（5m 预热 · 只走新浪 · 2 并发 · 开工间隔 2s）· `spot_refresh`（可串联行业补全）· `sector_map_enrich` · `fundamentals_warmup`（默认 ingest 真实 history）· `paper_daily` · `validation_prepare` · `sentiment_scan`
- Web 分钟强更：`POST /api/quant/minute/refresh` · `GET /api/quant/minute/status` · Job `GET /api/jobs/minute-refresh`
- 验证宇宙卫生：`GET /api/ops/validation-hygiene` · `POST /api/ops/validation-prepare`
- 舆情 as_of：`GET /api/ops/sentiment-as-of?code=&as_of=`
- 覆盖率字段：`coverage` / `data_coverage`（mapped、stale、levels）
- 告警码：`bars_coverage_thin` · `bars_stale` · `bars_empty` → `alert_outbound`
- 预热默认标的：验证宇宙（`validation_universe`）优先，否则 watching

目标交互（演进后）：

```mermaid
flowchart LR
  TX[腾讯] --> Q[quote]
  AK[AkShare] --> H[history]
  H --> S[store]
  Q --> DS[DataService]
  H --> DS
  S --> DS
  DS --> SIG[策略 / Skill]
  DS --> BT[回测 / 纸面]
```

与路线图差距表一致：[design-spine.md · 路线图](../design-spine.md#能力评估与升级规划路线图视角)（数据行：实时拉取 → 本地历史库、复权一致、多源对齐）。

---

## 相关代码速查

| 能力 | 路径 |
|------|------|
| 现价 | `adapters/market/quote_api.py` |
| 日线拉取 + normalize | `adapters/market/history.py` |
| **分钟拉取（新浪 5m）** | `adapters/market/minute_history.py` |
| 日线缓存 R/W · quality | `core/store.py` |
| **DataService（N1/M1）** | 门面 `core/data/facade.py`（模块函数 → dict）；领域 `core/data/`（`MarketDataService` · `BarsResult` · `MarketPorts`） |
| TTL / 质量阈值 | `core/data_policy.py` |
| 财务 PIT 面板 | `core/fundamentals_pit.py` |
| 样本运营 / 验证宇宙 | `core/sample_ops.py` · `core/validation_universe.py` · `GET /api/ops/sample-status` |
| 成熟闸门 | `core/maturity_gate.py` · `GET /api/ops/maturity-gate` · [design-spine.md · N6 准入](../design-spine.md#n6-真真盘准入备忘) |
| 源一致性审计 | `core/data_consistency.py` |
| 覆盖率 | `core/data_coverage.py` |
| 路径 | `core/paths.py`（`STORE_DIR`） |
| 调度预热 | `core/schedule_jobs.py`（`bars_warmup` · `minute_warmup` · `spot_refresh` · `fundamentals_warmup`） |
| 观察池分钟状态/强更 | `quant/research/minute_status.py` · `quant/services/quant_service_factors.py` |
| 现货筛选 | `skills/screen/engine.py` |
| 基本面 / 资讯 | `skills/fundamentals/engine.py` · `skills/news/engine.py` |

上层调用约定：Agent 只解读 Skill JSON 中的数字，**不得编造**行情或财务字段（见 architecture 原则「数据与建议分离」）。

---

