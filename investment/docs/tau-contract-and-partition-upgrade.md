# 决策时刻 τ 契约 · 双层 predicted_score · 分组目标升级

[← 文档索引](README.md) · ŷ 主链 [predicted-score-chain.md](predicted-score-chain.md)（含 **§2.5 双层 ŷ**）· 盘中 rem / 事件先验 [intraday-residual-score.md](intraday-residual-score.md) · 主轴 [design-spine.md](design-spine.md)

本文固化 2026-08 复盘结论：**产品契约（给定 τ 的条件预测）尚未完整落地**；**分组目标（IC↑ / R²↑ / 误差↓）在代码口径上有优化，但未交付到 live**。并给出可验收的升级方案。

**双层 predicted_score（产品定稿口径）**

| 字段 | 含义 | 训练 | 现网 |
|------|------|------|------|
| `predicted_score` | ŷ_EOD：隔夜/前瞻 h 日 | 组 β，标签 close→close | **主排序 · 买入** |
| `predicted_score_tau` | ŷ_τ：当日剩余（τ→收盘） | 独立 γ（rem_ridge）；标签 close/price[τ]−1 | **已输出**；F1 买入闸默认开 |
| 融合 | 决策层 F0→F1→F2→F3 | 见 **§9** | **默认 F1**；F2=`predicted_score_blend` 可选；B1 promote OOS 相对 active 硬闸 **已落地**；B2 focused 贪心 + B3 promote-preflight **已落地** |

规范摘要亦写入 [predicted-score-chain.md §2.5](predicted-score-chain.md)。

**状态**：方案文档；实施按下方阶段表推进，改状态时同步改本节与 [intraday-residual-score.md](intraday-residual-score.md) 阶段表。

---

## 1. 产品契约（应然）

```text
在决策时刻 τ：
  输入  ℱ_τ = τ 及以前可观测信号（无未来函数）
  输出  ŷ(τ) = f(ℱ_τ)
  标签  y(τ) = 从 τ 起要预测的收益（如 close[T]/price[τ]-1）
  用途  排序 / 买入 / 卖出 使用同一套 ŷ(τ)（或显式第二键并标注）
```

禁止：

- 用 \(\tau\) 之后才知道的价格/新闻进 \(\mathcal{F}_\tau\)
- 特征已含大半当日涨幅，却用「全日相对昨收」验收命中
- 混用同一 `score` 字段却不标注 `as_of_tau`

---

## 2. 现网实然（相对契约的缺口）

| 契约项 | 现网 | 结论 |
|--------|------|------|
| 主排序 / 买入 | EOD `predicted_score`：信息集 ≈ \(T\!-\!1\) 日线因子 × 组 β | **未按「当前 τ」决策** |
| 预测标签 | \(h\) 日 close→close（现网常见 h=1） | 不是 \(y(\tau)\) |
| 开盘价 / 缺口 | `gap_pct`、`event_prior`、`score_rem` | **卖出 soft hold + 展示**；不进买入排序 |
| 盘中最新价 `price[τ]` | 展示 / 反推昨收 | **不进主 ŷ，不作 rem 的 τ**（R0 的 τ=open） |
| 策略体感 | 买仍只看 ŷ；卖更谨慎 | 契约不完整 → 优化感弱属预期 |

一句话：**现网 = EOD 选股 + 主题日卖出保护**；不宜表述为已实现「任意时刻前视预测」。

详细阶段与代码锚点见 [intraday-residual-score.md](intraday-residual-score.md)。

---

## 3. 如何刻画「τ 前的信号」\(\mathcal{F}_\tau\)

### 3.1 分解

\[
\mathcal{F}_\tau = \bigl( X_{\le t(\tau)},\; Z_{\le\tau},\; C_{\le\tau} \bigr)
\]

| 块 | 含义 | 例子 |
|----|------|------|
| \(X\) | 慢变量（日线 / PIT） | `sub_scores`、财务 as_of、cluster label / 组 β |
| \(Z\) | 快变量（当日截至 τ） | open、gap%、price[τ]、open→τ 已实现、板块广度、分钟量价 |
| \(C\) | 约束 / 上下文 | 涨跌停、停牌、日历、门槛配置 |

### 3.2 按 τ 分层（固定少数点，禁止任意连续时点爆炸）

