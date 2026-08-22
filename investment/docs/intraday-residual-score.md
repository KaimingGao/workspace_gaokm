# 盘中剩余收益头（Intraday Residual Score）方案

[← 文档索引](README.md) · ŷ 主链 [predicted-score-chain.md](predicted-score-chain.md) · 主轴 [design-spine.md](design-spine.md) · 舆情旁路 [sentiment-layer.md](sentiment-layer.md) · 复盘 [score-review.md](score-review.md) · **τ 契约升级** [tau-contract-and-partition-upgrade.md](tau-contract-and-partition-upgrade.md)

本文定义：**在不动乱日线 ŷ 主轴的前提下，如何引入 \(T\) 日实时信息**，以及如何用历史数据拟合、分阶段落地。  
动机来自典型 miss：日线 ŷ（\(T\!-\!1\) 因子）为负或接近 0，但 \(T\) 日主题脉冲大幅上涨——残差主因是 **信息集外冲击**，不是单纯「不该拿今日涨跌对账」。

**状态**：方案已定稿，**P0～P2 研究轨已接线**；**Nowcast/Kalman 顺序滤波已落地**（EOD→open→可选分钟 τ；主题/缺口放大 \(Q\)；影子 `predicted_score_nowcast`，默认不改主排序）。刷簿 Z 齐套 / rem 满池 / 主题分层 OOS / 启用后轻量刷簿；分钟 τ 与 `w_mode` / cascade 影子默认关或 fixed，人审后开。  
**双层 ŷ**：契约 + blend + A2 影子簿已进代码；rem schema `rem_ridge_v6`（含 `oos.by_theme` / `residual_var`）。分钟 τ：`enable_minute_tau`（默认关）。A3 主排序仍待影子簿验收。P3 bandit 未做。  
**诚实边界**：主排序仍为 EOD；τ 层独立头 + 决策闸，不揉改 `predicted_score`。升级顺序见 [tau-contract-and-partition-upgrade.md](tau-contract-and-partition-upgrade.md) 与 [predicted-score-chain.md §2.5](predicted-score-chain.md)。

---

## 1. 一句话

```text
【双层 predicted_score】
日线 ŷ_EOD = predicted_score：     F_{T-1} → close[t+h]/close[t]-1     （生产排序主轴）
当日 ŷ_τ   = predicted_score_tau： F_τ     → close[T]/price[τ]-1       （独立头；雏形 score_rem）
事件先验 E_τ：缺口 / 板块广度 / 舆情 → 只改动作，不改 ŷ_EOD
```

禁止：把 \(\tau\) 之后才知道的价格或新闻塞进 \(\mathcal{F}_\tau\)；禁止用「含已实现涨幅的全日收益」去验收「已含盘中特征」的模型；禁止两套标签揉进同一 `predicted_score` 字段。

**双层字段、训练与融合阶梯**的规范表述见 [predicted-score-chain.md §2.5](predicted-score-chain.md) 与 [tau-contract-and-partition-upgrade.md §9](tau-contract-and-partition-upgrade.md)。**\(y_{\mathrm{EOD}}\)·\(y_\tau\)·\(y_{\mathrm{ON}}\) 预估周期**见 [predicted-score-chain.md §2.6](predicted-score-chain.md)。

---

## 2. 问题与口径

### 2.1 现网日线契约（保持）

| 符号 | 含义 |
|------|------|
| 决策日 / 因子截止 | 通常 \(T\!-\!1\)（盘中无完整 \(T\) 日线时） |
| 生产标签（live 组 β 常见） | \(y=\bigl(\mathrm{close}[t+h]/\mathrm{close}[t]-1\bigr)\times 100\)，当前 promote 产物多为 **\(h=1\)** |
| 输出 | `predicted_score` / `score`（ŷ%） |
| 配置 | `signal_config.scoring.horizon_days` 须与组 `return_model.horizon_days` **一致**（P0 已收紧） |

在 \(h=1\) 且因子停在 \(T\!-\!1\) 时，用 **今日涨跌对账 ŷ** 是合理审计；异常在于 **残差极大**（主题日可达数个点以上），而非「不该比」。

### 2.2 巨大残差的两层原因

| 层 | 作用 | 例 |
|----|------|-----|
| A. 日线建模 / 错误组 β | 让 ŷ **符号或幅度偏错** | 组内 RS/动量 β 为负；OOS 失败组仍 active |
| B. \(T\) 日实时信息未入模 | 解释 **大部分大幅残差** | 主题共振、开盘缺口后的盘中延续 |

