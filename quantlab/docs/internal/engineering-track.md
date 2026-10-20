## 工程结构轨（A0–A4 · B0–B4）

[← 文档索引](../README.md) · 产品边界见 [design-spine.md](../design-spine.md) · 存储选型见 [data.md · 数据层](../component/data.md#数据层data-layer) · Bars 迁库细则见 [sqlite-migration.md](sqlite-migration.md) · 债务台账见 [framework-review.md](framework-review.md)

**规划日期**：2026-08-21（A 轨）· 2026-10-16（B 轨）  
**定位**：产品能力轨（P/Q/R/V、DC/FM/RK）已收口后的 **工程结构**。A0–A4 · **B0–B4 均已落地**。不扩 Tick / 数仓 / SPA / OMS。

## 一句话

```text
A 轨：契约冻结 → Bars SQLite → Job 运行时硬化 → 领域门面对称 → 前端稳态
B 轨：契约冻结 → 拆 paper_replay + 去层倒置 → yhat 契约解环 → 持久化/prior 归位 → t0/root 收尾
```

## 锁定

| 做 | 不做 |
|----|------|
| 日线/分钟线 SQLite WAL（`QUANTLAB_BARS_BACKEND`） | 微服务 / Redis / Celery / Postgres |
| Job stale/cancel 统一；长任务可评估进程隔离 | 把 paper/watching/config 全部迁库 |
| BacktestService 信封；按用例拆巨石 | 全站 React / Vite / Ant Design Pro |
| paper/quant 编排继续下沉岛 | NN→生产 ŷ；OMS（N6 另立） |

## 环境变量

| 变量 | 含义 | 默认 |
|------|------|------|
| `QUANTLAB_BARS_BACKEND` | `sqlite` \| `json`；日线/分钟线缓存后端 | `sqlite`（无库或失败时可切 `json`） |
| `QUANTLAB_STORE_DIR` | 缓存根目录（其下 `bars.db` 或 `daily/`） | `data/store` |

## 验收命令（本轨）

```bash
cd quantlab
python3 -m unittest tests.test_store tests.test_a2_job_runtime tests.test_a3_backtest_service -v
python3 scripts/migrate_bars_to_sqlite.py --dry-run
```


## 巨石行数预算（维护用）

| 阈值 | 含义 |
|------|------|
| 根编排（`paper.js` / `quant.js`） | 目标趋势不增；新逻辑进子模块 |
| 领域引擎（`topk_probe` / `legacy/topk_backtest` / `factor_ols`） | 按用例切；单文件避免继续堆过 2.5k（~~`factor_ols_clusters`~~ 已退役） |
| `domain_*` | 仅改该域时再切；不为拆而拆 |

## 阶段状态

| 阶段 | 主题 | 状态 |
|------|------|------|
| **A0** | 契约冻结 | 本文 + architecture / data-layer / framework-review 指针 |
| **A1** | Bars SQLite | **已落地** |
| **A2** | Job 运行时 | **已落地**：chat 亦落盘；`stale_policy`/`persisted` 进 Job 快照；`reclaim_all_stale`；隔离评估见下节 |
| **A3** | BacktestService + 巨石 | **已落地**：`core/backtest/service.py` · `topk_weights` · `cluster_report_util`；QuantReplay 经 `backtest_service.run_topk` |
| **A4** | 前端稳态 | **已落地**：`paper/job_poll` · `paper/north_star_ui` · `dashboard_api`；quant.js 根文件禁堆域注释 |

Canonical Job API：`GET /api/jobs/{name}` · `POST /api/jobs/{name}/cancel`（`force=true` 立即释放槽）。  
读口：Domain Facade — DS · SS · BS（`core/*_service`；文档见 [§ Service 命名约定](#service-命名约定)）· CostPort。

## A2 · 进程隔离评估（结论）

| 任务 | 现行 | 是否再上 ProcessPool | 理由 |
|------|------|----------------------|------|
| AkShare 拉数 | `adapters/market/ak_worker.py` ProcessPool | 已隔离 | 崩溃不拖死 Web |
| paper / chat / ols 进度 | `data/jobs/*.json` 落盘 | — | reload 后可解释中断 |
| ŷ_τ* 建面板 | `tc_ridge_compact.build_tau_panels_from_bars` ProcessPool | **已上** | 分钟路径 CPU 重；`label_heads` 缩窗价；`TAU_RIDGE_WORKERS` 顶 4；Job `progress_cb` |
| 分组 OLS fit | Job 槽内 ThreadPool | **暂不上** ProcessPool | 矩阵拼装仍偏线程；已有 Job + cancel + stale reclaim；HTTP 经 `progress=1` 轻量轮询 |

后续若 OLS 仍饿死 API：优先「子进程整段 run_ols_clusters」单入口，而不是拆散 fit 函数。

---

## B 轨 · core 结构收口（B0–B4）

**定位**：工程债偿还（巨石、层倒置、signal↔research 环、持久化多真源）。不改对外 HTTP/JSON 契约，不改调仓/打分/prior 默认语义。

### B 轨锁定

| 做 | 不做 |
|----|------|
| 按用例拆巨石；公共 API 薄 re-export 保兼容 | 为拆而拆、改对外 HTTP/JSON 契约 |
| Protocol 注入替掉 core→services | 微服务 / Redis / Celery |
| `core/yhat` 只承载「模型读口 + 特征键 + 指纹」 | 重写 ridge/panel 算法 |
| `persistence` 门面统一路径与 atomic IO | 把 paper/watching/config 全体迁库 |
| prior 归 `market/priors` | 改 prior 产品语义 / 默认开关 |

**公共 API 冻结（B1 全程不得改签名）**：`backtest_paper_replay`、`load_replay_minute_bars`、`clamp_replay_*`、`replay_fill_px` / `replay_sell_fill_px`、`day_stock_legs`、`accumulate_day_contrib` / `finalize_stock_contrib`、`rescore_replay_y_τc_at_clock`。`core/backtest/paper_replay.py` 保留为 re-export 门面。已删：`scores_from_topk_precomputed`（无产品调用）、`REPLAY_LOT_*`（`REPLAY_AMOUNT_*` 别名）。

### B 轨阶段状态

| 阶段 | 主题 | 状态 |
|------|------|------|
| **B0** | 所有权矩阵 + 验收门 + 文档挂钩 | **已落地** |
| **B1** | `paper_replay` 按用例拆 + 去 adapters/services 硬依赖 | **已落地**：`replay_*.py` · `PaperCyclePort` · ports 分钟读 |
| **B2** | `core/yhat` 契约包 + priors 归包 | **已落地**：`core/yhat/` · `core/market/priors/` |
| **B3** | `persistence` 面 + watching 职责拆 | **已落地**：`core/persistence/` · `watching/{policy,repository,sources,sync_paper}` |
| **B4** | t0 公共分钟语义 + root 归档 + legacy 门禁 | **已落地**：`core/intraday/` · `core/platform/` · TopK `full_engine` 门禁 |

### B0 · 所有权矩阵（禁止第三套加载路径）

| 模块 | 所有权 | 禁止 |
|------|--------|------|
| `core/research/portfolio_bars.py` | 宇宙日线/基本面加载（经 DS） | quant/web 旁路拉 bars |
| `core/backtest/universe_filters.py` | 纯过滤 | 掺 I/O |
| `core/backtest/replay_score_cache.py` | replay 私有缓存 | 被 live 打分路径依赖 |
| `core/backtest/paper_replay.py`（+ `replay_*.py`） | 产品回测主路径 | 再堆新用例进同文件 |

### B 轨巨石预算

领域引擎单文件目标 **≤1500 行**；超标须有拆分 issue，不得继续净增。B1 后 `paper_replay.py` ≤1200（已达）；`replay_*.py` 目标 ≤700（scoring / score_cache 略厚，禁止继续净增）。

### B 轨验收命令

```bash
cd quantlab
python3 -m unittest \
  tests.test_paper_replay \
  tests.test_replay_score_cache \
  tests.test_last_portfolio_backtest \
  tests.test_universe_filters \
  tests.test_store \
  tests.test_framework_hardening -v
```

每阶段结束必跑上列命令；B1/B4 另做固定种子回测净值点位对照。

### B1 · 目标文件（扁平 `core/backtest/replay_*.py`）

| 文件 | 职责 |
|------|------|
| `replay_fill.py` | 成交钟 / 价 / price space |
| `replay_minute.py` | 分钟加载（经 ports/DS，无 adapters 硬依赖） |
| `replay_calendar.py` | 交易日日历 |
| `replay_contrib.py` | 日腿与贡献归因 |
| `replay_scoring.py` | 开盘打分 / 一日分数包三态（`refresh_tau_heads_after_prefix` · `maybe_backfill_open_tc_rank`） |
| `replay_sim_rows.py` | 模拟行与 fuse hit |
| `paper_replay.py` | 主循环 + re-export（`pyproject` 对该文件忽略 F401） |

### B2–B4 摘要

- **B2**：`core/yhat/{keys,model_load,fingerprint}.py`；`core/market/priors/`；守卫测禁 signal↔research 顶层互引。
- **B3**：`core/persistence/{bars,json_doc,result_snapshot}.py`；`watching/{repository,sources,sync_paper,policy}.py`。
- **B4**：`core/intraday/` 或 `backtest/intraday_common.py` 供 t0；`core/platform/` 归档 jobs/manifest；legacy TopK 仅 `full_engine=True`。

---

