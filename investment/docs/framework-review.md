# 代码框架梳理与合理性分析

[← 文档索引](README.md) · 工程分层见 [architecture.md](architecture.md) · 数据口见 [data-layer.md](data-layer.md) · 风控见 [risk-layer.md](risk-layer.md) · 路线图见 [roadmap.md](roadmap.md)

本文是对 `investment/` **现行代码框架**的梳理与债务分级（复核 **2026-08-05**；O1–O9 / Y-S / **框架深审 C1–C3·H1–H6** **已落地**）。  
写法约定：**「已收口」** = 主路径已落地、可验收；**「设计保留」** = 有意双轨，不算必须还债。

---

## 1. 系统定位

本地 **量化研究 + 纸面模拟 + AI 编排**，不做实盘代客下单。产品主路径：**观察 → 模拟 → 回溯**（观察 ≠ 模拟）。

| 轴 | 内容 |
|----|------|
| 因果主轴 | 已发生事实 → 影响估计 → 验证 → 动作（[design-spine](design-spine.md)） |
| 工程主轴 | 数据 → 信号 → 因子 → 倾向 → 动作 |
| AI 边界 | 编排与解释；**不得**改写 `score` / `stance_label` |
| 路径终点 | **策略验证**（研究台 + 纸面）；OMS = N6，验证成熟后另立项 |

与路线图对齐：Q1–Q5 / P0–P2++ 主干已验收；本文只管**工程债**，不重复北极星产品缺口（见 [roadmap](roadmap.md)）。

---

## 2. 顶层目录

| 目录 | 职责 | 备注 |
|------|------|------|
| `core/` | 领域层：信号/回测/纸面/风控/DataService/ports/定时（无 LLM/HTTP） | canonical 真相源 |
| `services/` | Web/CLI 应用服务（纸面拆 account/jobs/trades） | — |
| `quant/` | 研究台 `QuantService` + `quant/research` + Agent `quant/skill` | — |
| `web/` | FastAPI + 静态 UI（`/watching` `/follow` `/replay`） | `js/paper/*` 子模块 |
| `agent/` | LLM 编排、registry、prompts | 正本（原 `advisor/` 已删） |
| `skills/` | Agent 工具 + `common/`（ports 实现）+ `ports_bind` | 消费侧经 DataService/ports |
| `data/` | JSON 状态、日线缓存、`jobs/` | — |
| `research/` | 薄 CLI（读数经 DataService） | — |
| `evals/` | 黄金用例 + repro | — |

入口：`run_web.py` · `main.py` · `core/schedule_jobs.py`。目录树见 [structure.md](structure.md)。

**命名（设计保留）**

| 对外 / URL | 对内 canonical |
|------------|----------------|
| 「模拟」· `/follow` | `paper` · `/api/paper*` · `paper.json` |
| Agent | `agent/` |
| 量化 Skill | `quant/skill` + `skills/quant`（tool_config） |

---

## 3. 分层与主调用链

```text
接入 (web/main) → 服务 (services · quant/services)
  → 编排 (agent) → 适配 (skills/handler)
  → 领域 (core) → DataService → ports → skills.ports_bind → skills.common / engines
```

Web 主路径：观察建仓 → 确认调仓（`run_daily_cycle`）→ 轮询 **`GET /api/jobs/paper`**（兼容 `/api/paper/job`）→ 回溯 QuantService。

---

## 4. 做得对的地方

1. 领域与 HTTP/LLM 分离  
2. 产品边界清晰（观察 ≠ 模拟）  
3. DataService + ports；Skill/研究/业务读口已对齐  
4. 策略 / 风控 / 五问可验收  
5. Job 抗 reload（`WEB_RELOAD` 默认关；paper 槽落盘）  
6. 纸面体量拆分：`paper_exec` / `paper_cycle` / services mixins / `js/paper/*`
7. 未接线死模块已删：`core/signal/factors/optimizer.py` · `volatility_position.py`

---

## 5. 债务台账

### 5.1 已收口（含本轮 O1–O9）