A 用分组门禁与重聚类处理；B 用本方案的 **事件先验 + 剩余收益头** 处理。二者叠加，不是单选。

### 2.3 三个必须对齐的符号

| 符号 | 含义 |
|------|------|
| \(\tau\) | 决策时刻（何时算分、何时允许调仓意图） |
| \(\mathcal{F}_\tau\) | \(\tau\) 及以前可观测信息 |
| \(y(\tau)\) | 从 \(\tau\) 起要预测的收益 |

契约：\(\hat y(\tau)=f(\mathcal{F}_\tau)\) 只预测 \(y(\tau)\)。扩展实时信号 = 扩展 \(\mathcal{F}_\tau\)，通常必须同步改 \(y\) 或改「输出用途」（排序 vs 动作）。

---

## 3. 产品能力定义（三选一主轴）

### 方案 A — 双层：日线 ŷ 不变 + 实时只作策略（现行主路径）

| | 定义 |
|--|------|
| 输入 | \(T\!-\!1\) 日线 → ŷ；开盘后另算 \(E_\tau\) |
| 输出 | **排序轴仍是 ŷ**；\(E\) 只产出 soft hold / 缩放 / 告警 |
| 预估目标 | 仍是 \(T\!-\!1\to T\) 收盘收益 |
| 对账 | 不对账「ŷ≈今日涨跌」为唯一 KPI；对账「有 \(E\) 时是否少做反」 |

### 方案 B — 盘中重定义：\(\tau\) 预测剩余路径

| | 定义 |
|--|------|
| 输入 \(\mathcal{F}_\tau\) | 日线因子(\(\le T\!-\!1\)) ∪ \(T\) 日截至 \(\tau\) 的价量/板块/新闻 |
| 输出 | \(\hat y(\tau)\) |
| 目标 | \(y(\tau)=\mathrm{close}[T]/\mathrm{price}[\tau]-1\) |

### 方案 C — 跨日开盘头 + 盘中头（规划）

| 头 | 目标 | 状态 |
|----|------|------|
| \(\hat y_{\mathrm{ON}}\) | \(\mathrm{open}[t]/\mathrm{open}[t-1]-1\) | `predicted_score_on`；见 [predicted-score-chain.md §2.6](predicted-score-chain.md) |
| \(\hat y_{ID}(\tau)\) | \(\mathrm{close}[T]/\mathrm{price}[\tau]-1\) | 接近现网 rem / \(\hat y_\tau\) |

**注意**：现网 **`gap_pct`** = \(\mathrm{open}[t]/\mathrm{close}[t-1]-1\)（收→开**已实现**缺口），**不是** \(y_{\mathrm{ON}}\)。

**本仓库选定路径：**

1. **生产主轴长期保持方案 A 的 ŷ_EOD**（可审计、与账本/分组兼容）。  
2. **研究与增强走「残差头」**（结构上接近 B 的 \(y(\tau)\)，但不替换 EOD 主字段）。  
3. **\(y_{\mathrm{EOD}}\) · \(y_\tau\) · \(y_{\mathrm{ON}}\)** 组成 open 锚 **预估周期**（与 EOD 收锚并行）；§2.6 有图。\(y_{\mathrm{ON}}\) 落地前仅文档契约，**不**把分钟特征直接塞进 `predicted_score`。

```text
【产品叙事 · 双层 predicted_score】
predicted_score          = ŷ_EOD = 隔夜截面吸引力（EOD；主排序）
predicted_score_tau      = ŷ_τ   = 当日剩余收益头（规划名；现网雏形 score_rem / predicted_score_rem）
event_prior              = 动作约束（soft hold / warn；不改 ŷ_EOD）
禁止混用同一 score 字段不标注 as_of=τ / y_spec
融合：决策层闸门（F1）优先于加权合成（F2）；见 predicted-score-chain §2.5
```

---

## 4. 建模结构（推荐：残差头）

### 4.1 公式

