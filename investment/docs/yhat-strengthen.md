# ŷ 生产硬化深化补强（Y 轨）

[← 文档索引](README.md) · 产品主轴 [design-spine.md](design-spine.md) · 选股主轴 [weight-suggest-deepen.md](weight-suggest-deepen.md) · 上轮下一程 [strengthen-next.md](strengthen-next.md)（C/P/EP **已收口**）· D/S/E · N6 [n6-live-gate.md](n6-live-gate.md)

**规划日期**：2026-08-05 · **落地**：2026-08-05  
**定位**：在 R/V/D/S/E 与 C/P/EP、分组 live（L0–L4）已落地之后，把 **predicted_score（ŷ）** 从「能跑的主轴」升到「**可严肃验证、可辩护、可衰减监控**」的生产选股路径。  
**命名**：**Y** = Ŷ / Yield-model Harden；与历史 P/R/V/S/D/E/C/P/EP 区分。  
**终点**：抬高纸面夏普可信度 × 回测–纸面拟合可解释性 × ŷ 衰减可发现；逼近 N6 闸门评估。**本轨不写 OMS / Tick / 全市场数仓 / NN→生产分**。

### 落地索引（2026-08-05）

| 阶段 | 关键落点 |
|------|----------|
| Y0 | `POST /api/signal/config/scoring` · 策略中心门槛表单 · 滞回卖出（既有） |
| Y1 | `estimate_yhat_ic` · `assess_cluster_live_health` 重拟合提示 · OOS 失败率 / ŷ IC 证据包 |
| Y2 | `_buy_match_block_reason` · `cost_assumptions` 挂调仓响应 · fit-gap 成本不一致（既有） |
| Y3 | `weight_mode`→optimize · `exposure_style` · 行业 map 覆盖警告 |
| Y4 | `maturity_gate` y_track 项 · validation_pack v3 + cluster_fingerprint |
| Y5 | 策略页链到平台样本/闸门 · `paper_daily_streak` 软项 |
| **Y-S** | 双标尺债：`resolve_optimize_score_floor` · promote/apply 剥离 0–100 · feedback→scoring · ports 分钟/财务 |

---

## 0. 一句话目标

```text
成功画像（Y 轨终点）
  · 买卖门槛、策略卡、调仓文案只认 ŷ% 滞回；0–100 规则分不再冒充 live 门槛
  · 组 β / ŷ 有滚动衰减监控与再训/刷新纪律；陈旧不只靠手动点「刷新簿」
  · 分池调仓成本与撮合假设可对照回测；落差有一页归因
  · ŷ 路径有最小拟合回归套件（回测↔纸面）；成熟闸门含 ŷ 硬项
  · 观察池财务覆盖与 paper_daily 样本连续可巡检（运营债可见）
```

---

## 1. 背景：已收口 vs 仍缺

| 轨 / 能力 | 状态 | 说明 |
|-----------|------|------|
| **D / S / E** | ✅ | 数据 PIT · 验证包 · 北极星证据诚实 |
| **C / P / EP** | ✅ | 口径 · 换手/预算 · 启用证据包 |
| **L0–L4 + P11** | ✅ | 组 OLS β → ŷ · live · 分池簿 · 滞回卖出（ŷ&lt;hold） |
| **仍缺（本轨）** | 规划中 | 配置双轨残留 · ŷ 衰减纪律 · 成本撮合加深 · 拟合回归 · 样本运营 |

### 相对专业：本轨覆盖的路径内缺口

| 优先级 | 缺陷 | 污染什么 | Y 阶段 |
|--------|------|----------|--------|
| **P0** | Spec/纸面仍露 0–100 门槛；与 ŷ 滞回并存 | 人审误操作 · 假专业 | **Y0** |
| **P1** | ŷ / 组 β 衰减难发现；刷新靠人 | 纸面夏普不可持续 | **Y1** |
| **P2** | 冲击/涨跌停/流动性仍偏简 | 回测–纸面落差不可解释 | **Y2** |
| **P3** | 组合仍偏启发式；风格·Beta 浅 | 集中风险「看起来有」 | **Y3** |
| **P4** | ŷ 路径缺自动拟合回归 | 闸门靠感觉 | **Y4** |
| **P5** | 财务空值 / 日更薄 / outcome 未标 | 验证样本不诚实 | **Y5** |

**边界外（不进 Y 轨）**：OMS、Level2、完整 Barra、NN→生产 `score`、静默写 `signal_config.weights`、全市场 Tick 仓。

---

## 2. 锁定原则

