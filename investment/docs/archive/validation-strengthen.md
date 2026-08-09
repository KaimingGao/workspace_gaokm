# 策略验证深化补强规划（S 轨）

[← 文档索引](../README.md) · 产品主轴 [design-spine.md](../design-spine.md) · 已收口 V 轨 [strategy-validation-upgrade.md](strategy-validation-upgrade.md) · R 轨 [upgrade-refactor-plan.md](upgrade-refactor-plan.md) · N6 备忘 [n6-live-gate.md](../n6-live-gate.md) · 数据 [data-layer.md](../data-layer.md) · 风控 [risk-layer.md](../risk-layer.md)

**规划日期**：2026-07-31  
**定位**：在 **R0–R5 / V0–V5 主干已落地** 之后，把系统从「研究–纸面可演示、KPI 可查」升到「**策略结论可被严肃验证、可对外辩护**」。  
**命名**：**S** = Strengthen / 升级深化补强；与历史 **P\***、**R\***、**V\***、Web **W\*** 区分。  
**终点**：达到可决定是否立项 **N6** 的成熟度；**本轨不写 OMS / Tick 仓 / 全站 SPA**。

---

## 0. 一句话目标

```text
成功画像（S 轨终点）
  · 财务可用日（公告日优先）+ 日线 as_of 无未来泄漏可复现；demo ≠ 已验证
  · 因子证据以「池内日频截面 IC」为主叙事；单票 IC / 池 OLS 为探针
  · 参数网格带试验次数与 OOS 纪律；验证包含暴露 / 拟合 / 样本纪律
  · 组合对照含 qp_lite（可选）；风控 outcome 可巡检
  · 成熟闸门硬项可评估；通过后才允许另开 N6 文档
```

---

## 1. 为何还要 S 轨（相对 V 轨）

| V 轨已交付 | S 轨补什么 |
|------------|------------|
| 日线 PIT、财务 history 最小路径、maturity-gate API | **公告日可用**、IC/批量路径 PIT 一致、promote 拒 demo |
| 池 **score** 截面 IC、单票因子 IC、中性化 lite | **因子级截面 IC**（Pearson + Spearman）+ 枢纽入口 |
| param-grid / validation-pack / WF | 包内 **暴露**、**A/B 对照指纹**、网格试验次数可见 |
| exposure / outcome / risk_parity_lite / qp_lite | 对照模式含 qp_lite；闸门项对齐 S 验收 |
| §11 清单 API | 运营可勾选；文档收口为「现行下一程 = S」 |

**不进 S 轨**：OMS、Level2、完整 Barra、NN→生产 score、静默写 `signal_config`、Bloomberg 密度 UI。

---

## 2. 锁定原则

| # | 原则 |
|---|------|
| 1 | 阶段诚实：策略验证；N6 须过闸门后另立项 |
| 2 | 生产 Alpha = 线性可解释；研究可加深，promote 人审 |
| 3 | LLM 不改 `score` / `stance_label` |
| 4 | 先可信（PIT/样本）再变厚（QP/大因子库） |
| 5 | 研究 / 回测 / 纸面同源 `score_bars` + DataService |
| 6 | 每阶段 ≥1 API/落盘 + ≥1 Web 可见 + 测试 |
| 7 | UI 遵守 [quant-ui-standard](../quant-ui-standard.md) |
| 8 | demo / synthetic 可识别，不得当「已验证」 |

---

## 3. 阶段总览（S0–S4）

```text
S0  样本与 PIT 变真
        │
        ▼
S1  截面研究范式（因子级 CS IC）
        │
        ▼
S2  过拟合纪律与验证包
        │
        ▼
S3  组合 / 风控稳态加深
        │
        ▼
S4  成熟闸门收口 →（过）N6 备忘 /（不过）停
```

| 阶段 | 主题 | 主抬柱 | 状态 |
|------|------|--------|------|
| **S0** | PIT / 公告日 / demo 拒晋升 / IC 路径 PIT | 拟合 | **已落地** |
| **S1** | 因子截面 IC · Spearman · 枢纽按钮 | 收益质量 | **已落地** |
| **S2** | 验证包暴露 · A/B 指纹 · 网格试验次数 | 速度 · 拟合 | **已落地** |
| **S3** | weight 对照含 qp_lite · 暴露进包 | 风控 | **已落地** |
| **S4** | 闸门 S 项 · 文档收口 · 回归测 | 三项可回归 | **已落地** |

---

## 4. S0 · 样本与 PIT 变真

| ID | 交付 | 落点 | 验收 |
|----|------|------|------|
| S0.1 | 财务点按 **可用日**（`ann_date` / `available_as_of` 优先，否则 `as_of`）截断 | `core/fundamentals_pit.py` | 报告期晚于决策日、公告日已过 → 可选入；单测 |
| S0.2 | 池截面 IC 每日 `resolve_fundamentals_for_score(as_of=date)` | `core/backtest/pool_ic.py` | `pit_fundamentals=true` 元数据 |
| S0.3 | 权重 promote 在 synthetic 主导时硬拒（除非 `allow_demo=true`） | ~~`signal_config_draft`~~ → 研究枢纽 / ReturnScoreModel 启用闸门 | 返回明确 error |
| S0.4 | 闸门项保留 real_multi / demo_discipline | `maturity_gate` | 已有 + S4 扩展 |