**τ = EOD（昨收，现网主轴）**

```text
ℱ_EOD ≈ {
  bars date ≤ T-1,
  X = sub_scores(T-1),
  fundamentals as_of ≤ T-1,
  promoted cluster β
}
# 标签（现网）: close[t+h]/close[t]-1
# 不含: T 日 open / 现价 / 当日新闻
```

**τ = open（R0）**

```text
ℱ_open = ℱ_EOD ∪ {
  open[T],
  gap% = open[T]/close[T-1]-1,
  同业开盘缺口截面 / breadth（若已算）,
  theme_day = 1{gap ≥ 阈值},
  可选: publish_time ≤ open 的舆情
}
# 标签: close[T]/open[T]-1
# 禁止: 盘中后续价、午后新闻
```

**τ = 09:45（R1，需分钟）**

```text
ℱ_0945 = ℱ_open ∪ {
  price[09:45], volume[≤09:45],
  ret_open_to_τ,
  板块中位已实现(≤τ),
  新闻 publish_time ≤ 09:45
}
# 标签: close[T]/price[09:45]-1
```

### 3.3 工程特征包（建议打分/落盘统一形状）

```text
{
  stock_code, decision_date,
  tau: "eod" | "open" | "09:45",
  tau_ts,
  as_of_bars,                 # X 截止，开盘 τ 多为 T-1
  X: { ... },                 # 仅 ≤ as_of_bars
  Z: { gap_pct, open, price_tau, ... },
  C: { limit_up, halted, ... },
  y_spec,                     # 与 τ 对齐的公式
  y: null | 实现值            # 收盘后填，仅训练/复盘
}
```

规则：

1. **时间戳门**：字段 `observed_at ≤ τ`，否则丢弃或置缺。  
2. **日线门**：\(X\) 的 bar `date ≤ T-1`（开盘/盘中 τ 时 T 日 K 未完成）。  
3. **缺失**：Z 不足 → 该 τ 头不输出或回退 EOD，禁止用收盘填盘中洞。  
4. **截面同步**：breadth 只用已报价同伴，并记录有效样本数。

### 3.4 合法 / 非法

| 合法 | 非法 |
|------|------|
| open、gap、τ 前分钟 | 用当日收盘解释开盘决策 |
| \(T\!-\!1\) 因子 + 开盘缺口 | 「今日涨跌」当特征又预测「今日涨跌」 |
| `publish_time ≤ τ` 新闻 | 收盘后标题解释盘中 ŷ |

### 3.5 与现网字段映射

| 契约块 | 现网 | 缺口 |
|--------|------|------|
| \(X\) | `sub_scores` → EOD ŷ | 有 |
| \(Z\) @open | `gap_pct` / rem 特征 / `event_prior` | 有；未进主排序 |
| \(Z\) @price[τ] | 无正式盘中 τ | 缺分钟或未接线 |
| 显式 `as_of_tau` / `y_spec` | 弱 | A1 补齐 |
| \(y(\tau)\) 与主 score | 未对齐 | 主轴仍是 EOD 标签 |

---

## 4. 是否需要更多因子？

**现阶段不优先堆日线因子。**

| 原因 | 说明 |
|------|------|
| 信息集缺口 | 主题残差来自 \(T\) 日冲击；再多 EOD 形态进不了 \(\mathcal{F}_\tau\) |
| 线性容量 | 现有动量/RS/波动/质量/资金流等已够喂组 OLS；盲目加因子抬共线、伤 OOS |
| 错组 β | 分组错了，多因子只是多几项被错用的 β |
| 分区未 promote | loss 改了但 live β 未换，加因子难归因 |

**再加因子的前提**：固定标签与 τ 下现有 \(X\) holdout IC 平台期；新因子 PIT 无泄漏且 OOS 不恶化。

**若加，优先加契约缺口型 \(Z\)**（开盘缺口%、板块开盘广度、有分钟后的 τ 已实现），而不是再扩一堆 EOD 技术因子。

顺序建议：

```text
可 promote 的更好分组 → open τ 头进买卖 → 再评估日线因子是否不够 → 扩因子库
```

---

## 5. 分组目标：IC↑、R²↑、误差↓

### 5.1 应然

