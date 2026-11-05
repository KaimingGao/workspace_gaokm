# Live 早开盘调仓：今开截面 PIT 方案

[← 文档索引](../README.md) · 调仓产品见 [rebalance-logic.md](../rebalance-logic.md) · L1/L2 见 [feature-lake-daycube.md](./feature-lake-daycube.md) · 宇宙键见 [daycube-universe-key.md](./daycube-universe-key.md)

> 状态：**已落地**（成交闸 + open-pit 出分 + tip + rank 显式传立方；回测未改）  
> 范围：live 开盘窗（默认 `live_fill_clock`～10:00，可配 09:25）的矩阵调仓打分与下单闸  
> 不覆盖：模型重训、回测 `_score_open_day` 改写、实盘 OMS、启动时间 >09:30 的分钟钟现算

---

## 1. 问题与结论

### 1.1 现象（2026-10-28 · 伊利股份 600887）

早约 09:32 live 调仓买入时，界面会话日 T=2026-10-28、显示 ŷ_oo **+0.224%**，实际用的是 **10-27** 开盘立方；晚间日线物化后持仓刷新改读 **10-28** 立方，显示 **−0.236%**。β 未变，α 均 +0.058；成交价 29.0187 来自当时现价 29.01（滑点 3bp），今开与日线开盘均为 29.06，不是早晚开盘价不一致。

### 1.2 根因（修复前）

- 开盘窗内当日官方开盘立方尚未物化（当日日线未入库）。
- `_lake_score_day_candidates` 依次尝试会话日 → `expected_latest_daily_bar_date`（15:05 前 = 上一交易日）→ 再前一日；读到昨日立方后打 `lake_score_fallback_from` **仍返回分数**，自动调仓不读该标记直接下单。
- 昨日立方的 ŷ_oo 标签是 `open[T−1]→open[T]`，集合竞价给出今开后这段收益**已实现**，不是产品要估的 `open[T]→open[T+1]`；界面 tip「T−1 因子」不能当作因子截止日的证明。

### 1.3 与回测的对比

| 路径 | 决策日 T 的特征 | 缺当日立方时 |
|------|-----------------|--------------|
| 历史回测 `_score_open_day` / `score_open_day_entries` | 读 **当天** L1/L2（窗口截至昨收，报价用今开） | `clip_replay_score_dates` 丢掉未完成会话日，**不**用昨日 ŷ 顶替 |
| 修复前 live（消费只读） | 优先会话日立方；没有则 **静默退到昨日** | 仍下单 |
| **现行** live 开盘钟 | 会话日立方，否则 **open-pit 今开截面** | 落账拒单；浏览才可带标签回退 |

结论：回测本无此错位；live 开盘钟已按上表修好。复权不是本次 ŷ_oo 翻转的原因（伊利 10-27 立方在晚间重写后 z 仍与早盘 tip 一致）。

---

## 2. 修复目标

- 开盘窗内决策日 = 会话日 T；ŷ_oo 预测 **今开 → 下一交易日开开**；特征 = **截至 T−1 的完整前复权日线** + **T 日行情今开**，截面 z 在观察池当场算。
- 当日官方开盘立方已在盘上 → 直接用它（与晚间刷新同口径）；占位截面建不齐 → **拒绝本轮下单**，禁止再用昨日立方成交。
- 占位结果 **不得** 写成官方 `open` L2 文件，以免晚上物化跳过当日；回测逻辑保持不变。

---

## 3. 早开盘时序（修复后）

自动 worker：交易日 `live_fill_clock`（可 09:25）～10:00，窗内 30s 一轮；当天成功调仓后不再跑。

```text
窗开且当天未跑
  ├─ 有未完成隔夜挂单 → 本轮不算新分；读会话日 T 官方开盘立方：有 → 用官方 μ/σ 与 X，跳到 fuse / 下单
  ├─ 无 → 批量行情拿观察池今开（约 800 只，一条腾讯请求）→ 读每只最近 ~80 根日线（只用到 T−1）
  ├─ inject_raw_aoo / 开盘钟 raw_atc / 训练同款风格裸分（不含 aoo 重叠列、无真源资金流）
  ├─ 内存 DayCube → ŷ_oo / ŷ_τc / rank → fuse
  ├─ 今开或风格齐的行 < 门槛（默认 LAKE_COVER_FRAC=0.5）→ 失败，不写账，不退昨日
  └─ 覆盖够 → 现价成交（现有 fill；滑点等不变），成交记录保留下单时 ŷ；占位立方只在内存
```

≤09:30：`use_minute_tau=False`，ŷ_τc 为开盘 Z，不拉当天 5 分钟线。名次、fuse、买卖闸沿用现有 `rank_lots`。

---

## 4. 9:25 之后如何算 z

