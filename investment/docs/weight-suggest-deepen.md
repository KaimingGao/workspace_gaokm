# 权重建议深化方案（IC / OLS / 配权）

目标：把研究枢纽「建议」从**单票启发式**推进到更接近专业研究链路的**可审证据摘要**，仍不自动写盘。

## 目标链路

```text
截面 IC / ICIR  →  （弱证据）OLS β  →  约束小步 Δ  →  OOS / 回测门禁  →  人审 promote
```

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0** | 默认用研究池 **截面 IC + ICIR** 驱动建议；\|ICIR\| 门槛 + 按 ICIR 缩放步长；表内展示 ICIR / 证据来源 | ✅ |
| **P1** | IC 弱时 OLS 回退 / 近零降权；对齐 CS-IC 输入 | ✅ |
| **P2** | 建议权 vs 当前权的 **OOS Top-K 门禁**（过门 → `promote_ready`） | ✅ |
| **P3** | 组内权重上限、零权冻结、与 live regime 白名单软提示 | ✅ 轻量 |
| **P4** | 单票 OLS β 聚类 → 组内池 OLS / 小步建议权（研究探针） | ✅ 轻量 |
| **P4.1** | 每组：组内建议权 vs 当前权 · Top-K OOS 对照（只读） | ✅ 轻量 |
| **P4.2** | 优选组 / 各组导出权重 diff（人审；`promote_ready` 恒否） | ✅ 轻量 |
| **P5** | 分组 score：每组用组权打分 → **组内排序**（对照全局权统一排名） | ✅ 轻量 |
| **P6** | 各组组内 Top-N **合成候选簿** + 分池 vs 全局 Top-K 对照回测 | ✅ 轻量 |
| **P7** | `code→cluster→weights` **映射产物** + 纸面分池调仓预演（不写账） | ✅ 轻量 |
| **P8** | 分池候选簿 **确认落账**（`confirm` → 写 paper；永不写 signal_config） | ✅ 轻量 |
| **P9** | `code_map` **多权打分**（仅组内序）+ 归档复打 API（不进 live） | ✅ 轻量 |
| **L0** | live 映射晋升 / 回滚（`cluster_weights_active.json`） | ✅ |
| **L1** | `score_stock` 影子/激活双分（`cluster_scoring.mode`） | ✅ |
| **L2** | `rank_cluster_pools` + 纸面 `cluster_mode` 调仓 | ✅ |
| **L3** | 草稿/日更刷新簿 + 覆盖率·陈旧健康检查 | ✅ |
| **L4** | execution 只读 `cluster_book`（不写全局 weights） | ✅ |

## 研究枢纽定位（股票分组）

**目的**：用聚类找出 **OLS 表现相似** 的股票 → **同组共用一套建模/配权**；**不同组用不同建模**（各组独立池 OLS → 独立小步权），避免异质票硬套同一套全局权。

```text
股票分组 → 宇宙=仅纸面持仓；OLS β·complete·τ；过远/异质票各自升单票组
一组一表 → G 标题含成员（名称+代码）；多票组池 OLS / 单票组单票 OLS → 小步建议权
未入组   → 数据不足单独提示（不再单独「花名册 / 持仓入组」块）
```

进阶（折叠）：影子对照 / 用分组打分 / 按分组调纸面仓 —— **不是**枢纽日常主路径。
探针（折叠）：单票 vs 所在组 —— 核对该票是否仍适合本组建模；不冲组表。

## P4–P9 · 实现链路（支撑上述三件事）

```text
宇宙 = 仅纸面持仓
  → 逐票 OLS β → 缩尾+z-score → complete·τ → 过远/Δβ异质 → 各自升单票组 → 多票组池 OLS / 单票组单票 OLS → 小步建议权
  → holdings_assignment（纸面持仓 → 组）
  → 组内 OOS / 组内 score 排序（证据）
  → UI：一组一表；可选晋升 live / 影子 / 分池调仓
```

