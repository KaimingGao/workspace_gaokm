# 升级重构开发规划（R0–R5 · 已收口）

[← 文档索引](README.md) · **现行下一程** → [strategy-validation-upgrade.md](strategy-validation-upgrade.md)（V0–V5） · 差距依据见下文与 [quant-ui-gap.md](quant-ui-gap.md) · 产品主轴 [design-spine.md](design-spine.md) · 历史节奏 [roadmap.md](roadmap.md) · Web 壳层 [quant-ui-upgrade.md](quant-ui-upgrade.md) · 工程债 [framework-review.md](framework-review.md)

**规划日期**：2026-07-29  
**状态**：**R0–R5 已收口**（含运营/样本包）。后续排期见 **[strategy-validation-upgrade.md](strategy-validation-upgrade.md)**。  
**基线（收口时）**：P0–P2++ 与 Web W0–W5、升级 R0–R5 已落地；能力地图粗估 **~70%～78%**；**产品定位 = 策略验证**；实盘 OMS 待验证成熟后另立项（N6）。  
**本文用途**：归档「相对专业量化栈 → R0–R5」的升级 / 重构计划；**不**替代 [design-spine](design-spine.md)，也**不**作为现行开工主文档（现行主文档 = V 轨）。

---

## 0. 一句话目标

把系统从「主轴可演示的研究–纸面闭环」升到「**北极星三项可度量、可回归** + 路径内能力缺口按柱优先加深」——终点仍是聚宽 / QuantConnect 级研究台与纸面准实盘，**不是**机构 OMS / 盯盘终端。

```text
成功画像（本规划终点）
  · 滚动纸面夏普/卡玛 · TTM · 回测–纸面 Corr/TE 有 API + 日更落盘 + Web 仪表
  · 财务 PIT 最小可用；回测/纸面读口一致且可审计
  · 参数实验 / 规格编辑缩短 Idea→回测 TTM；深度归因与冲击成本可见
  · Risk 敞口与拦截有效率可巡检；仍无人审不写盘、不接券商
```

---

## 1. 差距结论 → 规划输入

### 1.1 对照结论摘要

| 层级 | 相对专业栈 | 规划态度 |
|------|------------|----------|
| OMS / Level2 / 真强平 | 接近 0 | **现行边界外**；策略验证成熟后另立项 N6 |
| 能力地图六大模块 | ~68%～74% | **加深，不冲满分画像** |
| 产品北极星三项乘积 | 公式有；仪表部分可见、未成系统 KPI | **本规划第一优先级** |
| Web 研究台 | W0–W5 已补图表/Dock/WS；缺 IDE / 真虚拟表 / 深度 Brinson UI | **跟随后端数据，不单独炫技** |