与现网 DayCube / `cross_section_zscore_matrix` 同一公式（总体标准差，除以有效只数 n，不是 n−1）：`z = (x−μ)/σ`，`μ = x̄`，`σ = sqrt(mean((x−μ)^2))`。

规则：

- 分组 = 会话日 T 的开盘截面（一组）；每列只对有限值算 μ/σ，有效只数 < 2 或 σ≈0 → 该列有效值 z=0。
- **缺今开的股票整行不进 μ/σ，也不进下单名单**；禁止用 0 填开盘价再进截面。
- ŷ_oo、开盘 Z 的 ŷ_τc、相关 rank **共用同一套** 今开 raw 与 μ/σ，禁止「ŷ_oo 用今天、τ 还挂昨天」；冻结的 α、β 不变，贡献仍是 β×z。

原始值 x：日线只到昨天，今开用行情开盘价接到决策占位 bar（高=低=收=今开，量用昨量）。例：伊利 `CLOSE0` = 昨收/今开 = 28.89/29.06；`YKSFT` / `YKLEN` 仍是昨天那根 K。

---

## 5. 依赖什么、不依赖什么

| 依赖 | 早开盘是否需要 | 说明 |
|------|----------------|------|
| 截至 T−1 的前复权日线（bars.db） | **要** | `OMA60` 等约需 61+ 完整日；今开接在末根之后 |
| 当日日线 | 不要 | 早上尚未入库 |
| 当日官方开盘立方 / 当日 L1 物化 | **不要**（用于下单） | 没有则当场算；有则优先读官方 |
| 昨日官方立方 | **禁止用于成交** | 研究浏览可保留带标签的 fallback |
| 冻结 α、β 与观察池名单 | 要 | 模型配置与宇宙键，不是当场物化产物 |
| 腾讯（或东财回退）批量行情今开 | 要 | 截面宇宙 ≈ `read_cube_codes()` 全池 |

---

## 6. 耗时预期（本机实测量级）

观察池约 800 只时，一次成功路径：行情今开与日线缓存之后，全池风格因子大约十几秒，截面 z 与名次大约 2 秒。官方开盘立方已在盘上时跳过这场现算。

行情单次超时 4 s，可再试一次后东财批量回退。慢点在全池风格因子，不在截面 z。worker 窗内 30 s 一轮，等待不计入；官方开盘立方已在盘上时跳过行情与当场算因子。历史对照：旧路径整池逐票 `score_stock` 曾到数十分钟，只读昨日立方很快但错日。

---

## 7. 腾讯批量频率

**正常一天成功调仓一次，不必再单独加限流。** 已有机制：整池逗号拼接 **一次** HTTP（不是 800 次）；进程内行情缓存 TTL **60 s**，同轮打分今开与成交现价第二次命中缓存；失败时可用过期缓存保底（最长约 30 min），再东财 ulist 整批回退，禁止串行逐票拖死线程池。需要守住的：同一次 `rebalance` 内，打分用的 quotes **传给成交侧复用**，不要各拉一遍；开盘窗内反复失败时，缓存过期后约每 60 s 再打一整池——可接受，不要拆很多小批，不要在失败路径退回逐只 `get_quote`；成功后 `already_ran_today` 挡掉当日重复。

---

## 8. 影响与残余差异

### 8.1 解决什么

- 不再用「已实现的昨开→今开」ŷ_oo 开仓；tip / `lake_score_day` 与真实因子日一致，占位截面标「T 当天 · 今开截面」。
- 建不齐则拒单，去掉静默错日成交。

### 8.2 副作用 / 预期不一致

- **买卖名单**：与昨日 fallback 不同，「昨天立方 +0.22%」不会再出现；**回测不变**。
- **晚间官方立方**：日线物化后持仓刷新改读官方；量能列、用到当日高低的列（OVMA / OIMAX 等）相对早盘占位变动；成交记录保留当时 ŷ，不改写历史成交分。
- **开盘价一致时**：OMA / CLOSE0 / GAP / YKSFT / OO_MA，以及量价、波动等风格裸分，与官方立方对齐。动量 / 跳空风险 / 过热与 `raw_aoo_*` 重叠，资金流无真源，这两类不进早盘截面。
- **研究浏览 / 测试**：可保留带明确标签的昨日 fallback，**执行路径不可**；`test_materialize_consumer` 等断言须拆成浏览 vs 成交。

占位 bar 与收盘后真实日线 bar 的固有差（量、高低）应写在 tip/文档里，不要藏。

---

## 9. 打分顺序（实现契约）