选分区使：组池拟合好（R²↑ / 误差↓）+ 组内 ŷ 截面 IC↑，并控制失衡/单票等结构成本。  
实现：`quant/research/partition_loss.py`（`ic_ref_scale` 放大 IC 项，避免被 \(1-R^2\) 淹没；holdout / 多折与 live auto-k 对齐）。

### 5.2 实然

| 层 | 状态 |
|----|------|
| 目标函数 / 选 k 口径 | **已改**（IC 量纲、失衡/单票罚、holdout） |
| 生产 `cluster_weights_active` | **基本未吃到**：retune 后 draft OOS 变差 → **未 promote** |
| 实盘选股质量 | 不取决于这波 loss 微调 alone |

结论：**优化器更对齐目标，交付物未跟上。**

### 5.3 升级（B）

**B1 — 目标与 promote 闸解耦**

- 选分区：继续用 `partition_loss`（holdout）。  
- Promote 硬闸：**已落地** `compare_oos_vs_active`（`cluster_oos_labels`）—— draft `fail_rate` 不得高于 `max_oos_fail_rate`；相对 active **默认软提示**（`promote_allow_worse_oos_than_active=true`）；设 `false` 可恢复「不得差于 active」硬闸；`force` 可豁免整闸。  
- 避免「loss↓ 但 OOS↑」被当成成功。

**B2 — 搜索对准目标（重于再拧权重）**

- 保持 auto-k 邻域 + 多折 holdout。  
- **已加强**：β 异质踢出（既有）+ 有界贪心换组——小宇宙 `full`；大宇宙（≥40 或 n>32）改为 **`focused`**：只动 holdout IC≤0 / 拟合失败的弱组票，优先并入强组（不再整段 `large_universe_skip`）。  
- 大宇宙：粗分 + 组内细分，或弱行业先验（非唯一划分）——仍可继续加深。  
- 验收：同切点新分区 vs active 的 mean holdout IC、组均 R²、OOS fail 数、焦点票组归属 —— **`compare_partition_vs_active` / `GET /api/quant/cluster-live/promote-preflight`**。

**B3 — 交付节奏**

1. draft + 对照表（promote-preflight）→ 2. 过 B1 → promote → 3. 纸面观察；失败 shadow 回滚。

**与 A 的衔接**：组 β 仍拟合 **EOD 标签**（与账本一致）；τ 头用 **独立模型**（可按组分桶），禁止把分钟特征塞进组 β 却仍用 close/close 标签。

---

## 6. 产品契约升级（A）

**A1 — 字段契约先立住**

- 固定 τ：`open`（必做）；`09:45`（有分钟再开）。  
- 输出：`as_of_tau`、`y_spec`、`predicted_score`（EOD）、`predicted_score_tau`（新）。  
- UI/账本禁止混读。  
- 研究枢纽 IA：双层 ŷ 复盘 + 独立 ŷ_τ 区块 + 概览 KPI 并列；rem 不与「跑分组」并列主 CTA（见 [predicted-score-chain.md §2.5](predicted-score-chain.md)）。  
- 验收：任意一票能回答「分对应哪个 τ、预测到哪」。

**A2 — τ 分驱动动作（买卖对称，可回滚）**

- 买：EOD 门槛 **且** `ŷ_τ ≥ floor_τ`（或 EOD 主排 + τ 硬闸）。  
- 卖：soft hold 看 `ŷ_τ` / 缺口，而非仅挡「低 EOD」。  
- 先纸面 **影子簿** 2–4 周再切主路径。  
- 验收：主题日踏空↓；非主题换手不过度↑；IC(\(ŷ_τ,y_τ\))>0 且稳定。

**A3 — 主排序切到 τ（完整契约）**

- 仅当 A2 影子簿达标。  
- `score := predicted_score_tau`；EOD 降为对照。  
- τ：open → 09:45。

**近期明确不做**：用现价改写 EOD ŷ 却不改标签；无分钟却声称任意盘中 τ。

---

## 7. 推荐落地顺序与验收

```text
1) A1 字段契约（open τ）
2) B1 + B2（可 promote 的更好分区）
3) A2 影子簿：τ 闸买卖
4) 分钟就绪 → A2@09:45 / A3
```

