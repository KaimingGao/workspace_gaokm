# 专业核心三轨深化补强（DC / FM / RK）

[← 文档索引](../README.md) · 产品主轴 [design-spine.md](../design-spine.md) · 已收口 B [beta-regression-strengthen.md](beta-regression-strengthen.md) · N6 [n6-live-gate.md](../design-spine.md#n6-真实盘准入备忘仅文档--无代码)

**规划日期**：2026-08-09 · **主干落地**：2026-08-09  
**定位**：在 D/S/E/X/Y/B/C/P/EP 已收口之后，针对相对专业栈仍弱的三条路径内能力——**数据清洗加深（DC）· 因子模型硬化（FM）· 组合风控加深（RK）**——做可验收补强。  
**命名**：**DC** = Data Clean deepen；**FM** = Factor Model harden；**RK** = Risk / 组合 deepen。  
**终点**：抬高回测–纸面拟合可信度与纸面夏普可辩护性；逼近 N6 闸门评估。**本轨不写 OMS / Tick / 全市场数仓 / NN→生产分 / 完整 Barra / 真强平**。

---

## 0. 一句话目标

```text
成功画像（三轨终点）
  · 财务「可用日覆盖」可量化；ann_missing / 假 PIT 不冒充干净；停牌日不进截面；复权策略可辩护
  · 因子健康进 promote 硬门；伪/稀疏因子进不了生产；IC–β 衰减可发现并联动 refit/demote
  · 风格/β 暴露进优化目标；动态限额可解释；拦截 outcome 有样本门槛；qp_lite 对照可复现（非强制）
```

依赖默认：先 **DC** → 再 **FM** → 后 **RK**（RK0 可与 FM2 尾部小幅并行）。

```text
DC0 → DC1 → DC2 → DC3
                    │
                    ▼
              FM0 → FM1 → FM2 → FM3
                              │
                              ▼
                        RK0 → RK1 → RK2 → RK3
```

---

## 1. 背景：已收口 vs 本轨补强

| 轨 / 能力 | 状态 | 说明 |
|-----------|------|------|
| **D0–D4** | ✅ | ann 入库 · source_audit · 复权 · 日历 · DQ 中心 |
| **X0–X5** | ✅ | live 财务 PIT · index · 伪因子健康 · 同构门禁 |
| **Y0–Y5 / B0–B5** | ✅ | ŷ 口径 · 衰减骨架 · 样本/y/共线/λ/重估 |
| **C/P/EP** | ✅ | 换手 · 预算缩量 · 启用证据包 |
| **本轨主干** | ✅ 已落地 | PIT 深度 · 停牌过滤 · promote 健康门 · 风格/regime 限额 · outcome 催办 · 验证包对照 |

**边界外（不进本轨）**：OMS、Level2、完整 Barra、NN→生产 `score`、静默写 `signal_config.weights`、全市场 Tick 仓、实时 VaR 引擎、真强平。

---

## 2. 锁定原则

| # | 原则 |
|---|------|
| 1 | 仍经 **DataService / ports**；禁止业务散落扫盘 |
| 2 | 生产选股真源 = **组/全局 `ReturnScoreModel` → ŷ%**；heuristic 仅研究 OOS |
| 3 | 永不静默写 `signal_config.weights`；force promote 必写 audit |
| 4 | LLM 不改 `score` / `stance_label` / ŷ |
| 5 | 先可信（PIT·健康门·限额可解释）再变厚（QP·大因子库） |
| 6 | 每阶段 ≥1 API/落盘 + ≥1 Web 可见 + ≥1 测试；改静态 bump `ASSET_V` |
| 7 | UI 遵守 [../quant-ui.md#web-ui-标准研究台](../quant-ui.md#web-ui-标准研究台) |

---

## 3. 轨 A · 数据与清洗（DC0–DC3）

目标：让「决策日可见」比「库里有数」更可信。

| 阶段 | 主题 | 交付要点 | 主要落点 |
|------|------|----------|----------|
| **DC0** | 财务 PIT 深度 | 多期面板覆盖率阈值；`ann_missing` 进 maturity/DQ 同口径；假 PIT 软/硬项 | `fundamentals_pit` · `data_quality_center` · `maturity_gate` · `pro_core` |
| **DC1** | 停牌 / 复牌对齐 | `filter_halted_bars`；面板/回测跳过不可交易 bar 并写 audit | `market_calendar` · cluster panel · backtest |
| **DC2** | 公司行为可辩护 | 复权策略强制一致；混用拒绝；报告显式 `adjust_policy` | `data_service` · `source_audit` · run manifest |
| **DC3** | 覆盖催办闭环 | DQ：ann_missing TopN + 一键 ingest；平台页可巡检 | `/api/ops/data-quality` · platform UI · sample_ops |

### 验收

- [x] 验证宇宙财务可用日覆盖可量化；ann_missing 不冒充干净 PIT
- [x] 停牌/零量 bar 可过滤并进 audit
- [x] 回测/读条路径 `adjust_policy` 可辩护
- [x] 平台 DQ 可对 ann_missing TopN 发起 ingest 催办

---

## 4. 轨 B · 因子与模型（FM0–FM3）

目标：生产 ŷ「能跑」→「衰减可发现、伪因子进不了 promote」。

| 阶段 | 主题 | 交付要点 | 主要落点 |
|------|------|----------|----------|
| **FM0** | 因子健康硬门 | `guard_weights_for_promote` 接入 promote/apply；force 必 audit | `factor_health` · cluster/strategy promote |
| **FM1** | 稀疏 / 伪因子治理 | registry 暴露 sourced/proxy；生产进模白名单可巡检 | `factor_registry` · `PROXY_OR_UNSOURCED` |
| **FM2** | IC–β 衰减联动 | 滚动 IC / decay 摘要进 cluster health；与 refit/demote 打通 | `cluster_live_health` · `walk_forward.ic_decay_curve` |
| **FM3** | 截面中性化验收 | neutralize 开关进 validation_pack 指纹 / ab_compare | `neutralize` · `validation_pack` |

### 验收

- [x] promote 健康不合格默认拦截；force 写 audit
- [x] 生产进模因子列表可解释（proxy 可见）
- [x] 衰减/重估信号在 health 可见
- [x] 验证包带 neutralize 配置指纹

---

## 5. 轨 C · 组合与风控（RK0–RK3）

目标：限额从「检查」升级为「进目标 + 可审计动态」；仍停留在纸面。

| 阶段 | 主题 | 交付要点 | 主要落点 |
|------|------|----------|----------|
| **RK0** | 风格 / β 进目标 | 暴露风格桶进 optimize 软约束；超限缩量/告警 | `exposure` · `portfolio_optimize` · `budget` |
| **RK1** | 动态限额表 | regime × 波动分档 → 有效 `max_position/sector`；预演可见 | `regime` · `market_vol_scale` · optimize |
| **RK2** | 拦截 outcome 闭环 | 未标注催办摘要；有效率样本门槛 | `block_outcome` · `north_star` · strategy UI |
| **RK3** | qp_lite 可复现对照 | `compare_weight_modes` 进 validation_pack.ab_compare；缺 cvxpy 标明 unavailable | `weight_mode_compare` · `validation_pack` |

### 验收

- [x] 优化结果可见风格/行业约束影响
- [x] 高波/弱趋势日有效限额收紧且可解释
- [x] 未标注拦截可催办；有效率有样本门槛
- [x] 验证包可复现 weight_mode 对照

---

## 6. 落地索引

| 阶段 | 关键落点 |
|------|----------|
| DC0 | `core/pro_core.py` · maturity `dc_track` · DQ `pit_depth` |
| DC1 | `market_calendar.filter_halted_bars` · panel/backtest 可选过滤 |
| DC2 | `assert_adjust_policy_consistent` · source_audit 挂 adjust |
| DC3 | `POST /api/ops/fundamentals-ingest-nudge` · platform DQ 按钮 |
| FM0 | promote 路径调用 `guard_weights_for_promote` |
| FM1 | `list_factors(include_meta=True)` sourced/proxy |
| FM2 | cluster health `ic_decay_summary` |
| FM3 | validation_pack `neutralize` fingerprint |
| RK0–RK1 | `optimize_weights` style soft-cap + regime×vol 有效限额 |
| RK2 | `unlabeled_digest` 进 DQ / maturity 软项 |
| RK3 | validation_pack `ab_compare.weight_modes` |
| 测 | `tests/test_pro_core_track.py` |

---

## 7. 成功度量（绑北极星）

- **拟合**：`source_audit` + `adjust_policy` 一致；停牌跳过可审计
- **收益质量诊断**：因子健康拦截伪权重；衰减触发 7 日内出现 refit/demote 信号
- **风控底线**：有效限额在预演可见；outcome 未标注可催办；weight_mode 对照可复现

---

## 8. 排期建议

| 周 | 内容 |
|----|------|
| W0 | 本文档 + 索引 |
| W1–W2 | DC0–DC1 |
| W3 | DC2–DC3 |
| W4–W5 | FM0–FM2 |
| W6 | FM3 + RK0 |
| W7–W8 | RK1–RK3 |
