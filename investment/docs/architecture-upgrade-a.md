# 工程结构轨（A0–A4）

[← 文档索引](README.md) · 产品边界见 [design-spine](design-spine.md) · 存储选型见 [data-layer](data-layer.md) · Bars 迁库细则见 [sqlite-migration.md](sqlite-migration.md)

**规划日期**：2026-08-21  
**定位**：产品能力轨（P/Q/R/V、DC/FM/RK）已收口后的 **工程结构** 下一程。不扩 Tick / 数仓 / SPA / OMS。

## 一句话

```text
契约冻结 → Bars SQLite → Job 运行时硬化 → 领域门面对称 → 前端稳态
```

## 锁定

| 做 | 不做 |
|----|------|
| 日线/分钟线 SQLite WAL（`INVESTMENT_BARS_BACKEND`） | 微服务 / Redis / Celery / Postgres |
| Job stale/cancel 统一；长任务可评估进程隔离 | 把 paper/watching/config 全部迁库 |
| BacktestService 信封；按用例拆巨石 | 全站 React / Vite / Ant Design Pro |
| paper/quant 编排继续下沉岛 | NN→生产 ŷ；OMS（N6 另立） |

## 环境变量

| 变量 | 含义 | 默认 |
|------|------|------|
| `INVESTMENT_BARS_BACKEND` | `sqlite` \| `json`；日线/分钟线缓存后端 | `sqlite`（无库或失败时可切 `json`） |
| `INVESTMENT_STORE_DIR` | 缓存根目录（其下 `bars.db` 或 `daily/`） | `data/store` |

## 验收命令（本轨）

```bash
cd investment
python3 -m unittest tests.test_store tests.test_a2_job_runtime tests.test_a3_backtest_service -v
python3 scripts/migrate_bars_to_sqlite.py --dry-run
```


## 巨石行数预算（维护用）

| 阈值 | 含义 |
|------|------|
| 根编排（`paper.js` / `quant.js`） | 目标趋势不增；新逻辑进子模块 |
| 领域引擎（`topk_backtest` / `factor_ols_clusters`） | 按用例切；单文件避免继续堆过 2.5k |
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
读口：Domain Facade — DS · SS · BS（`core/*_service`；文档见 [architecture · Service 命名约定](architecture.md#service-命名约定)）· CostPort。

## A2 · 进程隔离评估（结论）

| 任务 | 现行 | 是否再上 ProcessPool | 理由 |
|------|------|----------------------|------|
| AkShare 拉数 | `skills/common/ak_worker.py` ProcessPool | 已隔离 | 崩溃不拖死 Web |
| paper / chat / ols / param-grid 进度 | `data/jobs/*.json` 落盘 | — | reload 后可解释中断 |
| 分组 OLS fit | Job 槽内 ThreadPool | **暂不上** ProcessPool | panel/矩阵难 pickle；已有 Job + cancel + stale reclaim；HTTP 经 `progress=1` 轻量轮询 |
| 参数网格 | 同左 | **暂不上** | 结果小、可落盘完整 result |

后续若 OLS 仍饿死 API：优先「子进程整段 run_ols_clusters」单入口，而不是拆散 fit 函数。