| 块 | 成功标准 |
|----|----------|
| A | 影子簿 vs 纯 EOD：主题日卖出踏空↓；换手可控；\(ŷ_τ\) 与 \(y_τ\) IC 稳定为正 |
| B | promote 后 holdout IC / 组 R² 优于旧 active；OOS fail 不升；错组案例减少 |

**最划算的两刀**：① 能 promote 的更好分区（抬主轴 ŷ）；② A2@open 买入也看开盘 τ 分（契约对称，不再只是卖更谨慎）。

---

## 8. 实现依赖：资源 · 原始信号 · 因子（X / Z）

本节回答：落地 A/B 方案要准备什么。原则是 **复用现有日线 \(X\)，新建/补齐 \(\tau\) 时刻 \(Z\)**；不先扩一大套 EOD 因子。

### 8.1 资源（基础设施）

| 资源 | A1 / A2@open / B | A2@09:45 / A3 盘中 | 现状 |
|------|------------------|-------------------|------|
| 日线缓存 `data/store/daily` | 必需 | 必需 | 有 |
| 实时行情（开盘价、现价、昨收、涨跌幅） | 必需（open / gap） | 必需 | 有（腾讯 quote） |
| 观察池 / `sector_map` | breadth、分池 | 同左 | 有；覆盖率要维持 |
| live 组 β / `cluster_weights_active` | B promote；τ 头可分桶 | 同左 | 有；B 需可 promote 新稿 |
| rem / τ 模型落盘 `rem_ridge_model.json` | A2 门控与 `ŷ_τ` | 按 τ 分模型或多 τ | **rem_ridge_v2**：persist 写入 `tau` / `y_spec` / `dual_score_head` |
| 分钟缓存 `data/store/minute` | **不需要** | **必需**（稳定回填） | 弱；挡 R1 |
| 交易日历 / 停牌涨跌停标记 | 样本清洗、\(C\) | 同左 | 部分有 |
| 舆情时间戳（可选） | soft hold 对称 | `publish_time≤τ` | 有旁路；进 \(Z\) 须 PIT |
| 算力 / 研究流水线 | 分组重跑 + holdout 对照表 | τ 面板 OOS | 现有 quant API / scripts |
| 纸面影子簿与日志 | A2 验收 | 同左 | **已落地** `cluster_book_tau_shadow.json` + status `tau_shadow_book` |

### 8.2 原始信号（未加工行情 / 事件）

| 信号 | 用途 | 何时需要 |
|------|------|----------|
| `close[T-1]`（日线） | 缺口分母；EOD \(X\) | 全程 |
| `open[T]`（行情） | gap、τ=open 的 price[τ] | A1 起 |
| `close[T]`（日线，事后） | 标签 \(y\)、OOS | 训练/复盘 |
| 截面同伴 `open` 列表 | `sector_gap_breadth` | A2 推荐；卖出环已有批量路径 |
| `price[τ]`、`volume[≤τ]`（分钟） | 盘中 \(Z\)、\(y(\tau)\) | 仅 09:45+ |
| 新闻/舆情 `publish_time` | 可选 \(Z\) / soft hold | 可选；须 ≤τ |
| 涨跌停 / 停牌 | \(C\)：不可成交剔除 | 全程样本质量 |

### 8.3 因子两类：慢变量 \(X\) vs 快变量 \(Z\)

**\(X\)（EOD，大多已有 — 以组 β 进模为准，不必先加新）**

现网 `signal_config.weights` / 注册表已覆盖，例如：

- 趋势：`momentum` · `ma_slope` · `technical_pattern` · `weekly_confirm` · `idio_momentum`
- 相对：`relative_strength`
- 量价/流动：`volume_price` · `liquidity` · `amihud` · `money_flow`
- 风险：`volatility` · `reversal` · `gap_risk`
- 基本面：`value` · `quality` · `size` · `earnings_yield` · `growth` · `dividend`
- 舆情（旁路权重）：`alt_sentiment`

分组 B 与 EOD ŷ **继续用这套 \(X\)**；任务是分对组、promote，不是扩库。

**\(Z\)（τ 前快变量 — 方案要新建/规范化的「因子」）**

