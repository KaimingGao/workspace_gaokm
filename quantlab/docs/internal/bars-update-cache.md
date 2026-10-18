# 行情更新、缓存与预处理

[← 文档索引](../README.md) · 数据层总览见 [component/data.md](../component/data.md) · 表结构见 [sqlite-migration.md](./sqlite-migration.md)

> 状态：**现行实现**（对照代码，不另起抽象）
> 范围：观察池日线与 5 分钟的更新、`bars.db` 缓存、入库前清洗、Feature Lake / 指纹、回测读仓
> 不覆盖：全市场数仓、Tick、财务 PIT 细节、纸面下单

这份文档描述代码现在怎么走。数据层总览里仍有「日线只写 JSON」的旧图；日线/分钟的运行时以本文为准。

---

## 1. 两条路

写仓和读仓是分开的。刷新负责把观察池补到最新；回测负责在已有仓上取出够长的序列。回测不会因为末根旧就顺便打远端。

```text
研究枢纽「更新日线 / 更新 5m」
        │
        ▼
refresh_bars_only / refresh_minute_only
        │
        ▼
ResearchDataService（拒 quote_fallback）
        │
        ▼
fetch_daily_bars / 分钟隔离拉取
        │
        ▼
bars.db（默认）或 JSON 回退

调仓回测 / 日报
        │
        ▼
load_portfolio_stock_bars
        │
        ├─ 单连接扫 bars.db（忽略 TTL）
        └─ 根数不够的票才经进程池补远端
```

| | 更新（写仓） | 回测（读仓） |
|---|---|---|
| 入口 | `quant/research/bars_status.py` · `minute_status.py` | `core/research/portfolio_bars.py` |
| 问的问题 | 末根到没到期望交易日、条数够不够拉取窗 | 根数够不够 `min_bars` |
| 过期缓存 | `cache_max_age_hours=0`，不因 24 小时 TTL 短路 | `ignore_age=True`，末根旧但根数够就直接用 |
| 远端 | 进程池；日线单票等 60 秒，回测补缺等 25 秒 | 只补短序列 |
| 基本面 | 不在这条路上 | 股票数 ≥ 40 时只读本地快照 |

调度里的 `run_bars_warmup` 是更轻的一条：最多 40 只、默认 `limit=60`，经 `summarize_data_quality` 做增量预热并出覆盖率。它不跑 `refresh_bars_only` 的整窗/缺口分流。

---

## 2. 日线更新

入口是 `refresh_bars_only`。范围是观察池，上限由 `watching_pool_limit` 钳到文件里的 `max_size`，硬顶 `WATCHING_MAX_SIZE = 1000`。

期望末根来自 `expected_latest_daily_bar_date`：交易日 15:05 之前仍算上一交易日；周末和假日用最近一个已过的交易日。不要用日历上的「今天」当缺口终点，否则周日会把周五已齐的仓全部打成远端。

拉取窗：