- 入口：研究枢纽「跑分组」/ `POST /api/quant/factor-ols-clusters`
- **宇宙**：**仅纸面持仓**（不再并观察池）；持仓不足 2 只不可聚类
- lookback 80、**关闭 PIT 财务**（否则逐票面板会极慢）
- 解释池 OLS 被异质票拉开；组权仍是小步 Δ，**非** raw β→权重占比
- **组内 OOS**：仅在组员宇宙上对照；过门 ≠ 全局 `promote_ready`；**不写** `signal_config`
- **导出**：`preferred_cluster`（导出优先，非分类标签）+ 每组「导出本组 diff」；合并为全局权前须确认代表性
- **枢纽 IA**：分组 + 一组一表 + 探针 = 主路径；阈值 / 横截面收进「全局对照」折叠（非分组权）
- **分组 score**：`group_scores` / `cluster.group_ranking`；`global_ranking` 为同批票全局权对照
- **分池合成**：`pool_merge.book`（最新截面各组 Top-N）；`pool_merge.backtest`（历史分池 vs 全局，close 执行）
- **映射产物**：`pool_artifact`（`code_map` + `pool_book`）；落盘 `data/reports/last_cluster_pool_artifact.json`
- **纸面预演 / 落账**：`POST /api/quant/cluster-paper-preview`（默认 deepcopy；`confirm=true` 写纸面并记 `last_cluster_pool`）
- **多权打分**：`multi_score` / `POST /api/quant/cluster-multi-score`（`cross_group_rank=false`；不进 live scorer）

## P0/P1 规则

对每个 `signal_config.weights` 因子：

1. **强截面证据**：`n` 足够，且 `|IC| ≥ 0.03`，且 `|ICIR| ≥ 0.25`  
   → `δ = sign(IC) · 0.03 · clamp(|ICIR|/0.5, 0.5, 1.5)`
2. **否则 OLS**：`|β| ≥ 0.05` → `±0.02`
3. **否则近零 IC** → `−0.015`
4. **零权冻结**：原权重为 0 的因子（如 `money_flow`）不复活
5. **组内上限**：`factor_groups` 各组和 ≤ 0.45，超限等比压缩后归一化
6. 相关冗余 / 白名单外上调 → `constraint_warnings`

## P2 OOS 门禁

- 同一研究池、同一 Top-K 参数，分别用当前权 / 建议权跑 `backtest_topk`（经 `signal_config_overlay`）
- 权益曲线后 30% 为 OOS（`split_oos_summary`）
- **过门**：建议 OOS ≥ 当前 OOS − `oos_tol_pp`（默认 1pp），且不新增 OOS 失败旗标
- `promote_ready = passed ∧ ¬skipped`；导出 diff 带 `apply_note`；未过门时 UI 二次确认

## 非目标

- 自动把组权写入 `signal_config.weights`（live 权向量只在 `data/live/`）
- 完整均值方差 / 风险平价求解
- raw OLS β → 权重占比
- 跨组统一总榜冒充分组

## 验收（研究段）

- `ic_mode=cs_ic` 时表含 ICIR；建议响应含 `oos_gate` / `promote_ready`
- 单测：ICIR 门槛、组上限、overlay、OOS 门禁 mock
- `ASSET_V` 与页内说明同步

---

## Live 路线 · 分组进生产打分（已落地）

### 产品硬约束（不可破）

1. **不**把各组权合并成一份全局 `signal_config.weights` 后假装「已分组」
2. **不**做跨组统一总榜；live 选股 = **组内排序 → 分池合成**（与研究 P5/P6 同构）
3. 全局 `signal_config` 继续服务「未映射票 / 回退路径」；分组权走**独立活产物**
4. 人审 promote 映射产物后才能挂 live；自动 OLS 重聚类默认关

### 现状 → 缺口

| 已有（研究） | Live 缺口 |
|--------------|-----------|
| `pool_artifact` / `code_map` | 版本化「生效中」产物 + 回滚 |
| `multi_score` / 组内序 | `score_stock` / `rank_cross_section` 读 code_map |
| 分池合成簿 + 纸面 confirm | 日更扫描 / 纸面调仓默认走分池模式 |
| 组内 OOS | live 旁路对照 + 失效告警 |

今日 live 主路径：`rank_cross_section` → `score_stock` → 单一 `load_signal_config().weights`。

### 目标架构

```text
[人审] β 分组产物
   → promote → data/live/cluster_weights_active.json  (code→cluster→weights + meta)
   → score_stock(code): 若 code∈map 则 overlay 组权，否则全局权
   → rank_cluster_pools(): 各组内 Top-N → 合并候选（非跨组 sort）
   → paper rebalance / daily cycle（可选开关 cluster_mode）
   → （更后）实盘 execution 只消费合并簿，不改全局 config
```