| 因子键 | 定义（τ 前） | 标签对齐 | 阶段 |
|--------|--------------|----------|------|
| `gap_pct` / `open_gap` | \(\mathrm{open}[T]/\mathrm{close}[T\!-\!1]-1\) | open→close | A1 起（已有计算） |
| `theme_day` | \(1\{\mathrm{gap}\ge\mathrm{trigger}\}\) | 同上 | 已有 |
| `sector_gap_breadth` | 同伴 gap≥阈值占比 | 同上 | 补齐批量、打进特征包 |
| `ŷ_EOD` 或组内秩（可选） | \(T\!-\!1\) 主分作协变量 | 勿泄漏收盘 | A2 可选 |
| `ret_open_to_tau` | \(\mathrm{price}[\tau]/\mathrm{open}-1\) | \(\tau\to\)close | 09:45 |
| `price_tau` / 分钟动量·量比 | 截至 τ 的价量 | 同上 | 09:45 |
| `sector_ret_to_tau` | 板块中位已实现 ≤τ | 同上 | 09:45 |
| 舆情分（PIT） | `publish_time≤τ` 聚合 | 对应 τ | 可选 |

现网 rem 额外特征键：`gap_pct` · `open_gap` · `sector_gap_breadth` · `theme_day`（`REM_FEATURE_EXTRA`），可与日线 \(X\) 拼进 Ridge；**进主排序前须有独立 `y_spec` 与 OOS**。

### 8.4 按阶段：最少集合

```text
【A1 字段契约 @open】
  资源: 日线 + quote(open, 昨收)
  信号: open[T], close[T-1]
  因子: X(已有) + Z={gap_pct, theme_day}；输出 as_of_tau / y_spec / predicted_score_tau 占位

【A2 影子簿 @open】
  资源: + sector_map/观察池批量行情；rem 或 τ Ridge 落盘；影子簿开关
  信号: + 同伴 open 截面
  因子: Z 加上 sector_gap_breadth；ŷ_τ = g(X,Z)；买卖闸用 ŷ_τ

【B 分组 promote】
  资源: 全池日线面板、holdout 日历、对照脚本、OOS 闸
  信号: 仅日线（与现网 EOD 标签）
  因子: 仍用现有 X；不新建日线因子

【A2/A3 @09:45】
  资源: 分钟缓存稳定回填
  信号: price/volume ≤09:45
  因子: + ret_open_to_tau, 分钟量价, sector_ret_to_tau
```

### 8.5 不要当作本方案「必建因子」的

- 再堆一批与现有共线的 EOD 技术指标  
- 用「当日已实现涨跌幅」当特征去预测「当日相对昨收」  
- 无分钟时伪造盘中 \(Z\)

---

## 9. τ ŷ 建模 · 训练 · 与 EOD 融合

日线 `predicted_score`（ŷ_EOD）**保持现网组 β / 全局 Ridge**，不改标签。  
新增 **ŷ_τ**（字段名建议 `predicted_score_tau`；现网 `score_rem` 为 open 雏形）。

### 9.1 建模（ŷ_τ）

\[
\begin{aligned}
y_\tau &= \bigl(\mathrm{close}[T]/\mathrm{price}[\tau]-1\bigr)\times 100 \\
\hat y_\tau &= g\bigl(X_{T-1},\, Z_{\le\tau};\gamma\bigr)
\end{aligned}
\]

| 项 | 建议 |
|----|------|
| 模型类 | 与 EOD 同构：**标准化 + Ridge/OLS**（现网 `rem_ridge` / `fit_factor_ols_from_panel`） |
| \(X\) | 复用 \(T\!-\!1\) `sub_scores`（可子集，防共线） |
| \(Z\) @open | `gap_pct` · `theme_day` · `sector_gap_breadth`（+ 可选 ŷ_EOD 作协变量） |
| \(Z\) @09:45 | 另加 `ret_open_to_tau` · 分钟量比等 |
| 分桶 | **优先全局一头**；样本够再按 live cluster 分桶各估 \(\gamma\) |
| 与 EOD 关系 | **独立头**：估 \(\gamma\)，**不改**组 β；禁止把 \(Z\) 塞进 EOD 标签回归 |

起步固定 \(\tau\in\{\mathrm{open}\}\)；分钟就绪后再训第二套 \(\gamma_{0945}\)，不要一个模型混多个 τ。

### 9.2 训练