| ID | 原问题 | 落地证据 |
|----|--------|----------|
| **H1** | 业务读口绕过 DataService | 纸面/量化/调度经 DataService |
| **H2** | `market` 硬 import skills | `adapters` + `ports_bind` |
| **H3** | reload 清空 Job | `WEB_RELOAD=0`；`data/jobs/paper.json` |
| **H4** | 调仓堆在 `paper.py` | `paper_cycle` + `paper_exec`（账本 IO ~250 行） |
| **H5** | 风控几乎没有 | `core/risk/checks.py` 调仓前硬拦 |
| **O1** | Skill 直调 `skills.common` | quote/kline/signal/compare/… → DataService/ports；回测结果带 `quality` |
| **O2** | core 硬依赖 skills | 仅 `ports/adapters.py` lazy bind；signal/spot 经 adapter |
| **O3** | 双 Job HTTP | UI → `/api/jobs/paper`；`/api/paper/job` 兼容保留 |
| **O4** | research 半收口 | CLI 无 `skills.common`；`get_quote` / DataService |
| **O5** | `paper.py` 过大 | 成交盯市 → `paper_exec.py` |
| **O6** | `paper_service` 偏厚 | `paper_account` / `paper_jobs` / `paper_trades` / `paper_helpers` |
| **O7** | `paper.js` 巨石 | `js/paper/fmt.js` · `chart.js`；编排仍 `paper.js` |
| **O8** | `structure.md` 滞后 | 已按现行 `core/` / services / js 刷新 |
| **O9** | services→skills 穿透 | `watching_service.search` → ports；insights 经 `load_disk_spot` |
| **Y-S1** | 日更 `min_score=55` 当 ŷ 门槛 → 目标簿近空 | `resolve_optimize_score_floor`；`paper_cycle` 传 `None`；≥10 视为遗留 0–100 |
| **Y-S2** | StrategySpec/`promote` 写 0–100 选股门 | `paper_rules` 仅限额；`apply_strategy_to_paper` / promote 剥离 legacy keys |
| **Y-S3** | feedback 建议改 `rank.min_score` | 改建议 `scoring.min_predicted_score`；不写 weights |
| **Y-S4** | services/core 直调 minute/财务 skills | `ports.fetch_minute_bars` · `fetch_cn_financial_series` + ports_bind |
| **F-C1** | core→quant.research 分层倒置 | `core/research/*`；quant 再导出 |
| **F-C2** | ŷ/0–100 渗入 neutralize·回测 UI·stance | neutralize 保 ŷ；JS `min_predicted_score`；stance 无静默回退 |
| **F-C3** | 三条调仓语义 | `paper_rebalance_orchestrator` + preset 别名 |
| **F-H1/H2** | web/quant 绕过 ports | watching→DataService；factors/t0→ports；stance 门面 |
| **F-H3** | 多 JSON 半晋升 | `live_config_manifest` 指纹 + alerts |
| **F-H5/H6** | weight_suggest / Job 多入口 | 日报跳过 IC suggest；`/api/jobs/{name}` canonical |
| **F-M1/M2** | 守卫不全 · 双 config GET | quant/services+core→quant 守卫；signal config 标 canonical |
| **F-B1** | north_star 巨石 | `risk_metrics` · `ttm_events` · `backtest_curve_store`；north_star 再导出 |
| **F-B2** | cluster_live 证据/状态 | `cluster_live_evidence.py` |
| **F-B3** | factor_ols_clusters 巨石 | `cluster_partition` · `cluster_weight_display` |
| **F-B4** | quant.js 门槛逻辑 | `web/static/js/quant/scoring.js`（`quant_scoring.js` 再导出） |
| **F-B5** | quant.js 功能域迁出 | `quant/` 工厂 + **6 域** `domain_*`；watching helpers→`watching_*_ui`·`watching_panel_ui`；分组结果按需展开因子表（避免主线程卡在「分组中…」）；建议 tip→`suggest_status_ui`；已修回测 `min_score:55`→`portfolioBtScoreFloorPayload`；`ASSET_V=p670` |
| **FH0** | mode 未硬门禁组 ŷ | `score_stock` / `cross_section` / live topk：仅 `active` 写主分；`shadow` 对照；`off` 不算组 ŷ；`max_oos_fail_rate` 默认 0.5；契约测 `test_cluster_mode_yhat_gate` |
| **FH1** | 晋升非原子 / 半晋升只告警 | `cluster_pointer.json` + 版本化 artifact + `os.replace`；active 硬门禁（缺簿/坏指针）；`force`→`promote_audit.jsonl`；`test_cluster_pointer_fh1` |
| **FH2** | 分组同步占 worker | 默认 Job `quant-ols-clusters`；UI 轮询；`sync=true` 兼容；`POST /api/jobs/{name}/cancel`；`test_fh2_ols_clusters_job` |
| **FH3** | core→services · legacy 55 | `core/signal/score_view.py`；守卫禁 services；`rank.min_score` deprecated |
| **FH4** | 静默 except | `score_stock.warnings`（舆情）；promote/mode manifest 失败进 warnings；`test_fh4_score_warnings` |
| **FH5** | 分组默认非 PIT | 默认 `pit_fundamentals=true`；`lookahead_flags`；UI PIT/非 PIT 旗标；**深化** `cluster_panels` 末日 as_of 探针 + `pit_as_of` 诚实旗标 |
| **FH4+** | 巨石再切 | `cluster_live_audit` · `cluster_panels` 按用例拆出（非为行数） |