\[
\begin{aligned}
\hat y_{\mathrm{EOD}} &= f_{\mathrm{daily}}(X_{T-1};\beta_{\mathrm{cluster}}) &&\text{现网组 β，promote 冻结} \\
y_{\mathrm{rem}}(\tau) &= \bigl(\mathrm{close}[T]/\mathrm{price}[\tau]-1\bigr)\times 100 \\
\hat y_{\mathrm{rem}}(\tau) &= g(X_{T-1},\, Z_{\le\tau};\gamma) &&\text{新拟合头}
\end{aligned}
\]

- **拟合对象优先只有 \(g\)**，日线 β 先不动 → 迁移成本可控。  
- \(Z_{\le\tau}\) 起步宜少：缺口%、open→τ 已实现、板块中位已实现、板块广度、（可选）舆情分。  
- \(g\) 起步 **Ridge/OLS**（与组 β 同构）；非线性留到样本与 OOS 稳定之后。

### 4.2 固定决策时刻（控制复杂度）

| 阶段 | \(\tau\) | 数据需求 |
|------|----------|----------|
| R0 | 开盘（`open`） | **仅日线**：\(y=\mathrm{close}/\mathrm{open}-1\)，\(Z\) 含缺口与同业开盘截面 |
| R1 | 开盘 + 09:45 | 分钟线或 5min 聚合 |
| R2 | + 10:30（可选） | 同上；禁止任意连续时点爆炸 |

### 4.3 事件先验 \(E\)（不进 ŷ）

与 [sentiment-layer.md](sentiment-layer.md) 同构：

```text
ŷ = ReturnScoreModel(...)           ← 唯一生产排序轴（EOD）
E = build_event_prior(gap, breadth) ← 先验
action = policy(ŷ, E, S)            ← soft_hold / warn；不改 ŷ
```

配置：`signal_config.event_prior`（`mode`: off|risk|gate；`gap_trigger_pct` 等）。  
实现：`core/event_prior.py`；纸面卖出滞回接入 `paper_rebalance`（低 ŷ 卖出在主题缺口日可 soft hold）。

### 4.4 合法 / 非法组合

| 输入 | 合法目标 | 非法 |
|------|----------|------|
| 仅 \(T\!-\!1\) 日线 | \(T\!-\!1\to T\) 收盘 | 用盘中已实现涨幅当「命中」却不改标签 |
| \(T\!-\!1\) + 开盘价 | 缺口；或 open→close | 用 10:00 后信息声称只预测缺口 |
| 截至 \(\tau\) 的分钟/板块 | \(\tau\to\) 收盘 | 全日相对昨收且特征已含大半当日涨幅（泄漏） |
| 新闻 | `publish_time ≤ τ` | 收盘后标题解释盘中 ŷ |

---

## 5. 历史拟合怎么做

### 5.1 面板行

每个 `(code, date=T, τ)` 一行：

```text
X     ← 日线因子，仅用 bars date ≤ T-1
Z     ← T 日、时间戳 ≤ τ 的缺口 / 分钟 / 板块 / 新闻
y_rem ← close[T] / price[τ] - 1
丢弃  ← 停牌、τ 无价、涨跌停不可成交（对齐 halt_policy）
```

### 5.2 流水线（对齐现网分组 OLS）

```text
历史日线 +（分钟或 open）
  → panel(code, date, τ, X, Z, y_rem)
  → 可选按 live cluster 分桶
  → Ridge: y_rem ~ zscore(X) + zscore(Z)
  → 时间切分 OOS（前段估 / 后段验；禁止打乱日期）
  → 指标：IC(ŷ_rem, y_rem)、方向命中、大残差日「少做反」
  → 人审 promote → 先门控，再考虑 rem 展示/排序
```

### 5.3 样本权（第二阶段）

- 近端提权；涨跌停降权或剔除。  
- **Regime**：用 **开盘可得** 的截面离散度 / 缺口中位数定义 `theme_day`，再降权或加交互；禁止用收盘大涨反标主题（泄漏）。

### 5.4 无分钟线时的诚实边界

只能拟合 **τ=open**（open→close）。  
盘中延续（缺口后继续冲）必须等分钟数据；在此之前用 event_prior 做动作保护，不假装 ŷ_EOD 能点出 +7%。

---

## 6. 与错误组 β、日线门禁的关系

实时方案 **不替代** 日线治理：

