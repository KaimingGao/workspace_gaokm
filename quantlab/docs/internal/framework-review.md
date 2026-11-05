## 代码框架梳理与合理性分析

[← 文档索引](../README.md) · 工程分层见 [架构总览](../architecture.md#架构总览) · 数据口见 [data.md · 数据层](../component/data.md#数据层data-layer) · 风控见 [risk.md · 风控层](../component/risk.md#风控模型risk-layer) · 路线图见 [design-spine.md · 路线图](../design-spine.md#能力评估与升级规划路线图视角)

本文是 `quantlab/` **现行代码框架**的梳理与债务分级（O1–O9 / Y-S / **框架深审 C1–C3·H1–H6** **已落地**）。写法约定：**「已收口」** = 主路径已落地、可验收；**「设计保留」** = 有意双轨，不算必须还债。

## 1. 系统定位

本地 **量化研究 + 纸面模拟 + AI 编排**，不做实盘代客下单；产品主路径：**观察 → 模拟 → 回溯**（观察 ≠ 模拟）。

- **因果主轴**：已发生事实 → 影响估计 → 验证 → 动作；**工程主轴**：数据 → 信号 → 因子 → 倾向 → 动作
- **AI 边界**：编排与解释，**不得**改写 `score` / `stance_label`；**路径终点**：策略验证（研究台 + 纸面），OMS = N6 另立项
- 与路线图对齐：Q1–Q5 / P0–P2++ 主干已验收；本文只管**工程债**（[roadmap](../design-spine.md#能力评估与升级规划路线图视角)）

## 2. 顶层目录

| 目录 | 职责 |
|------|------|
| `core/` | 领域层：信号/回测/纸面/风控/DataService/ports/定时（无 LLM/HTTP），canonical 真相源 |
| `services/` · `quant/` | Web/CLI 与研究台 **Application Service**（纸面拆 account/jobs/trades；`QuantService` + `quant/research` + `quant/skill`） |
| `web/` · `agent/` | FastAPI + 静态 UI（`js/paper/*` 子模块）；LLM 编排、registry、prompts |
| `skills/` · `adapters/` | Agent 工具（handler + shim）；出站行情/资讯/基本面 I/O，`bind.py` 注入 `core.ports` |
| `data/` · `research/` · `evals/` | JSON 状态与日线缓存；薄 CLI（读数经 DataService）；黄金用例 + repro |

入口：`run_web.py` · `main.py` · `core/platform/schedule_jobs.py`（根路径 shim）；目录树见 [architecture.md · 目录结构](../architecture.md#代码目录结构)。
**命名**：用例组装 = **Application Service**（`services/*` · `quant/services/QuantService`）；领域出口 = **Domain Facade**（DS · SS · BS：`core/data/facade` · `signal_service` · `backtest_service`）。对外「模拟」/ `/follow` ↔ 对内 `paper` / `paper.json`；量化 Skill ↔ `quant/skill` + `skills/quant`。

## 3. 分层与主调用链

```text
接入 (web/main) → Application Service (services · quant/services)
  → 编排 (agent) → 适配 (skills/handler)
  → Domain Facade (DS/SS/BS) → core → ports → adapters.bind
```

Web 主路径：观察建仓 → 确认调仓（`run_daily_cycle`）→ 轮询 **`GET /api/jobs/paper`**（兼容 `/api/paper/job`）→ 回溯 QuantService。

## 4. 做得对的地方

1. 领域与 HTTP/LLM 分离；产品边界清晰（观察 ≠ 模拟）
2. DataService + ports；Skill/研究/业务读口已对齐；策略/风控/五问可验收
3. Job 抗 reload（`WEB_RELOAD` 默认关；paper 槽落盘）
4. 纸面体量拆分：`core/paper/exec.py` / `paper_cycle` / services mixins / `js/paper/*`

## 5. 债务台账

### 5.1 已收口（含本轮 O1–O9）

| ID | 原问题 | 落地证据 |
|----|--------|----------|
| **H1** | 业务读口绕过 DataService | 纸面/量化/调度经 DataService |
| **H2** | `market` 硬 import skills | `registry` + `adapters.bind` |
| **H3** | reload 清空 Job | `WEB_RELOAD=0`；`data/jobs/paper.json` |
| **H4** | 调仓堆在 `paper.py` | `paper_cycle` + `core/paper/exec.py`（账本 IO ~250 行） |
| **H5** | 风控几乎没有 | `core/risk/checks.py` 调仓前硬拦 |
| **O1** | Skill 直调市场 I/O | quote/kline/signal/compare/… → DataService/ports；回测结果带 `quality` |
| **O2** | core 硬依赖 skills | 仅 `ports/registry.py` lazy bind `adapters.bind`；signal/spot 经 adapter |
| **O3** | 双 Job HTTP | UI → `/api/jobs/paper`；`/api/paper/job` 兼容保留 |
| **O4** | research 半收口 | CLI 无直连 adapters；`get_quote` / DataService |
| **O5** | `paper.py` 过大 | 成交盯市 → `core/paper/exec.py` |
| **O6** | `paper_service` 偏厚 | `paper_account` / `paper_jobs` / `paper_trades` / `paper_helpers` |
| **O7** | `paper.js` 巨石 | `js/paper/fmt.js` · `chart.js`；编排仍 `paper.js` |
| **O8** | `architecture.md#代码目录结构` 滞后 | 已按现行 `core/` / services / js 刷新 |
| **O9** | services→skills 穿透 | `watching_service.search` → ports；insights 经 `load_disk_spot` |
| **Y-S1** | 日更 `min_score=55` 当 ŷ 门槛 → 目标簿近空 | `resolve_optimize_score_floor`；`paper_cycle` 传 `None`；≥10 视为遗留 0–100 |
| **Y-S2 / Y-S3** | StrategySpec/`promote`/feedback 写 0–100 选股门 | `paper_rules` 仅限额，promote 剥离 legacy keys；建议改 `scoring.min_predicted_score`，不写 weights |
| **Y-S4** | services/core 直调 minute/财务 skills | `ports.fetch_minute_bars` · `fetch_cn_financial_series` + ports_bind |
| **Y-S5** | `cycle._resolve_strategy_spec` 失败路径把 `params`（含研究 `min_score`）打进 `paper.rules` | **已落地**：仅合并 `get_strategy_spec` 的 `paper_rules`/risk 安全子集（C3） |
| **R-S1** | 风控限额兜底分叉：`checks` `max_positions or 5`、cycle `or 5`、book `40`、spec 填空 `20` | **已落地**：`core/risk/limits.py` + `resolve_account_risk_limits`（C1）；C1.x 收 kelly 单位 / 末级 DEFAULT / Spec warning |
| **P-V1** | `paper.json` 无结构校验；任意函数可写 `rules.min_score` 等毒字段 | **已落地**：`ledger.validate_paper` load/save strip（C2） |
| **F-C1** | core→quant.research 分层倒置 | `core/research/*`；quant 再导出 |
| **F-C2** | ŷ/0–100 渗入 neutralize·回测 UI·stance | neutralize 保 ŷ；JS `min_predicted_score`；stance 无静默回退 |
| **F-C3** | 三条调仓语义 | `core/paper/rebalance/orchestrator` + preset 别名 |
| **F-H1/H2** | web/quant 绕过 ports | watching→DataService；factors/t0→ports；stance 门面 |
| **F-H3** | 多 JSON 半晋升 | `live_config_manifest` 指纹 + alerts |
| **F-H5/H6** | weight_suggest / Job 多入口 | 日报跳过 IC suggest；`/api/jobs/{name}` canonical |
| **F-M1/M2** | 守卫不全 · 双 config GET | quant/services+core→quant 守卫；signal config 标 canonical |
| **F-B1** | north_star 巨石 | `risk_metrics` · `ttm_events` · `backtest_curve_store`；north_star 再导出 |
| **F-B4 / F-B5** | quant.js 门槛逻辑 / 功能域迁出 | `quant/scoring.js`；`quant/` 工厂 + **6 域** `domain_*`；`watching_*_ui`；`ASSET_V=p670` |
| **FH3** | core→services · legacy 55 | `core/signal/score_view.py`；守卫禁 services；`rank.min_score` deprecated |
| **FH4** | 静默 except | `score_stock.warnings`；promote/mode manifest 失败进 warnings；`test_fh4_score_warnings` |

### 5.2 设计保留（勿当缺陷乱拆）

- **`paper` 唯一账本**：模拟真相源；position 默认读 paper；对外「模拟」/ `follow` ↔ 对内 `paper`。
- **非 paper Job 纯内存**：evals/schedule 短任务可接受。

### 5.3 明确不做

- 拆 AI 旁路、重写 paper 引擎、券商 OMS（N6）；全市场 Tick / 完整财务 PIT 数仓；在线 RL 自动改权。

## 6. 结论

- **整体**：研究台 + 纸面准实盘分层已理顺；框架急债 **O1–O9 / Y-S / F-C1–C3·H\* 已落地**，维持 ports 与 ŷ 单标尺 + live manifest 纪律。
- **A 轨（工程结构）**：**A0–A4 已落地**——契约冻结 → Bars SQLite → Job 运行时硬化 → BacktestService + 按用例拆巨石 → 前端稳态；SS-E1～E5 / FH0–FH5 已收口。
- **工程结构 B 轨**：**B0–B4 已落地**——拆 `paper_replay` + 去层倒置 → `yhat` 契约解环 → 持久化/prior 归位 → t0/root（见 [engineering-track.md · B 轨](engineering-track.md#b-轨-core-结构收口b0b4) · [core/README.md](../../core/README.md)）。
- **状态层 C 轨**：**C0–C3 · C1.x 已落地**——风险限额 SSOT · kelly 单位 · `validate_paper` · promote/cycle 卫生（见 [engineering-track.md · C 轨](engineering-track.md#c-轨--状态层-ssotc0c3)；债项 R-S1 / P-V1 / Y-S5）。
- **产品缺口**：仍见 design-spine / quant.md ŷ 全链路（非本表）；**体量债**：`quant.js` 已拆域 + 工厂，巨石按用例再切归 **A3/A4**（非为拆而拆）。
- 单测锚点：`tests/test_framework_hardening.py`（core 无硬 skills import、Skill→DataService）· `tests/test_c_track_invariants.py` · `tests/test_m1_data_collection.py` · `tests/test_d1_d6_platform.py`。

## 7. 快速对照：文档 ↔ 代码

| 概念 | 落点 |
|------|------|
| 唯一读口 | `core/data/facade.py` |
| 端口 / 绑定 | `core/ports/{market,adapters,signal}.py` · `adapters/bind.py` |
| 账户风控限额 SSOT | `core/risk/limits.py`（`resolve_account_risk_limits` · `pct_to_fraction`） |
| 账本 / 成交 / 日循环 | `core/paper/`（ledger · exec · cycle · rebalance · `validate_paper`） |
| Live 清单 | `core/live_config_manifest.py` · `data/live/live_config_manifest.json` |
| 研究共享（core） | `core/research/{factor_ols_fit,oo_panel,tau_panel,portfolio_bars}.py`（`panel.py` 仅兼容再导出） |
| 风控门禁 | `core/risk/checks.py` |
| Job 槽 | `core/platform/job_progress.py`（根路径 shim）· **`GET /api/jobs/{name}`**（canonical） |
| 模拟 UI | `web/static/js/paper.js` + `paper/fmt.js` · `chart.js` |