```text
历史日线 (+ 分钟若 τ>open)
  → 按 (code, date=T, τ) 拼面板：X≤T-1, Z≤τ, y_τ
  → 丢停牌 / τ 无价 / 涨跌停不可成交（对齐 halt_policy）
  → 时间切分：前段估 γ，后段 OOS（禁止打乱日期）
  → 指标：IC(ŷ_τ, y_τ)、方向命中、主题日「少做反」
  → 人审 → persist（如 rem_ridge_model.json，注明 tau + y_spec）
```

| 实践 | 说明 |
|------|------|
| 样本权 | 近端提权；主题日可用开盘可得 `theme_day` 加权（R2，无收盘泄漏） |
| 现网锚点 | `collect_rem_open_panel` · `fit_rem_ridge_report` · `predict_rem_from_features` |
| OOS 门 | IC/命中过弱则 **只展示不进买卖闸**（现 rem IC≈0.09 量级时尤甚） |
| 重训节奏 | 与分组解耦；EOD promote 后可重刷 τ 头，或按周/月滚动 |

### 9.3 融合两个 score（推荐阶梯）

两个数 **量纲都是收益 %**，但 **预测区间不同**，不能当成同一标签的简单平均后当唯一真相。融合应发生在 **决策层**，不是先糊成一个数再假装对账。

**阶段 F0 — 不融合（现网）**

```text
排序/买入 := ŷ_EOD
卖出豁免 := event_prior(gap, ŷ_τ)
```

**阶段 F1 — 闸门融合（推荐下一刀，A2）**

```text
候选 := ŷ_EOD ≥ floor_EOD          # 谁能进池
买入 := 候选 ∧ ŷ_τ ≥ floor_τ       # 当日剩余也要够
卖出 := 原 EOD/TopK 规则，但 ŷ_τ 高或 theme → soft hold
```

语义：EOD 负责「隔夜相对吸引力」，τ 负责「今天别逆着开盘结构买/卖」。

**阶段 F2 — 显式合成分（可选，须标注）**

\[
s = w_{\mathrm{EOD}}\hat y_{\mathrm{EOD}} + w_\tau \hat y_\tau
\]

- \(w\) 可固定（如 0.5/0.5）或按 `theme_day` 提高 \(w_\tau\)。  
- **仅用于排序/展示**；复盘仍分别报 IC(ŷ_EOD,y_EOD) 与 IC(ŷ_τ,y_τ)。  
- 禁止用 \(s\) 去对账单一 \(y\)。

**阶段 F3 — 主轴切换（A3）**

盘中决策窗以 ŷ_τ 为主排序；ŷ_EOD 降为对照/隔夜研究。须影子簿达标。

**不推荐**

- 未改标签就把 \(Z\) 和 \(X\) 训成「一个 predicted_score」  
- 用今日已实现涨幅校准 ŷ_EOD 再当预测  
- 两套分加权后只留一个字段、不写 `as_of` / `y_spec`

### 9.4 字段与对账

| 字段 | 对账标签 |
|------|----------|
| `predicted_score` | \(\mathrm{close}[t+h]/\mathrm{close}[t]-1\) |
| `predicted_score_tau` | \(\mathrm{close}[T]/\mathrm{price}[\tau]-1\) |
| 融合分 \(s\)（若有） | **不对账**；只作排序键，旁注权重 |

---

## 10. 决策记录（摘要）

| 决策 | 结论 |
|------|------|
| 现网是否完整实现 τ 契约 | **否**；仅为 EOD + 卖出保护 |
| rem / gap 是否改变买入 | **否** |
| 是否先加大量日线因子 | **否**；优先 \(Z\)@open 与分组交付 |
| EOD 主轴是否立即废除 | **否**；经 A1→A2 影子簿后再谈 A3 |
| 分组 loss retune 是否等于 live 更好 | **否**；必须过 OOS promote 闸 |
| 两套 ŷ 如何共存 | EOD 沿用；τ 独立 Ridge；融合先 F1 闸门，慎 F2 加权 |
| τ 建模起点 | 复用/升级 rem_ridge；τ=open 先行 |

修订：改阶段状态时同步 [intraday-residual-score.md](intraday-residual-score.md) · [predicted-score-chain.md](predicted-score-chain.md) · [roadmap.md](roadmap.md)。
