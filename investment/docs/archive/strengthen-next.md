# 下一程深化补强（C / P / EP）· 已收口

[← 文档索引](../README.md) · **现行下一程** → [yhat-strengthen.md](yhat-strengthen.md)（Y0–Y5）· 产品主轴 [design-spine.md](../design-spine.md) · D/S/E · N6 [n6-live-gate.md](../n6-live-gate.md)

**规划日期**：2026-08-03（收口）· **后继**：2026-08-05 起见 [yhat-strengthen.md](yhat-strengthen.md)  
**定位**：相对专业量化差距分析后的一程（**已收口**）。本轨补**口径可信（C）→ 组合可解释（P）→ 分组启用证据包（EP）**。**不写 OMS**。

---

## 0. 一句话目标

```text
成功画像
  · 组内 Top-N ≠ 账户 TopK；卖出/预演文案与分池簿一致；三页 score 同源可核验
  · 调仓有换手上限 + 单票/行业预算执行（不只检查）+ 一页简化归因
  · 「对照→启用」强制或强提示附带证据包（簿长、Top-N、OOS、换手估计、暴露）
```

---

## 1. 背景：已收口 vs 仍缺

| 轨 | 状态 | 说明 |
|----|------|------|
| **D0–D4** | ✅ 已落地 | ann / source_audit / 复权 / 日历 / DQ |
| **S0–S4** | ✅ 已落地 | PIT / CS IC / 验证包 / qp_lite 对照 / 闸门 |
| **E0–E4** | ✅ 已落地 | 策略 KPI · 日期对齐 · IC PIT · 日历 · outcome |
| **分组 live** | ✅ 主干 | OLS β 分组 · 组权 · 组内 Top-N=10 · 交易/观察同源 score |
| **仍缺（本轨）** | 推进中 | 口径漂移 · 组合启发式过薄 · 启用缺强制证据包 |

**边界外（不做）**：OMS / Tick / 完整 Barra / 静默写 `signal_config.weights` / 全市场数仓迁库。

---

## 2. 总览

```text
C0  口径纠偏（文案 · 状态条 · Top-N 语义）
        │
        ▼
P0  调仓换手可见 + 上限
        │
        ▼
P1  风险预算进分池落账（单票/行业）
        │
        ▼
P2  一页简化归因
        │
        ▼
EP1 启用证据包（对照→启用挂载）
        │
        ▼
N6 闸门评估（另文档，本轨不写 OMS）
```

| 阶段 | 主题 | 状态 |
|------|------|------|
| **C0** | 口径纠偏 | **已落地**（2026-08-03） |
| **P0** | 换手可见 + 上限 | **已落地**（2026-08-03） |
| **P1** | 风险预算执行 | **已落地**（2026-08-03） |
| **P2** | 简化归因 | **已落地**（2026-08-03） |
| **EP1** | 分组启用证据包 | **已落地**（2026-08-03） |

---

## 3. C0 · 口径纠偏

**为何先做**：组内 Top-10 与「TopK 之外」文案曾混淆；研究 / 观察 / 交易若数字不一致会直接打击信任。

| ID | 交付 | 落点 |
|----|------|------|
| C0.1 | 分池卖出 reason =「不在分池目标簿（组内 Top-N…）」 | `paper_rebalance.py`（已有）· UI tip |
| C0.2 | 交易页状态条统一：mode · 映射版本 · **组内 Top-N** · 簿长 | `paper.js` · `cluster_status_public` |
| C0.3 | 观察页摘要加载后提示 score 权重模式（active/组权） | `quant.js` fillWatchingInsights |
| C0.4 | 分池预演请求勿硬编码 `top_k=10`；按簿长持有 | `paper.js` runClusterPaperRebalance |
| C0.5 | 文档写死：Top-N=每组相对序；账户目标=合并簿 | 本文 + `weight-suggest-deepen.md` 一句 |

**验收**

- [x] active 时预演卖出原因不含误导性「横截面 TopK」（分池路径）
- [x] 交易页状态可见「组内 Top-N=… · 簿 N 只」
- [x] 观察摘要状态含权重模式
- [x] 分池预演不硬编码 `top_k=10`（按簿长）

---

## 4. P0–P2 · 组合可解释

| 阶段 | 交付 | 验收 |
|------|------|------|
| **P0** | 预演展示本轮换手（金额/只数）；可选 `max_turnover_pct` 软拦 | 调仓报告头可见换手 |
| **P1** | 单票/行业上限在分池买入路径缩量或跳过并写 reason | 超限有明确决策文案 |
| **P2** | 简化归因一页（选股 vs 配置 vs 残差，或暴露贡献） | 交易页或日报可看 |