| # | 原则 |
|---|------|
| 1 | 生产选股真源 = **组/全局 `ReturnScoreModel` → ŷ%**；heuristic 仅研究 OOS |
| 2 | 滞回：买入/入簿 `min_predicted_score`；卖出 **仅** `ŷ < min_hold_predicted_score`；中间带持有 |
| 3 | 永不静默写 `signal_config.weights`；live 经研究枢纽人审 |
| 4 | LLM 不改 `score` / `stance_label` / ŷ |
| 5 | 先可信（口径·衰减·拟合）再变厚（QP·大因子库） |
| 6 | 每阶段 ≥1 API/落盘 + ≥1 Web 可见 + ≥1 测试；改静态 bump `ASSET_V` |
| 7 | UI 遵守 [quant-ui-standard.md](quant-ui-standard.md) |

---

## 3. 阶段总览（Y0–Y5）

```text
Y0  配置与口径单真源（ŷ 滞回）
        │
        ▼
Y1  ŷ / 组 β 衰减与再训纪律
        │
        ▼
Y2  成本 · 撮合诚实度（ŷ 路径）
        │
        ▼
Y3  组合暴露加深（可选 QP）
        │
        ▼
Y4  ŷ 拟合回归套件 + 闸门项
        │
        ▼
Y5  样本 / 运营债收口
        │
        ✕  现行不进入：N6 OMS（另立项）
```

| 阶段 | 主题 | 主抬柱 | 建议周期 | 状态 |
|------|------|--------|----------|------|
| **Y0** | 配置口径单真源 | 信任 · 防误操作 | 3–5 日 | **已落地** |
| **Y1** | 衰减监控与再训 | 收益质量 | 1–1.5 周 | **已落地** |
| **Y2** | 成本撮合 | 拟合 | 1–1.5 周 | **已落地** |
| **Y3** | 组合暴露 / QP lite | 风控 · 收益 | 1 周 | **已落地** |
| **Y4** | 拟合回归 + 闸门 | 三项可回归 | 1 周 | **已落地** |
| **Y5** | 样本运营 | 证据诚实 | 并行 / 穿插 | **已落地** |

---

## 4. Y0 · 配置与口径单真源

**为何先做**：StrategySpec / `paper.rules` 仍带 0–100 `min_score` 等；live 已用 ŷ±1% 滞回。双轨并存会再次出现「正分被清仓 / 门槛误解」。

| ID | 交付 | 落点建议 | 验收 |
|----|------|----------|------|
| Y0.1 | 策略中心 / `GET /api/signal/config`（或 strategies）**可编辑或至少显式展示** ŷ 买卖门槛 | `signal_config.scoring` · 策略页脚已有只读 → 加平台或策略折叠表单（人审写盘，不静默） | Web 改 +1/−1 可见且 reload 生效 |
| Y0.2 | 策略卡 **不再**把 `params.min_score`（0–100）标成 live 门槛；描述与 promote 文案标明「限额/持有期」 | `strategies.py` · `quant.js` renderStrategyList | 卡片无「门槛 65」误读 |
| Y0.3 | `paper.rules` 的 `min_score`/`min_hold_score` 在 **predicted_score 路径**标注 legacy；`resolve_buy_floor` / `resolve_hold_floor` 为唯一调仓门 | `paper_exec` · `paper_rebalance` · `score_display` | 单测：cluster 卖出仅 hold 门槛 |
| Y0.4 | 调仓 UI tip / 原因串与滞回一致（已部分改；扫残留「不在簿即卖」） | `paper.js` · `paper_trades.py` | 中间带持仓预演 = 持有 |
| Y0.5 | 文档：`weight-suggest-deepen` · `strategy-layer` · 本页交叉引用 | docs | 无「簿外必卖」旧句 |

---

## 5. Y1 · ŷ / 组 β 衰减与再训纪律

| ID | 交付 | 落点建议 | 验收 |
|----|------|----------|------|
| Y1.1 | 滚动 **池 ŷ IC / 分层收益** 进 `strategy_monitor` 或独立 `yhat_monitor`；告警出站 | `core/strategy_monitor.py` · alert | 日更后可见衰减告警 |
| Y1.2 | live 映射 **fitted_as_of / 样本数 / ridge** 健康项加深；超龄建议「跑分组→对照」不只 shadow 降级 | `cluster_live.assess_cluster_live_health` | 研究枢纽落地卡提示「建议重拟合」 |
| Y1.3 | 纸面日更 `prepare_cluster_for_daily`：shadow|active 时 **自动 refresh-book**（已有）+ 可选「IC 崩塌 → 禁主动启用」软闸 | `cluster_live` · maturity/enable_evidence | 证据包含 rolling_ic 摘要 |
| Y1.4 | 组内 OOS 失败率阈值进启用门禁（EP1 已有全败禁；改为可配容忍） | `set_cluster_scoring_mode` | 配置项可测 |

---

## 6. Y2 · 成本 · 撮合诚实度（ŷ 路径）