详细对照：[design-spine · 达成度](design-spine.md#达成度评估2026-07) · [quant-ui-gap](quant-ui-gap.md) · 上一轮对话缺陷分级。

### 1.2 需求拷问（进本规划的门禁）

任一工作包进排期须能答其一，否则砍掉或降级：

1. 是否抬高 **纸面风险调整收益** 的可见性 / 可控性？  
2. 是否压缩 **TTM**（Idea → 可复现回测 → 纸面规则）？  
3. 是否抬高 **回测–纸面拟合**（共用引擎、PIT、成本、相关/TE）？  
4. 是否抬高 **风控底线**（拦截有效率可见、事前硬拦）？  

答不上来 → 不进 R 轨。

### 1.3 与已有文档的分工

| 文档 | 职责 |
|------|------|
| [design-spine.md](design-spine.md) | 北极星定义 · 能力地图 · 因果链 |
| [roadmap.md](roadmap.md) | 历史 P0–P2++ · N1–N6 骨架说明 |
| [quant-ui-upgrade.md](quant-ui-upgrade.md) | Web W0–W5 已交付与 UI 下一刀 |
| [framework-review.md](framework-review.md) | 代码债台账（非产品缺口） |
| [risk-layer.md](risk-layer.md) / [data-layer.md](data-layer.md) | 风控 / 数据域演进细节 |
| **本文** | **下一程升级重构：阶段 · 交付包 · 依赖 · 验收 · 锁定取舍** |

---

## 2. 锁定原则（全程）

| # | 原则 |
|---|------|
| 1 | **产品边界**：一切买卖 = 模拟账户；禁止暗示真券商下单 |
| 2 | **生产 Alpha**：线性可解释 `score_bars`；NN 仅 `research/ml`，promote 须人审 |
| 3 | **LLM 旁路**：不得改写 `score` / `stance_label`；不得静默写 `signal_config` |
| 4 | **先仪表、再填空**：北极星 KPI 未稳前，不并行冲 Tick 数仓 / 完整 QP / OMS |
| 5 | **研究 / 回测 / 纸面同源**：共用 `score_bars` 与 DataService 约定；禁止双套计分 |
| 6 | **验收可演示**：每阶段至少 1 条 API/日更落盘 + 1 条 Web 可见物 + 对应测试 |
| 7 | **UI 契约**：改壳层先改 [quant-ui-standard](quant-ui-standard.md)；默认报告感、不学 Bloomberg 密度 |

---

## 3. 阶段总览（R0–R5）

命名 **R** = Refactor / Upgrade 下一程，与历史 **P0–P2++**、Web **W0–W5** 区分。

```text
R0 北极星仪表硬化 ──► R1 拟合底座（财务 PIT · 成本/冲击）
         │                      │
         └──────────┬───────────┘
                    ▼
              R2 研究吞吐（参数实验 · 规格编辑 · TTM）
                    │
                    ▼
              R3 组合/风控加深（暴露 · 有效率 · QP lite）
                    │
                    ▼
              R4 回测报告机构化（Brinson · 对照回放）
                    │
                    ▼
              R5 工程重构收口（表虚拟化 · 服务边界 · 文档 KPI）
                    │
                    ✕  现行不进入：N6 OMS（策略验证成熟后另立项）
```

| 阶段 | 主题 | 建议周期 | 主抬柱 | 依赖 | 状态 |
|------|------|----------|--------|------|------|
| **R0** | 北极星三项系统 KPI | 1.5～2 周 | 收益 · 拟合 · 速度（度量） | 现有纸面净值 / 回测曲线 | **已落地** |
| **R1** | 数据与成本拟合底座 | 3～4 周 | 拟合 | R0 指标定义稳定 | **主干已落地** |
| **R2** | 研究台吞吐 / TTM | 3～4 周 | 速度 | R0 TTM 事件锚点 | **已落地** |
| **R3** | 组合与风控加深 | 3～4 周 | 收益质量 · 风控底线 | R0 拦截流水可汇总 | **主干已落地** |
| **R4** | 回测机构化报告 | 2～3 周 | 拟合 · 诊断 | R1 成本/冲击字段 | **已落地** |
| **R5** | 工程重构与稳态 | 贯穿 + 收口 1～2 周 | 速度（维护）· 质量 | 各阶段并行债项 | **主干已落地** |

**粗日历（单人全职）**：R0～R2 ≈ **8～10 周** 达「可度量闭环」；R3～R4 ≈ 再 **5～7 周**；R5 穿插。勿与 N6 / 全市场 Tick 仓并行。

---

## 4. R0 · 北极星仪表硬化（先能量化再优化）

**目标**：三项乘积从「文档公式 / 部分 UI」变成 **日更可落盘、API 可查、可回归** 的系统 KPI。

> 注：仪表盘可能已有指标卡雏形（见 [quant-ui-upgrade §13](quant-ui-upgrade.md)）；本阶段验收标准是 **后端权威计算 + 日更写入 + 与 localStorage 装饰解耦**。

### 4.1 交付包

| ID | 项 | 落点（建议） | 验收 |
|----|----|--------------|------|
| R0.1 | **滚动纸面夏普 / 卡玛** | `core/paper_metrics.py`（或等价）；`paper_daily` → `monitor_metrics` / DecisionRecord | 日更后 API 返回 rolling_sharpe · calmar；与回测夏普分列展示 |
| R0.2 | **回测–纸面 Corr / TE** | 同策略窗口对齐权益曲线；写入 `realization` 块 | 指定窗口相关与跟踪误差可查；缺对齐数据时显式 `unavailable` |
| R0.3 | **TTM 事件锚点** | 轻量事件：`idea_opened` · `backtest_ready` · `paper_rule_live`（JSONL 或 DecisionRecord 扩展） | 可算中位 TTM；无事件时 UI 标「未度量」 |
| R0.4 | **拦截有效率 / 误拦率** | 从 `risk_block` 流水汇总（先人工标注规则或启发式） | 平台/策略折叠可见比率或样本量不足说明 |
| R0.5 | **Web 北极星条** | `/` 或平台只读 KPI 条；禁止变成第二套业务主表 | 与 API 数字一致；刷新失败 last-known |
| R0.6 | **测试** | `tests/test_north_star_metrics.py`（名可调） | 合成净值/曲线 → 指标数值稳定单测 |

### 4.2 不做

完整归因重构、财务 PIT、在线策略 JSON IDE、QP 求解器。

### 4.3 出门标准

- [x] 连续跑 3 次 `paper_daily`，KPI 字段非空或合法 `unavailable`（逻辑已接线；本地复跑验收）  
- [x] `GET /api/north-star` + `tests/test_north_star_metrics.py`  
- [ ] 说明书 [quant-ui.md](quant-ui.md) / [design-spine](design-spine.md) 二级指标表更新为「已仪表化」（部分已同步）  
- [x] 需求拷问：后续 PR 描述须引用抬高哪一柱  

#### R0 落地摘要（2026-07-29）

| ID | 状态 | 落点 |
|----|------|------|
| R0.1 滚动夏普/卡玛 | ✅ | `core/north_star.compute_paper_risk_metrics` → ops / 日更 / API |
| R0.2 Corr/TE | ✅ | `compute_realization` + `data/north_star_last_backtest.json`（组合回测落盘） |
| R0.3 TTM 打点 | ✅ | `data/ttm_events.jsonl`：反馈建议 / 回测成功 / promote |
| R0.4 拦截流水 | ✅ | `summarize_risk_blocks`（有效率仍 unavailable，待标注） |
| R0.5 Web | ✅ | 平台北极星条 · 模拟页 KPI；`ASSET_V` bump |
| R0.6 测试 | ✅ | `tests/test_north_star_metrics.py` |  

---

## 5. R1 · 拟合底座（财务 PIT · 成本冲击）

**目标**：减少「回测美、纸面惨」的结构性原因——**未来函数与成本模型过简**。

### 5.1 交付包

| ID | 项 | 落点（建议） | 验收 |
|----|----|--------------|------|
| R1.1 | **财务 PIT 最小可用** | 快照带 `as_of` / 报告期；`get_fundamentals(as_of=)`；估值/质量因子只读当时可见点 | 回测窗口打分无「未来财报」；`pit_report` 含 fundamentals 标记 |
| R1.2 | **非 PIT 显式降级** | 无历史点时因子权重归零或 `hard_reject` 策略可配置 | 文档 + UI 标明 snapshot 路径 |
| R1.3 | **冲击成本进回测统计** | `simple_cn` 之上可选冲击档；组合回测 `cost_compare` 增列 | 报告可见冲击贡献；缺模型不画假数 |
| R1.4 | **live / 回测源一致性审计** | 一次调仓/回测对比 `data_source` · fallback 计数 | 五问或质量折叠能指出不一致票 |
| R1.5 | **拟合指标回归** | R0.2 在 PIT/成本变更后重跑 | Corr/TE 变化可解释（changelog 一句） |

### 5.2 不做

全市场财务时序仓、Tick ETL、多源对齐中台。

### 5.3 出门标准

- [x] 至少 1 条 eval / 集成测锁定「as_of 财务」无未来泄漏（`tests/test_r1_fundamentals_pit.py`）  
- [x] [data-layer.md](data-layer.md) 更新 PIT 约定与限制  
- [x] 观察/回溯页质量文案与后端字段对齐（回测摘要 · 财务PIT · 源审计 · 冲击 bps）  

#### R1 落地摘要（2026-07-29）

| ID | 状态 | 落点 |
|----|------|------|
| R1.1 财务 PIT | ✅ | `core/fundamentals_pit.py` · `get_fundamentals(as_of=)` · 组合回测按信号日选取 |
| R1.2 非 PIT 降级 | ✅ | `missing_as_of_policy=zero_weight`；拒绝未来快照 |
| R1.3 冲击成本 | ✅ | `apply_trade_cost` 接量能；`cost_compare.avg_impact_bps` |
| R1.4 源一致性 | ✅ | `core/data_consistency.py` → 回测 `source_audit` · ops_report |
| R1.5 拟合回归 | ✅ | 样本运营包抬 snapshots/TTM/PIT；Corr/TE 路径不变，覆盖可查 `/api/ops/sample-status` |
---

## 6. R2 · 研究吞吐（压缩 TTM）

**目标**：研究员从「改 JSON / 跑 CLI」升级到「页内可实验」；服务北极星 **Velocity**。

### 6.1 交付包

| ID | 项 | 落点（建议） | 验收 |
|----|----|--------------|------|
| R2.1 | **参数网格 / 热力摘要** | 回测 API 批量 `param_scan`；Web 表或热力（可先表） | ✅ 同策略 ≥2 维参数扫描可出报告块 |
| R2.2 | **策略规格可编辑（约束内）** | ~~Monaco 草稿 + promote~~ → **已退役**；选股权改走研究枢纽 ReturnScoreModel | ✅ 历史已交付；现路径不经 signal_config 草稿 |
| R2.3 | **Notebook 降级方案** | 可选：导出研究脚本 / 固定 `research/*.py` 模板；全量 Jupyter **非必须** | ✅ 文档给出「Idea 标准路径」≤5 步 |
| R2.4 | **TTM 仪表接线** | R0.3 事件在「新建扫描 / 回测成功 / promote」处打点 | ✅ 打点已接线（中位周环比待样本） |
| R2.5 | **IC / weight_suggest 实验流** | 策略页：扫描 → 只读 diff → 一键生成 feedback | ✅ 不改变生产权重除非 promote |

### 6.2 不做

全站 SPA、在线任意 Python 沙箱（安全面过大）、自动改权。

### 6.3 出门标准

- [x] 新人按说明书完成「改一参 → 回测 → 看扫描表」&lt; 30 分钟（见 [research/README · Idea 路径](../research/README.md#idea-标准路径)）  
- [x] [quant-ui-standard](quant-ui-standard.md) 增补规格编辑白名单与人审边界  
- [x] R0 TTM 不再长期 `unavailable`（有样本后；打点已接线；`sample_ops seed-ttm` / cycle_id 配对）  

#### R2 落地摘要（2026-07-29）

| ID | 状态 | 落点 |
|----|------|------|
| R2.1 参数网格 | ✅ | `run_param_grid` · 回溯页热力/表 ·「应用最优」填 lookback/top_k |
| R2.2 规格草稿 | ✅→退役 | 曾：Monaco · `signal_config_draft`；现已删除，选股权 → 研究枢纽 β |
| R2.3 Idea 路径 | ✅ | `research/README.md` ≤5 步（研究枢纽 / 回测 / 策略卡） |
| R2.4 TTM | ✅ | 网格 / 反馈 / promote 打点 |
| R2.5 IC→feedback | ✅ | 研究枢纽 IC / OLS 只读；不写 signal_config |
| 测试 | ✅→退役 | `test_r2_signal_config_draft` 已删 |

---

## 7. R3 · 组合与风控加深

**目标**：从「静态限额 + 分数预算」迈向「敞口可见 + 有效率可审计」；对齐 [risk-layer](risk-layer.md) 演进 ②→③ 的前半。

### 7.1 交付包

| ID | 项 | 落点（建议） | 验收 |
|----|----|--------------|------|
| R3.1 | **行业 / 简易风格暴露矩阵** | 持仓 × sector_map（+ 可选市值桶）；调仓报告 / 策略折叠 | ✅ 一屏可见集中度；超限与预算告警同源 |
| R3.2 | **拦截有效率仪表** | 硬化 R0.4；按日/周汇总 | ✅ 误拦可追溯到 `risk_block` 原因码 |
| R3.3 | **QP lite（可选）** | 在分数预算之上：均值–方差或风险平价 **简化求解**；失败回退贪心 | ✅ `risk_parity_lite`（1/vol·等权+限额；无 cvxpy） |
| R3.4 | **风控巡检折叠** | 不加第 7 侧栏；平台或策略页聚合敞口 + 限额 + 流水 | ✅ 符合 quant-ui「每页一事」 |
| R3.5 | **拥挤度 / 统一风险分** | 标为 **R3 可选 / 远期**；无数据不硬做 | — 文档保留缺口 |

### 7.2 不做

实时 VaR 引擎、自动强平实盘、ML 风控拟合上生产。

### 7.3 出门标准

- [x] 故意构造超行业上限：硬拦 + 有效率分母增加（`tests/test_r3_exposure_risk.py`；标注 outcome 后算率）  
- [x] [risk-layer.md](risk-layer.md) 现状表更新  
- [x] 组合回测 / 调仓仍默认 `check_account_risk`  

#### R3 落地摘要（2026-07-29）

| ID | 状态 | 落点 |
|----|------|------|
| R3.1 暴露矩阵 | ✅ | `core/risk/exposure.py` · ops/纸面 API · 策略页折叠 |
| R3.2 拦截有效率 | ✅ | 结构化 `block_codes` · 按日/周 · outcome 标注后算率 |
| R3.3 QP lite | ✅ | `weight_mode=risk_parity_lite` · `budget.risk_parity_lite_weights`；失败回退 greedy |
| R3.4 风控巡检折叠 | ✅ | `/strategy` 敞口+限额+流水；五问增行业敞口 |
| R3.5 拥挤度/风险分 | — | 远期，文档保留缺口 |
| 测试 | ✅ | `tests/test_r3_exposure_risk.py` |

---

## 8. R4 · 回测报告机构化

**目标**：补齐「能跑一轮」到「机构可读报告」之间的诊断深度（服务拟合与收益质量诊断，不替代纸面 KPI）。

### 8.1 交付包

| ID | 项 | 落点（建议） | 验收 |
|----|----|--------------|------|
| R4.1 | **深度 Brinson / 因子归因** | `core/backtest/attribution` 扩展；脚注方法论 | ✅ Brinson lite + score 半组；无数据块不渲染 |
| R4.2 | **信号–成交对照** | 样本表：信号日 score / 意图价 / 成交价 / 涨跌停跳过 | ✅ `signal_fill_sample` 对齐 skipped_limit* |
| R4.3 | **多 regime 对照卡片** | 复用 `regime_summary`；Web 分桶指标 | ✅ `regime_buckets`；OOS 失败仍标红 |
| R4.4 | **报告导出** | MD/HTML 与页内块序一致 | ✅ `POST /api/quant/export/backtest` · 日报 Top-K 节加深 |

### 8.2 不做

Tick 逐笔回放、交易所级撮合仿真（保持「近似」定位）。

#### R4 落地摘要（2026-07-29）

| ID | 状态 | 落点 |
|----|------|------|
| R4.1 Brinson lite | ✅ | `attribution.brinson` · `factor_proxy` · 回溯页表 |
| R4.2 信号–成交 | ✅ | TopK legs 价格字段 · `signal_fill_sample` UI |
| R4.3 Regime 分桶 | ✅ | `regime_buckets_from_trades` · Web 卡片 |
| R4.4 报告导出 | ✅ | 页内「导出回测报告」· 日报摘要对齐 |
| 测试 | ✅ | `tests/test_r4_backtest_report.py` |

---

## 9. R5 · 工程重构与稳态（贯穿）

**目标**：降低继续加深功能时的变更成本；消化 [framework-review](framework-review.md) 与 UI 工程债，**不**借重构扩产品面。

### 9.1 交付包（可与 R0–R4 并行插队）

| ID | 项 | 说明 | 验收 |
|----|----|------|------|
| R5.1 | **主表真虚拟化** | `virtual_table.js` 观察/持仓 ≥200 行 | ✅ 默认虚拟滚动；预算 500 行；分页仅回退 |
| R5.2 | **paper / holdings 继续拆** | 大模块边界清晰；禁回巨型 `paper.js` | ✅ `paper/holdings_island.js` 控制器拆出 |
| R5.3 | **服务层边界** | 新 KPI / PIT 逻辑进 `core/`，Web router 只组装 | ✅ KPI/PIT/风控在 core；router 薄封装 |
| R5.4 | **性能预算** | 首屏 · 表 500 · 图 5y；写入手册 | ✅ [quant-ui-standard §4.1](quant-ui-standard.md) |
| R5.5 | **文档三联更新** | spine 达成度 · roadmap 指针 · 本文进度表 | ✅ 同步 R0–R5 |
| R5.6 | **evals 黄金路径** | 北极星 KPI · PIT · 调仓硬拦 各至少 1 例 | ✅ `evals/core_golden_paths.py` |

### 9.2 明确不做的「重构」

- 全站 React/CRA、Ant Design Pro  
- 重写 Agent 框架、为重构而换存储（SQLite/时序库）——除非 R1 PIT 证明 JSON 不可维护后再单独立项；选型理由与触发条件见 [data-layer · 存储选型](data-layer.md#存储选型为何是-json何时才上数据库)

#### R5 落地摘要（2026-07-29）

| ID | 状态 | 落点 |
|----|------|------|
| R5.1 虚拟表 | ✅ | `virtual_table.js` · watching/holdings 岛 · `VIRTUAL_TABLE_ROW_BUDGET=500` |
| R5.2 paper 拆分 | ✅ | `paper/holdings_island.js`；编排仍 `paper.js`（继续可拆） |
| R5.3 服务边界 | ✅ | 业务在 core；无新 KPI 进 app.py |
| R5.4 性能预算 | ✅ | quant-ui-standard §4.1 + 验收勾选 |
| R5.5 文档三联 | ✅ | spine · roadmap · 本文进度 |
| R5.6 evals | ✅ | `run_core_paths.py` · checklist 默认同跑 |
| 测试 | ✅ | `tests/test_r5_engineering.py` |

---

## 10. 边界外清单（本规划拒绝项）

| 项 | 去向 |
|----|------|
| OMS / EMS / Algo / Level2 / 多账户真交易 | 现行不做；策略验证成熟后 N6 另立项 |
| 暗色 Bloomberg 密度默认皮肤 | 须先改 quant-ui-standard；默认不改 |
| Tick 全量仓 / 宏观全集 ETL | N5 成功 + 拟合 KPI 稳定后另评估 |
| NN 直连生产 `score` | 禁止 |
| 在线 RL 自动调权 | 见 [rl-layer](rl-layer.md)；非本规划 |

---

## 11. 依赖与并行建议

```text
R0（KPI 定义/落盘）
  ├─► R1（PIT/冲击） ──► R4（机构报告）
  ├─► R2（TTM/实验） 
  └─► R3（暴露/有效率）── 可与 R2 后半并行

R5 工程债：全程插队，但不得阻塞 R0 出门
Web：优先消费 R0/R1 API；IDE/扫描 UI 跟 R2；Brinson 列跟 R4
```

| 并行对 | 条件 |
|--------|------|
| R0 API ∥ 仪表盘 KPI 条 | 契约字段先冻结 |
| R1 PIT ∥ R2 参数扫描 UI | 扫描不得依赖未完成财务因子 |
| R3 暴露矩阵 ∥ R5 虚拟表 | 无硬依赖 |

---

## 12. 进度表（维护用）

| 阶段 | 状态 | 出门日 | 备注 |
|------|------|--------|------|
| R0 | **已落地** | 2026-07-29 | `core/north_star.py` · `/api/north-star` · 日更/ops · 平台+模拟 KPI |
| R1 | **主干已落地** | 2026-07-29 | 财务 PIT 面板 · 冲击成本 · 源审计 |
| R2 | **已落地** | 2026-07-29 | 参数网格 · 草稿/promote · Idea 路径 · IC→feedback |
| R3 | **主干已落地** | 2026-07-29 | 暴露矩阵 · 原因码 · 有效率仪表 · risk_parity_lite |
| R4 | **已落地** | 2026-07-29 | Brinson lite · 信号成交 · regime 分桶 · 报告导出 |
| R5 | **主干已落地** | 2026-07-29 | 虚拟表 · holdings 拆分 · 性能预算 · core evals · 文档三联 |

阶段完成后：勾选对应章节出门标准 → 更新本表 → 回写 [design-spine · 达成度](design-spine.md#达成度评估2026-07) 一句。

---

## 13. 立即开工建议（下一刀）

**R0–R5 已收口。** 现行下一程见 **[strategy-validation-upgrade.md](strategy-validation-upgrade.md)**（V0–V5 · 策略验证成熟度）。

历史收尾包（已完成，勿重复）：

1. ✅ `sector_map` 扩覆盖 + `research/sector_map_sync_run.py`  
2. ✅ `risk_block` outcome 标注 API / 策略页按钮  
3. ✅ R3.3 `risk_parity_lite`  
4. ✅ 样本运营：TTM cycle · history · densify · `/api/ops/sample-status`  

**禁止**借机开 OMS / Tick / 全站 SPA / NN 上生产 score。V 轨下一刀优先 **真实财务 PIT 覆盖**（V0）。

---

## 14. 风险与缓解

| 风险 | 缓解 |
|------|------|
| KPI 数字与直觉不符引发「调指标」 | 方法论脚注 + 合成数据单测；禁止为好看改公式 |
| PIT 数据源不完整导致大面积 `unavailable` | 显式降级策略；先覆盖观察池热门标的 |
| 研究编辑误写生产配置 | 草稿 / diff / promote；沿用 feedback 人审 |
| 范围膨胀回 OMS / 数仓 | 每周对照 §10；PR 模板强制「抬高哪一柱」 |
| 与 quant-ui-upgrade 下一刀冲突 | UI 虚拟表归 R5；拟合指标以后端 R0 为准 |

---

## 15. 验收总清单（规划终点）

- [x] 北极星三项有系统级 API + 日更字段 + Web 一致展示  
- [x] 财务 PIT 最小路径可演示；回测 pit_report 含基本面  
- [x] 参数扫描与规格草稿→promote 可演示；TTM 有样本（事件锚点已接线；样本持续积累）  
- [x] 暴露矩阵 + 拦截有效率可巡检（含 outcome 标注 API / 策略页）  
- [x] 归因/信号–成交对照达「机构摘要」级（非完整交易所仿真）  
- [x] 主表虚拟化与 core/服务边界债务可控  
- [x] **无** OMS / 真下单文案；生产分仍可审计  

达成后：能力地图粗估预期进入 **~75%～82%** 区间（仍缺完整数仓/交易所撮合/N6）；**北极星三项从「未仪表化」转为「可回归优化」**——这才是本规划的真正终点，而非模块百分比。
