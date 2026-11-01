## 工程结构轨（A0–A4 · B0–B4）

[← 文档索引](../README.md) · 产品边界见 [design-spine.md](../design-spine.md) · 存储选型见 [data.md · 数据层](../component/data.md#数据层data-layer) · Bars 迁库细则见 [sqlite-migration.md](sqlite-migration.md) · 债务台账见 [framework-review.md](framework-review.md)

**定位**：产品能力轨（P/Q/R/V、DC/FM/RK）已收口后的 **工程结构**。**A0–A4 · B0–B4 均已落地**，不扩 Tick / 数仓 / SPA / OMS。

## 一句话

- A 轨：契约冻结 → Bars SQLite → Job 运行时硬化 → 领域门面对称 → 前端稳态
- B 轨：契约冻结 → 拆 paper_replay + 去层倒置 → yhat 契约解环 → 持久化/prior 归位 → t0/root 收尾

## 锁定

- **做**：日线/分钟线 SQLite WAL（`QUANTLAB_BARS_BACKEND`）；Job stale/cancel 统一 + 长任务进程隔离评估；BacktestService 信封 + 按用例拆巨石；paper/quant 编排继续下沉岛。
- **不做**：微服务 / Redis / Celery / Postgres；把 paper/watching/config 全部迁库；全站 React / Vite / Ant Design Pro；NN→生产 ŷ / OMS（N6 另立）。

## 环境变量

- `QUANTLAB_BARS_BACKEND`：`sqlite` \| `json`；日线/分钟线缓存后端，默认 `sqlite`（无库或失败时可切 `json`）
- `QUANTLAB_STORE_DIR`：缓存根目录（其下 `bars.db` 或 `daily/`），默认 `data/store`

## 验收命令（本轨）

```bash
cd quantlab
python3 -m unittest tests.test_store tests.test_a2_job_runtime tests.test_a3_backtest_service -v
python3 scripts/migrate_bars_to_sqlite.py --dry-run
```

## 巨石行数预算（维护用）

- 根编排（`paper.js` / `quant.js`）趋势不增，新逻辑进子模块；`domain_*` 仅改该域时再切，不为拆而拆。
- 领域引擎（`topk_probe` / `legacy/topk_backtest` / `factor_ols`）按用例切，单文件避免堆过 2.5k。

## 阶段状态

A 轨全部已落地：**A0** 契约冻结 · **A1** Bars SQLite · **A2** Job 运行时（chat 落盘；`stale_policy` / `persisted` 进 Job 快照；`reclaim_all_stale`）· **A3** `core/backtest/service.py` · `topk_weights` · `cluster_report_util`，QuantReplay 经 `backtest_service.run_topk` · **A4** `paper/job_poll` / `paper/north_star_ui` / `dashboard_api`，quant.js 根文件禁堆域注释。

Canonical Job API：`GET /api/jobs/{name}` · `POST /api/jobs/{name}/cancel`（`force=true` 立即释放槽）。  
读口：Domain Facade — DS · SS · BS（`core/*_service`；文档见 [§ Service 命名约定](../architecture.md#service-命名约定)）· CostPort。

## A2 · 进程隔离评估（结论）

- AkShare 拉数：`adapters/market/ak_worker.py` ProcessPool，**已隔离**（崩溃不拖死 Web）；paper / chat / ols 进度落 `data/jobs/*.json`，reload 后可解释中断。
- ŷ_τ* 建面板：L1-first（`hz_*` / stitch）→ 个别票 5m ProcessPool，**已上**；产品 cold/低命中 fail-closed，`skip_l1_stitch` 免二次读湖，`TAU_RIDGE_WORKERS` 顶 4。

## B 轨 · core 结构收口（B0–B4）

**定位**：工程债偿还（巨石、层倒置、signal↔research 环、持久化多真源）。不改对外 HTTP/JSON 契约，不改调仓/打分/prior 默认语义。

### B 轨锁定

- **做**：按用例拆巨石 + 公共 API 薄 re-export 保兼容；Protocol 注入替掉 core→services；`core/yhat` 只承载「模型读口 + 特征键 + 指纹」；`persistence` 门面统一路径与 atomic IO；prior 归 `market/priors`。
- **不做**：为拆而拆 / 改对外 HTTP/JSON 契约；微服务 / Redis / Celery；重写 ridge/panel 算法；把 paper/watching/config 全体迁库；改 prior 产品语义 / 默认开关。

**公共 API 冻结（B1 全程不得改签名）**：`backtest_paper_replay`、`load_replay_minute_bars`、`clamp_replay_*`、`replay_fill_px` / `replay_sell_fill_px`、`day_stock_legs`、`accumulate_day_contrib` / `finalize_stock_contrib`、`rescore_replay_y_τc_at_clock`；`core/backtest/paper_replay.py` 保留为 re-export 门面。

### B 轨阶段状态

**B0** 所有权矩阵 + 验收门 · **B1** `replay_*.py` + `PaperCyclePort` + ports 分钟读 · **B2** `core/yhat/` · `core/market/priors/` · **B3** `core/persistence/` · `watching/{policy,repository,sources,sync_paper}` · **B4** `core/t0/intraday.py` · `core/platform/` · TopK `full_engine` 门禁（全部已落地）。

### B0 · 所有权矩阵（禁止第三套加载路径）

- `core/research/portfolio_bars.py`：宇宙日线/基本面加载（经 DS）；禁止 quant/web 旁路拉 bars。
- `core/backtest/universe_filters.py`：纯过滤，禁止掺 I/O；`core/backtest/replay_score_cache.py`：replay 私有缓存，禁止被 live 打分路径依赖。
- `core/backtest/paper_replay.py`（+ `replay_*.py`）：产品回测主路径，禁止再堆新用例进同文件。

### B 轨巨石预算

领域引擎单文件目标 **≤1500 行**，超标须有拆分 issue 且不得继续净增。`paper_replay.py` ≤1200（已达）；`replay_*.py` 目标 ≤700（scoring / score_cache 略厚，禁止净增）。

### B 轨验收命令

```bash
cd quantlab
python3 -m unittest tests.test_paper_replay tests.test_replay_score_cache tests.test_last_portfolio_backtest tests.test_universe_filters tests.test_store tests.test_framework_hardening -v
```

每阶段结束必跑；B1/B4 另做固定种子回测净值点位对照（保留在验收清单内）。

### B1 · 目标文件（扁平 `core/backtest/replay_*.py`）

- `replay_fill.py` 成交钟/价/price space · `replay_minute.py` 分钟加载（经 ports/DS） · `replay_calendar.py` 交易日历
- `replay_contrib.py` 日腿与贡献归因 · `replay_scoring.py` 开盘打分/一日分数包三态 · `replay_sim_rows.py` 模拟行与 fuse hit
- `paper_replay.py` 主循环 + re-export（`pyproject` 对该文件忽略 F401）

### B2–B4 摘要

- **B2**：`core/yhat/{keys,model_load,fingerprint}.py`；`core/market/priors/`；守卫测禁 signal↔research 顶层互引。
- **B3**：`core/persistence/{bars,json_doc,result_snapshot}.py`；`watching/{repository,sources,sync_paper,policy}.py`。
- **B4**：`core/t0/intraday.py` 供 t0；`core/platform/` 归档 jobs/manifest；legacy TopK 仅 `full_engine=True`。