1. **官方开盘立方（会话日 T）存在** → `load_day_cube` + 既有 lake 路径；`lake_score_day=T`，无 `lake_score_fallback_from`。
2. **否则** → 内存 open-pit：宇宙 = `read_cube_codes()`（约 800）；bars ≤ T−1；今开 = `batch_get_quotes` + `resolve_open_t`（只要交易日今开）；`inject_raw_aoo` / 开盘钟 τc 特征 / 训练面板同款风格裸分（窗长与 `collect_oo_panel` 抬窗后一致，今收不进 X）。风格裸名缺一列、窗没抬够、或昨收缺失的票不进截面；风格齐的行达不到覆盖门槛则整池拒单（`style_cover_short`），禁止只剩 `raw_aoo_*` 的薄向量成交。`build_day_cube` + `remember`（不落盘）返回 **cube 引用**；`_stamp_cube_head_scores` 写 ŷ_oo / ŷ_τc / fuse；标记 `lake_score_day=T`、`_score_source=open_pit`，**不得**设 fallback；simulate 把同一 `day_cube` **显式**传给 oo_rank / τc_rank。**覆盖失败** → `rebalance(dry_run=False)` 失败；研究/dry_run 可另议。产品 `rebalance` 经 `materialized_consumer()` 时走 lake / open-pit，不重新打开整池 `score_stock` 写盘。

---

## 10. 验收标准

| # | 场景 | 期望 |
|---|------|------|
| 1 | 无官方立方、行情今开齐全 | `lake_score_day=T`；CLOSE0=昨收/今开；允许下单；不再复现「用 T−1 立方却标 T」 |
| 2 | 今开覆盖不足 | 不下单；日志明确；不读昨日立方成交 |
| 3 | 官方立方已存在 | 用官方；不重建 pit；不覆盖官方文件 |
| 4 | pit 结果 | 不写入官方 `open` L2 路径 |
| 5 | 回测日 T | 仍读当日湖；未完成会话日仍被 clip，不靠昨日顶替 |
| 6 | tip | 占位：「T 当天 · 今开截面」；研究 fallback 必须写出真实因子日 |

---

## 11. 落地顺序（已完成）

已全部落地：成交闸（`refuse_day_fallback` + fallback 二次拒单）· open-pit（消费只读、返回 cube、不 persist）· tip 文案 · 单测（覆盖 / 闸 / 官方立方优先 / 不落盘 / 显式传 rank）· rank 鲁棒（simulate 传 `day_cube`，持仓补名次禁昨日回退）。可选未做：同轮 quotes 注入成交侧。

---

## 12. 关键代码索引

| 主题 | 位置 |
|------|------|
| 今开截面构建 | `core/paper/rebalance/open_pit_score.py` |
| 评分顺序 / 成交闸 / rank 传立方 | `watching_matrix._score_pool_from_lake` · `simulate_watching_matrix_preview` |
| 持仓补名次（无昨日回退） | `watching_matrix.stamp_live_ranks` · `_stamp_ranks_from_lake` |
| 消费只读 / 调仓入口 | `core/research/materialize/consumer.py`；`services/paper_trades.py` · `rebalance` · `auto_worker.py` |
| 开盘 PIT 拼接 | `alpha_oo.inject_raw_aoo` / `session_pit.resolve_open_t`；`alpha_tc.inject_raw_atc`；风格裸分 `open_pit_score._open_style_scores` |
| 截面 z / DayCube | `feature_standardize.cross_section_zscore_matrix`；`day_cube._compute_mu_sigma` |
| 观察池键 / 行情批量 | `coverage.read_cube_codes`；`quote_api.StockAPI.batch_query` · `QUOTE_MEM_TTL_SECONDS=60` |
| 回测开盘日（对照）/ tip | `lake_replay.score_open_day_entries` · `clip_replay_score_dates`；`score_tooltip.js` · `formatEodAsOfHint` |

---

## 13. 案例数据摘要（600887 · 2026-10-28）

昨收/今开/收盘 **28.89 / 29.06 / 28.69**；早盘 09:32 买 1700 @ **29.0187**（现价 29.01 × 1.0003）；早盘 tip **+0.224%** ← 10-27 立方 z，晚盘 tip **−0.236%** ← 10-28 立方 z；10-28 立方 `n_codes=798`、当晚物化，gaps 伊利 ≈ 29.06/28.89−1。排查时已排除：行情今开 ≠ 日线开盘、复权改写伊利 10-27 z、用现价当开盘价进因子。

---

## 14. 状态与下一步

- 文档与方案：本文；代码已落地并做 rank 鲁棒性加固——`open_pit_score.py` 内存截面不落盘并返回 cube 引用；`_score_pool_from_lake` 按「会话日立方 → open-pit →（浏览才）昨日回退」出分，`refuse_day_fallback` 禁成交回退；simulate 显式 `day_cube=` 传给 oo/τc rank，持仓补名次不回退昨日；tip 识别 `open_pit` / fallback；单测 `tests/test_open_pit_score.py` + `ConsumerCoverageTest` 闸断言。
- 下一步：开盘窗实盘观察一次；若需同轮 quotes 注入成交可再补。
