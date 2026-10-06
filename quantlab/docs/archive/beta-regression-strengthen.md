# 回归准确性深化补强（B 轨）

[← 文档索引](../README.md) · 选股主轴 weight-suggest-deepen（已归档）· 特征同构 feature-signal-strengthen（已归档，X **已落地**）· ŷ 硬化 yhat-strengthen（已归档）· N6 [n6-live-gate.md](../design-spine.md#n6-真实盘准入备忘仅文档--无代码)

**规划日期**：2026-08-05 · **落地**：2026-08-05  
**定位**：在 X 轨把 live/研究 **特征 X 对齐** 之后，专攻 **回归估 β 的准确性与稳定性**——样本、标签 y、共线/正则、滚动重估、截面范式。  
**命名**：**B** = Beta / Regression Accuracy；不重复造 X（同构）或 Y（生产口径）。  
**终点**：组/全局 OLS β 在 OOS 上更稳、可衰减监控、可辩护；**不**用树模型/MoE 替换生产 ŷ。  
**不做**：OMS · Tick · 全市场数仓 · 舆情进 ŷ · 静默写 `signal_config.weights` · NN→生产分。

---

## 0. 一句话目标

```text
成功画像（B 轨终点）
  · 拟合用 (X,y)：财务覆盖达标、ann_missing 可控、y 与 horizon/成本口径显式一致
  · 趋势族共线有「进模策略」（合并/正交/择一）+ Ridge λ 可滚动选定
  · 组 β 有滚动重估与 IC 衰减触发；陈旧模型不可长期 active
  · 主拟合叙事默认「决策日截面」；OOS 默认 respect_regime 与 live 对齐
  · 成熟闸门含回归质量软/硬项（样本 n、OOS、ann、refit）
```

因果链焦点：

```text
数据 → 信号(X) → 【回归估 β】→ ŷ → 动作
                      ▲ 本轨
```

---

## 落地摘要

| 阶段 | 主题 | 状态 | 落点 |
|------|------|------|------|
| **B0** | 覆盖 / ann 闭环 | ✅ | `sample_ops` · DQ · platform 样本 UI · maturity `ann_missing_ops` |
| **B1** | 有效样本 | ✅ | `sample_fingerprint` · `universe_sample_gate` · promote 校验 |
| **B2** | y 契约 | ✅ | `build_y_spec` · `scoring.horizon_days` · fit-gap · return_model |
| **B3** | 共线 + λ | ✅ | `apply_collinearity_policy` · `select_ridge_lambda` · cluster API |
| **B4** | 滚动重估 | ✅ | `refit_max_age_days` · IC demote · 落地卡「建议重估」 |
| **B5** | 截面 + regime-OOS | ✅ | 默认 `respect_regime=True` · OOS 戳记 · 叙事 primary=截面 |

| 测 | `tests/test_b_track.py` · `ASSET_V=p682` |
| 核心模块 | `core/research/beta_accuracy.py` |

---

## 1. 背景：已收口 vs 仍缺

| 能力 | 状态 | 说明 |
|------|------|------|
| **X0–X5** | ✅ | live 财务 PIT · index · 伪因子 · quality_policy |
| **P4 组 OLS → return_model** | ✅ | 选股真源 ŷ |
| **Y1 衰减提示** | ✅ | `refit_suggested` 等骨架 |
| **共线摘要** | ✅ 加深 | 提示 → **进模 drop_redundant**（可 keep_all） |
| **B0–B5** | ✅ | 本文件 |

边界外：树/MoE 生产替换、另类新闻进回归、完整 Barra 商业库。

---

## 2. 锁定原则

| # | 原则 |
|---|------|
| 1 | 生产 ŷ 仍是 **线性 ReturnScoreModel**；本轨只抬 β 质量 |
| 2 | **先样本与 y，再模型花样** |
| 3 | 永不静默写 weights；promote / 刷新簿仍人审 |
| 4 | OOS 对照必须可复现；demo/synthetic 不得宣称验证 |
| 5 | 每阶段 ≥1 API/落盘 + ≥1 Web 可见 + ≥1 测试；改静态 bump `ASSET_V` |
| 6 | UI 遵守 [../quant-ui.md#web-ui-标准研究台](../quant-ui.md#web-ui-标准研究台) |

---

## 3. 阶段总览（B0–B5）

```text
B0  财务覆盖与 ann 运营闭环（压前视/稀疏）
        │
        ▼
B1  有效样本与验证宇宙扩张纪律
        │
        ▼
B2  标签 y 契约（horizon · 成本 · 停牌）
        │
        ▼
B3  共线进模策略 + Ridge λ 滚动
        │
        ▼
B4  滚动重估与衰减强制纪律
        │
        ▼
B5  截面主叙事 + OOS 默认 respect_regime
```

| 阶段 | 主题 | 预估 | 状态 |
|------|------|------|------|
| **B0** | 覆盖 / ann 闭环 | 0.5–1 d | **已落地** |
| **B1** | 有效样本 | 0.5–1 d | **已落地** |
| **B2** | y 契约 | 0.5–1 d | **已落地** |
| **B3** | 共线 + λ | 1 d | **已落地** |
| **B4** | 滚动重估 | 1 d | **已落地** |
| **B5** | 截面 + regime-OOS | 1 d | **已落地** |

---

## 4. 分阶段规格（验收勾选）

### B0 · 财务覆盖与 ann 运营闭环

- [x] DQ / sample-status 露出缺 ann 清单与 ingest_hint
- [x] 闸门项可点进行动（maturity `ann_missing_ops`）
- [x] 测：coverage 字段契约（`ann_missing_top_codes`）

### B1 · 有效样本与验证宇宙

- [x] 拟合产物含样本指纹；不足时 UI 红字
- [x] promote 校验 `sample_fingerprint.promote_ok` + `universe_sample_gate`
- [x] 测：min_samples 门禁

### B2 · 标签 y 契约

- [x] 每个 return_model / cluster artifact 带 `y_spec`
- [x] fit-gap 提示 horizon 不一致
- [x] `scoring.horizon_days` 与 `build_y_spec` 同源
- [x] 测：y_spec 字段存在

### B3 · 共线进模 + Ridge λ

- [x] 拟合 meta：`collinearity_policy` · `ridge_lambda_selected`
- [x] API：`select_ridge` / `collinearity_policy`（默认 drop_redundant + 选 λ）
- [x] 测：高相关人造面板触发 drop

### B4 · 滚动重估与衰减纪律

- [x] `refit_max_age_days` / `min_yhat_rolling_ic`；超龄或 IC 破线 → demote
- [x] UI：落地卡陈旧/建议重估 +「建议重估」→ 跑分组
- [x] 成熟闸门 `refit_discipline`

### B5 · 截面主叙事 + OOS respect_regime

- [x] OOS API/任务默认 regime-aligned（`respect_regime=True`）
- [x] 叙事：primary=截面/组池；secondary=单票时序探针
- [x] 测：默认参数含 respect_regime

---

## 5. 与既有轨关系

```text
X 轨：X 同构（已落地）
Y 轨：ŷ 生产口径与衰减提示
B 轨：把 β 估准、估稳、按时重估     ← 本文件 · 已落地
S/E：验证与证据诚实（样本/PIT 工具）
```

---

## 6. 风险与回滚

| 风险 | 缓解 |
|------|------|
| drop_redundant 改变 β 排序 | `collinearity_policy=keep_all`；影子对照 |
| 自动 demote active 打扰 | 仅超龄/IC 破线；`auto_demote_on_stale` 可关；force 豁免 |
| y 改口径导致历史不可比 | artifact 带 y_spec；旧包只读 |

---

## 7. 文档与索引

- [x] 本文件阶段 → 已落地  
- [x] [docs/README.md](../README.md) B 轨一行  
- [x] weight-suggest-deepen（已归档）链到 B  
- [x] feature-signal-strengthen（已归档）「后继 B」  
- [x] maturity_gate `track` 含 B