| 项 | 作用 | 状态 |
|----|------|------|
| `scoring.horizon_days` 与组模型一致 | 契约 | **P0 已落地**（默认 1；promote 不一致则拒） |
| OOS 失败组 | 固定：不进主簿；主分降为全局 ŷ / heuristic（组 ŷ 仅 `score_cluster`）；袖仓见 `rebalance_tracks` | **已固化** |
| 异质组重聚类 / 贪心换组 | 缓解错误组 β | 研究枢纽流程；按票重跑 |
| event_prior 缺口 soft hold | 主题日少因负 ŷ 卖掉 | **P1 已落地钩子** |
| ŷ_rem 研究面板 / live 门控 | open→close rem + `score_rem` 展示 | **R0/R0p/R3 已落地**（IC 仍弱） |

---

## 7. 分阶段落地规划

### 总原则

```text
先契约与门禁 → 事件动作层 → 可日线拟合的 rem 头 → 分钟 RS → regime → 是否进排序
每阶段：研究 OOS → 人审 → 再碰生产字段
```

### 阶段表

| 阶段 | 名称 | 交付 | 依赖 | 状态 |
|------|------|------|------|------|
| **P0** | 日线契约与坏组门禁 | `horizon_days=1` 对齐；promote 校验；OOS 失败组剔簿 + 主分降级 | 现网 cluster | **已落地** |
| **P1a** | 事件先验·缺口 | `event_prior`；纸面低 ŷ 卖出 soft hold | quote open + 昨收 | **已落地** |
| **P1b** | 事件先验·板块广度 | 持仓/候选开盘缺口截面 → theme | `compute_sector_gap_breadth_live` | **已落地** |
| **P1c** | 舆情对称门控 | `reduce_avoid_on_bullish` → soft_hold | `sentiment_prior` | **已落地** |
| **R0** | open→close 残差头（研究） | `rem_panel` + `rem_ridge` + `POST /api/quant/rem-ridge` | 日线 | **已落地** |
| **R0p** | rem 门控试点 | `rem_gate_enabled` + live `rem_ridge_model.json` | R0 persist | **已落地** |
| **R1** | τ=09:45 分钟 RS | `collect_rem_tau_panel`（有分钟缓存才有样本） | 分钟数据 | **已落地（数据条件）** |
| **R2** | Regime 加权 | `theme_day` + `theme_boost` 样本权 | R0 | **已落地** |
| **R3** | 展示字段 | `score_rem` / `predicted_score_rem` / `event_prior` 挂 `score_stock` | R0p | **已落地** |
| **N/A** | 替换 EOD ŷ 主轴 | **不做** | — | 明确非目标 |

### 建议日历（弹性）

| 周次 | 焦点 |
|------|------|
| W0 | 文档 + P0/P1 运维：刷簿 OOS 剔组、观察 soft hold |
| W1 | 同行业缺口广度 · 研究枢纽 rem 按钮 · score tooltip rem |
| W2+ | 用更大宇宙重估 rem；OOS IC 稳定后再依赖 rem_gate；分钟 τ 有数据再扩 R1 |

**已知限制（2026-08-13）**：扩大宇宙后 rem OOS IC≈0.09（n≈5328 / 36 票，命中≈0.52）——较 12 票 IC≈0.05 有改善，仍弱；**门控 + gap theme soft hold 可用，勿把 rem ŷ 当强排序信号**。分钟 τ 缺数据时 R1 暂缓。

**运维快照（同日）**：
- 满池 h=1 聚类已 promote **v54** · `mode=active` · OOS 失败组剔簿。
- `min_predicted_score`：**1.0 → 0.35**（h=1 日频 ŷ 很少到 +1%；旧门槛会把簿压成 1 只）。
- `sector_map` 已与观察池对齐（覆盖≈100%）；主题缺口才拉同伴广度。
- rem 预测对缺特征按训练集均值填（z=0），避免 live 仅有 gap/部分 sub_scores 时 `score_rem` 恒为 null。
- 分池簿行透传 `score_rem` / `event_prior`；横截面「不在 TopK」卖出也会走 event soft hold。

### 仍可优化（按性价比）

| 优先级 | 项 | 说明 |
|--------|-----|------|
| 高 | 降 OOS 失败组占比（现 ~50%） | 半池剔簿；纸面仍有 G3/G10/G15 持仓靠滞回留着。需更好聚类/特征或接受更小宇宙 |
| 中 | stance 与入簿门槛对齐 | 现 `probe=0.6`、入簿 `0.35`、`wait=0.25` 语义分叉，易误解 |
| 中 | rem 更大宇宙 / 主题日分层 IC | IC≈0.09 仍弱；勿把 rem 当排序主轴 |
| 低 | 分钟 R1 | 缺分钟缓存前不做 |
| 低 | `config.py` 缺省键仍兜底 +1.0 | 现网已写 0.35；改缺省会动单测，可单独 PR |