---

## 5. S1 · 截面研究范式

| ID | 交付 | 落点 | 验收 |
|----|------|------|------|
| S1.1 | 池内 **逐因子** 日频截面 IC（Pearson + Spearman） | `core/backtest/factor_cs_ic.py` | 单测合成面板 |
| S1.2 | `POST /api/quant/factor-cs-ic` | `quant_service` · `routers/quant` | 200 + factors[] |
| S1.3 | 研究枢纽「截面 IC」显式按钮 + 摘要 | `quant_panel` · `quant.js` | 不自动写权 |
| S1.4 | 说明：单票 IC ≠ 截面；OLS ≠ FM | help 折叠 | 文案可见 |

---

## 6. S2 · 过拟合纪律与验证包

| ID | 交付 | 落点 | 验收 |
|----|------|------|------|
| S2.1 | validation pack 含 `exposure` + `risk_blocks` 摘要 | `validation_pack.py` | 字段存在 |
| S2.2 | A/B 对照指纹（两配置 metrics 并排） | `core/ab_compare.py` · API | fingerprint 不同则可见 |
| S2.3 | param-grid 响应含 `trial_count` / 多重检验提示 | `run_param_grid` | UI/API 可见 |
| S2.4 | 网格「应用最优」仍受 OOS 门禁 | 已有 `paramGridApplyGate` | 保持 |

---

## 7. S3 · 组合与风控稳态

| ID | 交付 | 落点 | 验收 |
|----|------|------|------|
| S3.1 | `compare_weight_modes` 含 `qp_lite` | `weight_mode_compare.py` | modes 含键；无 cvxpy 时 unavailable |
| S3.2 | 验证包 / 平台可看暴露摘要 | pack + 既有 exposure API | 同卡可读 |
| S3.3 | outcome 标注路径保持 | `block_outcome` | 闸门 labeled 阈值 |

---

## 8. S4 · 成熟闸门收口

| ID | 交付 | 落点 | 验收 |
|----|------|------|------|
| S4.1 | 闸门增加 S 轨项：`factor_cs_ic_available`、`validation_pack_shape` | `maturity_gate.py` | soft/hard 分列 |
| S4.2 | `tests/test_s_track.py` 覆盖 S0–S4 主干 | tests | unittest 绿 |
| S4.3 | design-spine / README「现行下一程」指向本文 | docs | 链接正确 |
| S4.4 | N6 仍仅备忘 | `n6-live-gate.md` | 不写 OMS |

---

## 9. 需求拷问（进排期门禁）

1. 是否降低前视 / 源不一致？  
2. 是否让回测–纸面落差可解释？  
3. 是否抬高可审计 Alpha（截面证据）？  
4. 是否压缩 Idea→可复现验证摩擦？  
5. 是否让风控拦截可审计？  

答不上 → 不进 S 轨。仅为「以后下真单方便」→ 记 N6 备忘。

---

## 10. 与相关文档分工

| 文档 | 职责 |
|------|------|
| [design-spine.md](../design-spine.md) | 北极星 · 能力地图 · 阶段边界 |
| [strategy-validation-upgrade.md](strategy-validation-upgrade.md) | **已收口** V0–V5 |
| [upgrade-refactor-plan.md](upgrade-refactor-plan.md) | **已收口** R0–R5 |
| **本文** | **现行下一程 S0–S4**（深化补强） |
| [n6-live-gate.md](../n6-live-gate.md) | 闸门通过后的实盘立项备忘 |

---

## 11. 落地清单（代码索引）

| 模块 | 路径 |
|------|------|
| 财务可用日 PIT | `core/fundamentals_pit.py` |
| 池 score IC + PIT | `core/backtest/pool_ic.py` |
| 因子截面 IC | `core/backtest/factor_cs_ic.py` |
| promote 拒 demo | 研究枢纽启用闸门 / ReturnScoreModel（原 `signal_config_draft` 已删） |
| A/B 对照 | `core/ab_compare.py` |
| 验证包 | `core/validation_pack.py` |
| 权重模式对照 | `core/weight_mode_compare.py` |
| 闸门 | `core/maturity_gate.py` |
| API | `web/routers/quant.py` · `platform` |
| UI | `web/static/partials/quant_panel.html` · `js/quant.js` |
| 测 | `tests/test_s_track.py` |

---

## 12. 最终验收

- [x] S0–S4 代码与 API 可演示  
- [x] `python -m unittest tests.test_s_track` 通过  
- [x] 枢纽可跑「截面 IC」且不写 config  
- [x] 成熟闸门可读 S 项；`ready_for_n6_review` 仍依赖真实样本运营  
- [ ] （运营）观察池 `real_multi_coverage≥0.5` + 纸面日更样本 — **人工持续**，非一次 commit 可永久绿  

**现行不进入 N6 OMS。**