### 5.2 设计保留（勿当缺陷乱拆）

| 项 | 说明 |
|----|------|
| **`paper` 唯一账本** | 模拟真相源；对照仓 `portfolio.json` **已下线**；position 默认读 paper |
| **`paper` / `follow` /「模拟」** | 对外模拟 + 对内 paper |
| **非 paper Job 纯内存** | evals/schedule 短任务可接受 |

### 5.3 明确不做

- 拆 AI 旁路、重写 paper 引擎、券商 OMS（N6）  
- 全市场 Tick / 完整财务 PIT 数仓  
- 在线 RL 自动改权  

---

## 6. 结论

| 维度 | 评价 |
|------|------|
| 整体 | 研究台 + 纸面准实盘分层已理顺 |
| 框架急债 | **O1–O9 / Y-S / F-C1–C3·H\* 已落地**；维持 ports 与 ŷ 单标尺 + live manifest 纪律 |
| **下一程（机制化）** | **[framework-harden-plan.md](framework-harden-plan.md)**：**FH0–FH5 已落地**；后续按需持续切巨石（FH4）与财务面板真 PIT 注入（FH5 深化） |
| 产品缺口 | 仍见 roadmap / design-spine / yhat-strengthen（非本表） |
| 体量债 | `quant.js` 已拆域 + 工厂；`factor_ols_clusters` / `cluster_live` / `north_star` 已拆；巨石按用例再切归 FH4 |

单测锚点：`tests/test_framework_hardening.py`（含 core 无硬 skills import、Skill→DataService）· `tests/test_m1_data_collection.py` · `tests/test_d1_d6_platform.py`。

---

## 7. 快速对照：文档 ↔ 代码

| 概念 | 落点 |
|------|------|
| 唯一读口 | `core/data_service.py` |
| 端口 | `core/ports/{market,adapters,signal}.py` |
| 绑定 | `skills/ports_bind.py` |
| 账本 / 成交 / 日循环 | `paper.py` · `paper_exec.py` · `paper_cycle.py` · `paper_rebalance_orchestrator.py` |
| Live 清单 | `core/live_config_manifest.py` · `data/live/live_config_manifest.json` |
| 研究共享（core） | `core/research/{factor_ols_fit,panel,portfolio_bars,oos_slim}.py` |
| 风控门禁 | `core/risk/checks.py` |
| Job 槽 | `core/job_progress.py` · **`GET /api/jobs/{name}`**（canonical） |
| 模拟 UI | `web/static/js/paper.js` + `paper/fmt.js` · `chart.js` |