---

## 8. 验收与 KPI

### 8.1 分轨验收（禁止混算）

| 轨 | 验什么 |
|----|--------|
| EOD ŷ | 账本 / 复盘：\(h\) 日实现 vs 冻结 ŷ（现网） |
| event_prior | 主题日是否减少「因低 ŷ 卖掉后大涨」类踏空；误持比例 |
| ŷ_rem | \(\tau\) 时刻预测 vs \(\tau\to\) 收盘；IC / 命中；**不得**用全日相对昨收当唯一标签 |

### 8.2 主题日定义（开盘可得，防泄漏）

示例（可配置）：开盘后截面 `|gap|` 中位数或「gap≥阈值的股票占比」超过阈值 → `theme_day=1`。  
评估：条件在 `theme_day` 上的少做反率、纸面当日回撤贡献。

### 8.3 北极星对齐

对齐 [design-spine · 产品北极星](design-spine.md#产品北极星)：  
抬的是 **纸面风险调整收益** 与 **回测–纸面拟合**（主题日少踩坑），不是「ŷ 点出涨停」。

---

## 9. 非目标与红线

1. 不把实时特征静默写入生产 `predicted_score` 而不改 \(y\) / 字段名。  
2. 不用纸面成交价当 \(y\) 重拟合日线 β。  
3. 无 OOS、无人审不 promote rem 头。  
4. 无分钟数据时不宣称「已捕捉盘中延续」。  
5. 不替代 OOS 失败组摘除与重聚类（日线治理仍是第一公民）。  
6. 真·实盘 OMS 仍属 N6，与本方案无关。

---

## 10. 配置与代码锚点（随落地更新）

| 主题 | 路径 |
|------|------|
| 配置 | `data/signal_config.json` → `scoring.horizon_days` · `cluster_scoring.rebalance_tracks` · `event_prior` · `sentiment.prior.reduce_avoid_on_bullish` |
| 默认 | `core/signal/config.py` · `get_scoring_horizon_days` |
| Promote / OOS 剔组 | `core/signal/cluster_live.py` · `cluster_rank.py` |
| 事件先验 | `core/event_prior.py` · `core/paper_rebalance.py` |
| rem 面板 / Ridge | `core/research/rem_panel.py` · `quant/research/rem_ridge.py` |
| rem API | `POST /api/quant/rem-ridge` · `GET /api/quant/rem-ridge/model` |
| 日线 ŷ 主链 | [predicted-score-chain.md](predicted-score-chain.md) |
| 舆情旁路 | [sentiment-layer.md](sentiment-layer.md) |
| 测试 | `tests/test_p0_horizon_oos_event.py` · `tests/test_rem_ridge.py` |

---

## 11. 决策记录（摘要）

| 决策 | 结论 |
|------|------|
| 今日涨跌 vs EOD ŷ（h=1） | **对账合理**；巨大残差才是问题 |
| 实时信息先进哪 | 先 **动作层 \(E\)**，再 **rem 研究头**，最后才考虑进排序 |
| 拟合标签 | \(y_{\mathrm{rem}}(\tau)=\mathrm{close}/\mathrm{price}[\tau]-1\)（≈ \(y_\tau\)） |
| \(y_{\mathrm{ON}}\) | \(\mathrm{open}[t]/\mathrm{open}[t-1]-1\) · live 估 \(open[t+1]/open[t]\)；`predicted_score_on`；见 [predicted-score-chain §2.6](predicted-score-chain.md) |
| 与组 β | 残差头优先；错误组 β 靠 OOS 剔组 + 重聚类 |
| 分钟 RS / regime | R1/R2；有数据与 OOS 再上 |
| 契约完整态 | 见 [tau-contract-and-partition-upgrade.md](tau-contract-and-partition-upgrade.md) A1→A3；A3 前不替换 EOD 主字段 |

修订本文时：改阶段状态表，并在 [predicted-score-chain.md](predicted-score-chain.md) · [tau-contract-and-partition-upgrade.md](tau-contract-and-partition-upgrade.md) 保持互链。