| ID | 交付 | 落点建议 | 验收 |
|----|------|----------|------|
| Y2.1 | 分池预演/落账默认挂 **cost_model 版本 + 双边换手**（P0 已有换手）+ 冲击/滑点假设摘要 | `ops_report` · 交易页五问 | 与回测页成本假设可对照 |
| Y2.2 | 涨跌停 / 停牌：拟买无价或一字板 → 跳过并写 reason（最小纪律，不造全日停牌库） | `paper_rebalance` · calendar halt_hint | 单测假报价 |
| Y2.3 | 回测 Top-K（ŷ 模式）与纸面分池 **同一 cost_model 默认**；落差页标「成本假设不一致」 | `quant` fit-gap · replay | 不一致时 UI 警告 |

---

## 7. Y3 · 组合暴露加深

| ID | 交付 | 落点建议 | 验收 |
|----|------|----------|------|
| Y3.1 | 分池目标簿 + 持仓 **风格/规模暴露简表**（lite，非完整 Barra） | `exposure` · 策略中心风控折叠 | 一页可见 |
| Y3.2 | 调仓 optimize 默认可选 `qp_lite`（S3 已有对照）；active 路径可开关 | `portfolio_optimize` · paper rules | 开关可测 |
| Y3.3 | 行业 map 覆盖率 &lt; 阈值 → 启用证据包 warning（不硬拦） | `enable_evidence` | 落地卡提示 |

完整协方差 / 实时 VaR **不进本阶段**（记 N6 或后续 Z 轨）。

---

## 8. Y4 · ŷ 拟合回归套件 + 闸门

| ID | 交付 | 落点建议 | 验收 |
|----|------|----------|------|
| Y4.1 | `evals` 或 CLI：固定夹具上 **ŷ 回测 vs 纸面预演** 相关/方向差回归 | `evals/` · `research/` | CI 或可一键跑 |
| Y4.2 | `maturity_gate` 增硬/软项：ŷ live 健康、滞回配置存在、rolling_ic 可读、策略仓夏普 scope | `maturity_gate` | API 可查 |
| Y4.3 | 验证包默认带 `rank_mode=predicted_score` + cluster 指纹 | validation-pack | 导出可复现 |

---

## 9. Y5 · 样本 / 运营债（并行）

| ID | 交付 | 落点建议 | 验收 |
|----|------|----------|------|
| Y5.1 | DQ / sample-status：观察池财务多期覆盖、空 fundamentals 清单 | `/api/ops/data-quality` | 平台页一键可见 |
| Y5.2 | `paper_daily` 连续交易日计数进北极星/闸门软项 | north_star · maturity | 不足则「拟合仍空」可解释 |
| Y5.3 | 风控 outcome 未标注催办（E4 已有）→ 策略中心或平台醒目入口 | sample-status | 点击可达 |

---

## 10. 建议节奏

| 周次 | 内容 |
|------|------|
| **W1** | **Y0** 全部（口径与门槛 UI） |
| **W2** | **Y1.1–Y1.2** + **Y5.1** 穿插 |
| **W3** | **Y1.3–Y1.4** + **Y2.1–Y2.2** |
| **W4** | **Y2.3** + **Y3** |
| **W5** | **Y4** + **Y5.2–Y5.3** + 文档收口 |

过闸后：评估 [n6-live-gate.md](n6-live-gate.md)；**不过则停在策略验证加深，不接 OMS**。

---

## 11. 需求拷问（进排期门禁）

任一工作包须能答其一，否则砍掉：

1. 是否减少 **门槛/口径误解** 导致的错误清仓或错误买入？  
2. 是否让 **ŷ 衰减** 可被发现并阻止坏 live？  
3. 是否让 **回测–纸面落差** 更可解释？  
4. 是否抬高 **组合暴露** 的可审计性（非只调参好看）？  
5. 是否让 **样本/日更** 不足以「假装已验证」？  

答不上 → 不进 Y 轨。若答案仅为「方便以后下真单」→ 记 N6，本轨不做。

---

## 12. 相关代码索引（起点）

| 主题 | 路径 |
|------|------|
| ŷ 门槛 | `score_display.py` · `signal_config.json` · `config.py` |
| 分池滞回卖出 | `paper_rebalance.simulate_cross_section_rebalance` |
| live / 刷新簿 | `cluster_live.py` · `cluster_rank.py` |
| 启用证据 | `build_cluster_enable_evidence` |
| 监控 | `strategy_monitor.py` |
| 成本 | `paper_costs` · `backtest/costs.py` |
| 闸门 | `maturity_gate` |
| 策略中心 | `strategy_panel.html` · `quant.js` loadStrategyList |

---

## 13. 文档关系

| 文档 | 职责 |
|------|------|
| [weight-suggest-deepen.md](weight-suggest-deepen.md) | 选股主轴 **已落地** 叙事（β → ŷ） |
| [strengthen-next.md](strengthen-next.md) | C/P/EP **已收口**；不再当现行下一程 |
| **本文** | **现行下一程 Y0–Y5** |
| [n6-live-gate.md](n6-live-gate.md) | 验证成熟后实盘立项备忘 |