### 分阶段

| 阶段 | 内容 | 退出标准 |
|------|------|----------|
| **L0 · 生效产物** | `promote_cluster_artifact`：研究产物 → `cluster_weights_active.json`；带 `version` / `promoted_at` / `source_created_at`；UI「晋升 live 映射」二次确认；支持回滚上一版 | 文件可读写；未映射不影响现网 |
| **L1 · 影子打分** | `score_stock(..., cluster_mode="shadow")`：同时出 `score_global` / `score_cluster`；日报/研究页对照，**调仓仍用全局** | 影子跑 ≥N 日；组内序稳定、无大面积硬拒异常 |
| **L2 · 纸面分池 live** | `rank_cluster_pools` 替代（或并列）`rank_cross_section`；`paper.rebalance(cluster_mode=true)` 消费合并簿；默认仍需开关 | 纸面连续调仓与 P8 语义一致；可一键回退全局 Top-K |
| **L3 · 日更闭环** | 定时：watching 变更时「仅重打分不重聚类」；可选低频重跑 β 分组 → **草稿**待审，不自动 promote | 映射覆盖率/过期告警；草稿 ≠ active |
| **L4 · 实盘只读消费** | execution / 下单路径只读「分池合并簿」；审计字段 `weight_source=cluster:{label}` | 与券商/执行适配；仍禁止写全局 weights |

### 配置与数据

- **新文件**（建议）：`data/live/cluster_weights_active.json` + `cluster_weights_history/`
- **不要**把 `code_map` 塞进 `signal_config.json`（避免污染全局 promote / regime 白名单）
- `signal_config` 可加软开关：`cluster_scoring.enabled` / `mode: off|shadow|active`（开关在 config，**权向量在 live 产物**）

### 打分语义（L1/L2）

```text
for code in universe:
  weights = code_map[code].weights if mapped else global.weights
  score = score_bars(..., config=overlay(weights))
for each cluster:
  rank members by score  # 仅组内
book = concat(top_n_per_group)  # 分池合成；禁止 flat sort(all scores)
```

中性化：优先**组内**中性化；全宇宙中性化会混淆组权，L2 默认关或按组做。

### 风险与门禁

- **覆盖率**：未映射票走全局权并打标；覆盖率 &lt; 阈值 → 告警、禁止 `mode=active`
- **陈旧**：`promoted_at` 超期（如 7/14 交易日）→ 自动降级 shadow/off
- **组崩塌**：单票组过多 / 一组过大 → promote 拒绝或仅 shadow
- **回撤**：纸面 `cluster_mode` 与全局并行影子净值，劣于全局超阈值则建议降级
- **审计**：每笔纸面调仓 meta 带 `cluster_label` + artifact `version`

### 明确不做（Live 段）

- 自动把组权 promote 进 `signal_config.weights`
- 用跨组 score 排序冒充分组
- 无影子期直接 L2 active
- raw OLS β 当生产权重占比

### 简化主路径（UI）

```text
β 分组 → ① 应用分组（晋升+影子+刷新簿）→ ② 启用组权 → ③ 纸面分池
```

- 一键 API：`POST /api/quant/cluster-live/apply`
- 回滚 / 关闭 / 导出 / 预演 / 复打 收在「更多」

## 深化补强（相对 P/L 勾选后的落地）

骨架（P0–P9 + L0–L4）已闭合；补强不再开新大阶段，而把轻量实现做成可审工作台：

| 项 | 内容 | 状态 |
|----|------|------|
| 文档对齐 | 宇宙=纸面持仓；主路径三块写死 | ✅ |
| 枢纽 IA | 阈值/横截面降级为「全局对照」折叠 | ✅ |
| 组表工作台 | 组可折叠；\|Δ\| 大行可扫视；表头粘性 | ✅ |
| 探针状态 | 通过 / 异质 / 单票组 徽章，少长句 | ✅ |
| Live 运维验 | 覆盖率·陈旧·回滚对照 L 退出标准 | 待人工清单 |

### 与研究段衔接

- Promote 输入 = 人审后的 `pool_artifact`（P7），不是每次 β 分组的瞬时结果
- 纸面 confirm（P8）验证的是**候选簿**；L2 验证的是**日更路径长期用同一套 map**
- 多权复打（P9）= L1 的研究预演；L1 把它接到 `score_stock` 主链