| 常量 | 值 | 作用 |
|---|---|---|
| `BARS_DAILY_LOOKBACK` | 1000 | 观察池日 K 的交易日窗口 |
| `ALPHA_OO_PANEL_WINDOW` | 62 | 特征垫窗（`_MIN_BARS + 1`，`_MIN_BARS = 61`） |
| 实际拉取条数 | 1062 | `1000 + 62` |
| `DAILY_BARS_MAX_KEEP` | 1200 | 落盘保留上限，必须 ≥ 拉取条数，否则齐窗会被当成短仓反复整窗重拉 |
| `QFQ_LONG_GAP_DAYS` | 40 | 前复权长缺口才整窗重拉（见 [§ 8](#8-已知缝)） |

### 2.1 topup 与 full

`mode=topup`（默认）按本地 meta 分流。meta 只读 `date_max` 和 `bar_count`，不把全日线载入内存。

| 分类 | 条件 | 远端行为 |
|---|---|---|
| 已齐 | 末根 ≥ 期望日，且条数 ≥ 1062 | 不打远端 |
| 整窗 `full_codes` | 没有末根，或条数短于 1062 | `incremental=False` |
| 缺口 `tail_codes` | 末根落后，但条数已经 ≥ 1062 | `incremental=True`，只补尾巴 |

`mode=full` 把全池放进整窗。用来兜复权错位或仓坏。

短历史（上市不足 1062 根）每次 topup 都会整窗重拉。够长的票才走缺口合并。

### 2.2 批量拉取

缺的票交给 `ResearchDataService.get_bars_batch`：

- `cache_max_age_hours=0`：本地条数够也不能短路返回，必须再走增量计划。
- 每批 32 只。`timeout=60` 停在进程池 `AkPool.map` 上，不会传进 `get_bars`。
- 单例研究服务走 `research_bars_pack_worker` 进程池，避开主进程的 AkShare 锁。
- 研究服务默认 `reject_quote_fallback=True`。腾讯现价压出来的假日线会被清空。

远端计数：`data_source` 含 `akshare`，或来源不是 `cache` 开头且有 K 线，算远端成功。来源 `cache:stale`、空包、没有 K 线算失败。

本批 `failed_count = 0` 才调用 `mark_force_latest_bars_done`。来源 `cache:stale`、空包、空 K 线都算失败。标记只比较会话日，供界面上的 `needs_force_latest_bars`，不在拉取前被读取。

### 2.3 单票增量

`adapters/market/history.py` 的 `fetch_daily_bars` 是单票规则。默认复权 `qfq`。缓存里的 `adjust_policy` 和请求不一致则不用这份仓，避免 qfq / raw / hfq 混用。

`_incremental_remote_plan`：

1. 没有本地末根：整窗。
2. 末根日期已经 ≥ 完整 as-of（或 ≥ 今天）：`skip_remote`，直接返回本地。
3. 本地条数补上缺口后仍凑不够 `limit`：整窗。
4. 前复权且日历缺口 > 40 天：整窗，避免分红后新旧段错位。
5. 其余：从最后一根（含当日重叠）拉到今天，`fetch_limit` 约为缺口天数加 6，再按日期合并。
6. 前复权且重叠日开高低收对不上：再整窗拉一次。新序列盖得住才替换该复权键下的全部日线；盖不住则返回 `cache:stale`，不删库。`incremental=False`（`mode=full` 或短仓整窗）同样按这个长度条件替换。

同票进入远端前持 `code_refresh_lock`（进程内），并再读一次本地，避免并发穿透重复拉。远端异常且本地还有线时，返回 `cache:stale`。

A 股远端是 AkShare `stock_zh_a_hist`。`qfq` 失败再试不复权；`hfq` 不可用时回退 `qfq`。请求窗口在未给 `start_date` 时约为 `limit * 4` 个日历日。

合并规则 `merge_bars_by_date`：同日以后到为准。新根缺开、高、低、收或成交量则丢掉，不覆盖已有同日，也不新插入。成交量为 0 算齐。

---

## 3. 分钟更新

入口是 `refresh_minute_only`。和日线并行，不进 `get_bars`。单票经 `fetch_a_minute_bars_isolated` 放进子进程，超时杀掉。

| 模式 | 行为 |
|---|---|
| `full` | 只走新浪。本地已 Ready 则跳过 |
| `topup` | 已对齐跳过；跨度够补近端；缺仓或短仓也走新浪 |

Ready 默认要求最近 40 个交易日无缺，且 `fetched_at` 未超过 24 小时。新浪 5 分钟窗口约 41 个交易日。拉完默认睡 20 秒。单票超时 45 秒。

落盘用 `merge_minute_bars_by_time`，默认按交易日整段替换。新的一天必须盖住本地该日已有的时刻，更短、缺头、缺尾的新包整日放弃。

保留上限 `MINUTE_BARS_MAX_KEEP = 12000`（5 分钟全日约 48 根，大约 250 个交易日）。

---

## 4. 缓存

日线和分钟默认进 SQLite `data/store/bars.db`（WAL）。`QUANTLAB_BARS_BACKEND=json` 退回 `data/store/daily|minute/...json`。配置、账本、基本面、舆情仍是 JSON，不进这个库。

| 层 | 存什么 | 失效 |
|---|---|---|
| `daily_bars` + `daily_cache_meta` | 开高低收、成交量、复权策略、末根日期、条数、`fetched_at` | TTL 24 小时；`adjust_policy` 不一致则不用 |
| `minute_bars` + `minute_cache_meta` | 分钟 OHLCV | TTL 12 小时；同日整段替换 |
| `data/store/fundamentals/` | 财务快照 | 24 小时 |
| `data/store/news/` | 舆情标题 | 6 小时；和快照分目录，避免互相覆盖 |
| 现价内存 | 腾讯 quote | 60 秒；过期上限 30 分钟 |
| 研究面板 | （已退役） | collect / 训练只写读 L1 Feature Lake；指纹见 `panel_fingerprint` |
| 调仓打分 | `replay_score_cache` | 按开盘日输入指纹；只缓存 09:30 的打分，其它成交时刻命中后仍重算 |

日线主键是 `(code, date, adjust_policy, market)`。写入是按日覆盖，不整段删除；这次没带到的日期留在库里，超过 1200 根再从最早的日期裁掉。

回测扫盘走 `load_daily_cache_many`：一次连接、按 `(market, code)` 批量读。多线程并发打同一个 `bars.db` 会互相卡住。`load_portfolio_stock_bars` 的 docstring 仍写着「线程池扫本地缓存」，实现已经是单连接；以实现为准。

`QUANTLAB_DISABLE_CACHE=1` 时 `fetch_daily_bars` 不读不写本地仓。`QUANTLAB_STORE_DIR` 改缓存根目录。

---

## 5. 预处理

预处理发生在进仓之后、因子之前。没有单独的 ETL 作业。

### 5.1 字段

`normalize_bars` 把远端行收成：

```text
{ date, open, high, low, close, volume, amount? }
```

`volume` 只取成交量。成交额进 `amount`，不再混进 `volume`。缺开高低收时用收盘价填上再入库；没有收盘价的行丢掉。

SQLite `daily_bars.amount` 可空。打开旧库时补列，不回填。有 `amount` 就原样读出；NULL 的旧行不带这个键，`bar_amount` 仍用 `volume × close`。新写入的成交额会落库。JSON 后端本来就保留 `amount`。

### 5.2 质量

`assess_quality` 给出 `good` / `thin` / `empty`：

| 情况 | 级别 |
|---|---|
| 无 K 线、伪日期（`d0` / `today` 等）、或来源含 `quote_fallback` | `empty` |
| 来源是 fallback、根数 < 15、末根早于 10 天、或 `open ≈ 昨收` 的比例 > 40%（至少检查 20 对） | `thin` |
| 其余 | `good` |

生产评分只接受 `good` 且非 fallback。研究回测可以读 thin，但研究服务会拒绝 quote 退化成的假日线。

### 5.3 服务信封

`MarketDataService.get_bars` 在缓存之上包一层：

- 调用方传了 `as_of` 才做 PIT 切条（含当天）。不传就标明非 PIT，回测应自己用窗口切。
- `infer_adjust` 从 `data_source` 和请求策略得出 `qfq` / `raw` / `hfq`。
- `production_ok` 由质量级别和是否 fallback 决定。

`ResearchDataService` 在此之上默认拒绝 quote fallback：命中则 K 线清空，来源改成 `rejected_quote_fallback`。

回测装载后再过 `drop_daily_series_mismatch`：K 线和代码对不上的序列丢掉，并记入失败列表。6 位请求码按请求码入账，不用解析出来的另一个代码当键，避免把 A 的 K 线记到 B 上。

### 5.4 因子面板

单票日线在 `panel_matrix` 里折成 float64 矩阵，写穿 **L1 Feature Lake**（见 §5.5）。截面瘦 dict 不落盘。

指纹 `bars_fingerprint` / `make_fingerprint`（`panel_fingerprint.py`；`PANEL_CACHE_SCHEMA=3` 参与 fp 串）覆盖日线和分钟线的每一根。中间一根变化会 miss。空序列仍是 `empty`。语义变更时抬 schema → 全池 L1 miss。

---

## 5.5 Feature Lake（L1）与 DayCube（L2）

完整方案见 **[feature-lake-daycube.md](./feature-lake-daycube.md)**。摘要如下。

训练 / 回测 / live 共用的截面物化层（默认开启）：

```text
bars.db
  → L1 Feature Lake   {FEATURE_LAKE_DIR}/v{SCHEMA}/{family}/{code}.npz
  → L2 DayCube        {DAY_CUBE_DIR}/v{SCHEMA}/{universe_hash}/{clock}/{date}.npz
  → fit / paper_replay / watching_matrix / T0
```

| 层 | 模块 | 内容 |
|---|---|---|
| L1 | `core/research/feature_lake.py` | 按股×日 raw 特征（无同伴）；collect 写穿 |
| L2 | `core/research/day_cube.py` | 日 μ/σ、gap breadth / slim；宇宙 hash 绑定 |
| batch | `core/research/batch_score.py` | 池打分后建 cube + rescore；供调仓/回测 |

`refresh_bars_only` 对整窗/缺口票调用 `invalidate_l1_many`，下次 collect 重算。

**尾窗增量**（`incremental_panel.py`）：L1 meta 存 `bars_fp` / `stem_fp`；末根改写或追加 ≤16 根时，oo/τc/co/oo_rank 用 `keep_dates` 重算尾部再 merge。中间复权改写仍全量。

**回测 L1 hydrate**（`lake_replay.py`）：`paper_replay` 启动时 `prefetch_l1_coverage`（dates 索引 + 整票 panel 内存；`fill_clock≠09:30` 额外预热 `tc_HHMM`）；日循环按日切片灌 `sub_scores` / `features_tau` / `features_co`。**须有 oo 行才当命中**跳过 `score_window_as_item`（仅 tc/co 有湖日不够——否则无 `sub_scores`，ŷ_oo / oo_rank 空）；缺 oo 则切窗重算。盘中前缀 ŷ_τc 优先 L1 hydrate，miss 再 `rescore_scores_at_fixed_prefix`。分钟 τ 写 L1 用 `tc_HHMM`（与开盘 `tc` 分目录）。

**单遍 rescore**：`batch_score` 建 DayCube（含 `sub_scores`）后，ŷ_oo / τ / co 均用 cube μ/σ 打 z 再 predict。

**watching_matrix**：`QUANTLAB_BATCH_SCORE` 开启时 `score_one(..., defer_cs_predict=True)` 只抽特征，池齐后再 `rescore_fitted_cross_section_batched` 写 ŷ（避免单票错误 ŷ 再重算）。

---

## 6. 回测读仓

`load_portfolio_stock_bars`：

1. 候选去掉空白。调用方若给出 `max_names` 且 `offline_ok=True`，先只读全池缓存，优先留下根数够的票，再截断。避免 `watching[:N]` 丢掉后面已有缓存的名字。
2. `min_bars` 默认 `max(16, min(40, lookback // 2))`。短于这个数的序列丢弃，避免一只票把共同交易日交集压垮。
3. 拉取长度 = `lookback`（默认 120）+ 垫窗。垫窗至少 35，并且不小于 `ALPHA_OO_PANEL_WINDOW`（62）。
4. 本地用 `load_daily_cache_many`，`ignore_age=True`，只保留最后 `limit` 根。
5. 根数 < `min_bars` 的票才 `get_bars_batch(..., offline_ok=False, timeout=25)`。
6. 股票数 ≥ 40 时，基本面 `live=False`，只读本地财务快照。阈值不随观察池上限上调。

因此：一条 1000 根、末根停在十天前的序列，回测会当命中。要末根对齐，先跑日线更新。

---

## 7. 链路核对

下面这些是对得上的。

- topup 用末根日期和条数分流；`cache_max_age_hours=0` 不会被 24 小时 TTL 提前返回。
- 进程池超时留在 `AkPool.map`，研究路径拒绝 quote fallback。
- 复权策略和缓存不一致时跳过该仓。
- 日线按日期合并，分钟按交易日整段替换。这两条规则是分开的。
- 回测只在根数不够时补远端，和「不顺便刷新」一致。
- 落盘上限 1200 ≥ 拉取条数 1062，齐窗不会被裁成短仓后再整窗死循环。前提是 meta 里的 `bar_count` 读得到。

---

## 8. 已落地的契约

四处都改在原入口里。细则见 [日线契约修补规划](./bars-update-plan.md)。

- **前复权**：缺口合并时，若本地最后一天的开高低收和远端对不上（相对差超过 `1e-4`，或绝对差超过 `0.01`），再整窗拉一次并替换该复权键下的全部日线。重叠日一致仍只补尾巴。新序列明显短于本地时不删库，来源 `cache:stale`。`mode=full` 和短仓整窗同样走替换写，盖不住本地起点或长度不够 90% 则不删。40 天长缺口仍直接整窗。
- **已经拼坏的仓**：末根已齐且重叠日未变的序列不会进入 topup。可选再跑一次 `mode=full` 清旧头。2026-10-16 topup 已对 4 只触发重锚整窗替换，并写入部分 `amount`。
- **成交额**：`daily_bars.amount` 可空。旧行保持 NULL，直到该日被重新写入。
- **面板指纹**：schema 3，窗内每一根都参与哈希。下一次拟合全池 miss 一次。
- **记账**：`cache:stale` 计入失败。本批有失败不写当日标记，并清掉同会话误写的标记。

---

## 9. 不抽新抽象

更新、落盘、指纹、会话标记仍是各自的入口。回测读仓继续只看根数，不在读的时候打远端。

| 契约 | 位置 |
|---|---|
| 前复权重叠日替换 | `adapters/market/history.py` · `qfq_overlap_rebased` |
| 成交额 | `core/store_bars_sqlite.py` 的 `daily_bars`；缺额仍走 `core/bar_fields.py` · `bar_amount` |
| 面板指纹 | `core/research/panel_fingerprint.py` · `bars_fingerprint` |
| 会话标记 | `quant/research/bars_status.py` · `refresh_bars_only` |

---

## 10. 环境变量

| 变量 | 默认 | 含义 |
|---|---|---|
| `QUANTLAB_BARS_BACKEND` | `sqlite` | `json` 回退文件仓 |
| `QUANTLAB_STORE_DIR` | `data/store` | 缓存根；其下 `bars.db` |
| `QUANTLAB_DISABLE_CACHE` | 关 | `1` 时日线不读不写缓存 |
| `QUANTLAB_FEATURE_LAKE` | 开 | `0` 关闭 L1 Feature Lake |
| `QUANTLAB_FEATURE_LAKE_DIR` | `paths.FEATURE_LAKE_DIR` | L1 根目录 |
| `QUANTLAB_DAY_CUBE` | 开 | `0` 关闭 L2 DayCube 落盘 |
| `QUANTLAB_DAY_CUBE_DIR` | `paths.DAY_CUBE_DIR` | L2 根目录 |
| `QUANTLAB_BATCH_SCORE` | 开 | `0` 调仓/回测仍走旧双遍 rescore（不建 cube） |
| `QUANTLAB_MINUTE_FETCH_DELAY_SEC` | `20` | 新浪分钟远端拉取后间隔，上限 30 |
| `QUANTLAB_MINUTE_WARMUP_SKIP_IF_READY` | `1` | 已 Ready 则不打远端 |
| `QUANTLAB_MINUTE_WARMUP_READY_MIN_SPAN_DAYS` | `40` | Ready 要求的连续交易日 |
| `QUANTLAB_MINUTE_WARMUP_STALE_HOURS` | `24` | Ready 的 `fetched_at` 上限 |
| `QUANTLAB_MINUTE_LOOKBACK_DAYS` | `45` | 规划/状态用回看日历日 |
| `QUANTLAB_MINUTE_ISOLATED_TIMEOUT_SEC` | `45` | 分钟强更单票子进程超时；`0` 关闭隔离 |

TTL 与窗口常量在 `core/data/policy.py`，不要在调用点再写一套魔法数字。

---

## 11. 代码索引

| 能力 | 路径 |
|---|---|
| 日线状态与仅刷新 | `quant/research/bars_status.py` |
| 会话日标记 | `quant/research/bars_daily.py` |
| 分钟状态与预热 | `quant/research/minute_status.py` |
| 调度预热 | `core/schedule_jobs.py` · `run_bars_warmup` / `run_minute_warmup` |
| 研究/生产读口 | `core/data/service.py` |
| 日线拉取、增量计划、字段归一 | `adapters/market/history.py` |
| 分钟拉取 | `adapters/market/minute_history.py` |
| 进程池 | `adapters/market/ak_worker.py` · `adapters/bind.py` |
| TTL / 保留条数 | `core/data/policy.py` |
| 缓存读写与合并 | `core/store.py` |
| SQLite 后端 | `core/store_bars_sqlite.py` |
| 成交额近似 | `core/bar_fields.py` |
| 期望末根日期 | `core/market/calendar.py` |
| 回测读仓 | `core/research/portfolio_bars.py` |
| 面板指纹 | `core/research/panel_fingerprint.py` |
| 因子矩阵 | `core/research/panel_matrix.py` |
| 开盘日打分缓存 | `core/backtest/replay_score_cache.py` |
| 观察池上限 | `core/watching/policy.py` |
