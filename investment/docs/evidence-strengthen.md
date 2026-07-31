# 北极星证据诚实深化（E 轨）

[← 文档索引](README.md) · 产品主轴 [design-spine.md](design-spine.md) · 已收口 S 轨 [validation-strengthen.md](validation-strengthen.md) · D 轨 [data-layer-strengthen.md](data-layer-strengthen.md) · N6 [n6-live-gate.md](n6-live-gate.md)

**规划日期**：2026-07-31  
**定位**：在 R/V/D/S 主干已收口之后，用可验收代码抬高**北极星三项证据的可归因性与可对齐性**。  
**命名**：**E** = Evidence / Equity honesty；不冲 OMS / Tick / 全市场数仓。

---

## 0. 一句话目标

```text
成功画像（E 轨终点）
  · 纸面夏普分列全账户 vs 策略仓（equity_strategy）
  · 回测曲线落盘默认按纸面日期跨度裁剪；拟合诊断含 span
  · 单票 / OLS 因子 IC 与截面 IC 同走财务 PIT
  · 截面研究日强制 CN 交易日历 lite
  · sample-status 催办未标注 outcome
```

---

## 1. 阶段总览（E0–E4）

| 阶段 | 主题 | 状态 |
|------|------|------|
| **E0** | 策略纯净纸面 KPI | **已落地** |
| **E1** | 回测–纸面日期自动对齐 | **已落地** |
| **E2** | 单票/OLS 因子 IC 财务 PIT | **已落地** |
| **E3** | 截面日强制交易日历 | **已落地** |
| **E4** | outcome 催办 + 文档/闸门/测 | **已落地** |

---

## 2. 代码索引

| 模块 | 路径 |
|------|------|
| 策略净值快照 | `paper_exec.mark_to_market` · `paper.append_snapshot` |
| 北极星双 scope | `core/north_star.py`（`paper_risk_strategy` / `realization_strategy`） |
| 曲线对齐 | `save_last_backtest_curve(align_to_paper=True)` |
| 单票 IC PIT | `quant/research/factor_report.py` · `factor_registry.run_factor_experiment` |
| OLS PIT | `quant/research/factor_ols.py` |
| 日历 | `pool_ic` · `factor_cs_ic` + `market_calendar` |
| 未标注摘要 | `core/risk/block_outcome.unlabeled_digest` · `sample_ops.sample_status` |
| 闸门软项 | `maturity_gate` · `strategy_scope_readable` |
| 测 | `tests/test_e_track.py` |

---

## 3. 验收

- [x] E0–E4 代码与 API 可演示  
- [x] `python -m unittest tests.test_e_track` 通过  
- [x] `/api/north-star` 含 `paper_risk_strategy` / `scopes`  
- [x] 因子 IC / CS IC 元数据含 `pit_fundamentals` / `calendar`  
- [x] sample-status 含 `outcome_unlabeled`  
- [ ] （运营）`real_multi_coverage≥0.5` + 真实日更 + outcome 标注 — **人工持续**

**现行不进入 N6 OMS。**