### P0 落地说明（2026-08-03）

- 双边换手：`(买额+卖额)/2/净值*100`，挂在 `cash_impact.turnover_pct`
- 预演/落账 API（含分池）返回 `cash_impact` · `turnover`；交易页资金影响格展示换手
- 可选软上限：`paper.rules.max_turnover_pct`（别名 `max_turnover`）；超限跳过后续买入并记 `turnover_cap` 日志；默认不设则只展示不截断
- 建议纸面试值：`40`（激进对照可临时去掉）

### P1 落地说明（2026-08-03）

- `clip_buy_to_risk_budget`：按策略 `max_position_pct` / `max_sector_pct` 对拟买缩量；不足一手 → 跳过
- 已有持仓超限不再整批拦买（回撤仍硬拦）；逐笔预算 + 报告行决策「跳过」+ reason
- 缩量买入 note 带「单票/行业预算缩量…」；API 返回 `risk_budget_skips`
- **分池**：合并簿=目标集；`optimize` 未分配权重**不得**整票跳过（仅横截面筛目标仓）

### P2 落地说明（2026-08-03）

- `build_paper_attribution_lite`：持仓 MTM 市值权重 → 选股 / 配置 / 残差（Brinson lite）
- 挂 `ops_report.attribution`；调仓/日更响应顶层 `attribution`；交易页五问格「简化归因」
- 非完整因子 / 无外部行业指数（基准收益=组合加权收益）

完整协方差模型不进本轨。

---

## 5. EP1 · 分组启用证据包

与历史 **E 轨**（北极星证据）区分：本包专指 **cluster 对照→启用**。

| 字段 | 含义 |
|------|------|
| `top_n_per_group` / `max_names` / `name_count` | 簿构造 |
| `cluster_version` / `mode` | 映射版本 |
| `oos_summary` | 组 OOS 通过数 / 失败数 |
| `turnover_est` | 相对当前纸面 would_sell/buy |
| `exposure_summary` | 行业集中度简表 |
| `score_audit_sample` | 已有双分样本 |

**已落地（2026-08-03）**：`build_cluster_enable_evidence` → `cluster_status_public.enable_evidence`；研究枢纽落地卡折叠展示；`② 启用` 确认框带摘要；`set_cluster_scoring_mode(active)` 校验证据包门禁（健康不过或 OOS 全败则禁）。

---

## 6. 原则

1. 仍经 DataService / ports；LLM 不改 `score` / `stance_label`  
2. 每阶段 ≥1 API 或落盘字段 + ≥1 Web 可见 + ≥1 测试  
3. 先 C0 再 P/EP；不扩大因子库抢戏  
4. UI 遵守 [quant-ui-standard.md](../quant-ui-standard.md)；改静态资源 bump `ASSET_V`  

---

## 7. 与专业差距对照（本轨覆盖）

| 专业差距 | 本轨动作 |
|----------|----------|
| 假专业 / 口径漂移 | **C0** |
| 组合启发式、无换手/预算 | **P0–P1** |
| 缺系统化归因 | **P2** |
| promote 缺强制证据 | **EP1** |
| 数据 PIT / 研究闸门 | 已由 D/S/E 覆盖，本轨只消费 |

---

## 8. 建议节奏

| 周次 | 内容 |
|------|------|
| W1 | **C0** 收口 |
| W2 | **P0** + EP1 字段草表落地 API |
| W3 | **P1** |
| W4 | **P2** + EP1 UI 挂启用 |

---

## 9. 相关代码索引（现行）

| 主题 | 路径 |
|------|------|
| 组内 Top-N 合并簿 | `core/signal/cluster_rank.py` · `cluster_pool_merge.py` |
| 分池调仓 | `services/paper_trades.py` · `core/paper_rebalance.py` |
| 换手 / 软上限 | `paper_rebalance.compute_turnover_stats` · `rules.max_turnover_pct` |
| 买入风险预算 | `risk.budget.clip_buy_to_risk_budget` · `risk_budget_skips` |
| 纸面简化归因 | `core/paper_attribution.py` · `ops_report.attribution` |
| 启用证据包 | `cluster_live.build_cluster_enable_evidence` · `enable_evidence` |
| 状态 API | `cluster_live.cluster_status_public` |
| 观察 score | `core/watching_insights.py` |
| 评分悬浮 | `web/static/js/score_tooltip.js` |
