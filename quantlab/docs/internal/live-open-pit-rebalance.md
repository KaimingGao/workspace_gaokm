# Live 早开盘调仓：今开截面 PIT 方案

[← 文档索引](../README.md) · 调仓产品见 [rebalance-logic.md](../rebalance-logic.md) · L1/L2 见 [feature-lake-daycube.md](./feature-lake-daycube.md) · 宇宙键见 [daycube-universe-key.md](./daycube-universe-key.md)

> 状态：**现行**（2026-11-09）拟合 / 回测 / live **只读会话日 L1/L2**；缺 open 立方 → `materialize_required` → `do_session_open_l2` 落盘后再消费。§1 保留早盘事故根因备忘。  
> 范围：live 开盘窗矩阵调仓打分与下单闸  
> 不覆盖：模型重训、回测 `_score_open_day` 改写、实盘 OMS、>09:30 分钟钟

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
| **现行** live / 手动 | **仅**会话日官方 L2 | 缺则 `materialize_required`；产品不回退昨收、不临时补数 |

结论：回测本无此错位；live 开盘钟已按上表修好。复权不是本次 ŷ_oo 翻转的原因（伊利 10-27 立方在晚间重写后 z 仍与早盘 tip 一致）。

---

## 2. 现行目标

- 决策日 = 会话日 T；ŷ_oo = **今开 → 下一交易日开开**；特征 = **≤T−1 日线** + **T 日行情今开**，μ/σ 来自官方 open DayCube。
- 官方 open L2 已在盘上 → 消费侧直接读；缺失 → `materialize_required`（可 `do_session_open_l2` 正式落盘后重试）。
- **禁止**调仓线程临时拼截面、禁止昨收回退成交；回测逻辑不变。

---

## 3. 早开盘时序（现行）

自动 worker：交易日 `live_fill_clock`（可 09:25）～10:00，窗内 30s 一轮；当天成功调仓后不再跑。

```text
窗开且当天未跑
  ├─ 有未完成隔夜挂单 → 本轮不算新分；读会话日 T 官方开盘立方 → fuse / 下单
  ├─ 有官方 open L2 → 读立方出分 → fuse / 下单
  ├─ 无 → materialize_required → do_session_open_l2（行情今开 + ≤T−1 日线 → 官方 open DayCube）
  └─ 物化成功后重试消费；覆盖不足 / 行情缺今开 → 失败，不写账，不退昨日
```

≤09:30：`use_minute_tau=False`，ŷ_τc 为开盘 Z。名次、fuse、买卖闸沿用 `rank_lots`。

---

## 4. 截面 z（官方立方 / 物化写盘共用）

与 DayCube / `cross_section_zscore_matrix` 同一公式：`z = (x−μ)/σ`（总体标准差）。

- 分组 = 会话日 T 开盘截面；缺今开整行不进 μ/σ。
- 空今开行情短 TTL（约 5s）；`session_open` 拉截面前 `invalidate_codes`，仍缺今开再重拉。
- ŷ_oo / 开盘 ŷ_τc / rank 共用同一套 raw 与 μ/σ。

---

## 5. 依赖

| 依赖 | 说明 |
|------|------|
| ≤T−1 前复权日线 | 物化写盘与风格因子需要 |
| 腾讯/东财批量今开 | 仅物化补齐路径；消费只读立方 |
| 会话日官方 open L2 | **消费必需**；缺则先物化 |
| 昨日官方立方 | **禁止成交** |
| 冻结 α、β 与观察池 | 模型与宇宙键 |

---

## 6. 耗时

官方立方已在盘上：跳过行情与因子现算，只读 L2 出分。物化补齐：全池风格因子约十几秒量级（池约 800）。worker 窗内 30s 一轮；成功后 `already_ran_today` 挡当日重复。

---

## 7. 行情批量

整池一次 HTTP；缓存 TTL 约 60s（空今开约 5s）。物化拉今开前 `invalidate_codes`；禁止失败路径退回逐只 `get_quote`。东财 ulist 整批回退时注意 chunk。

---

## 8. 影响

- 不再用「已实现的昨开→今开」ŷ_oo 开仓；缺立方拒单，去掉静默错日成交。
- **历史 tip**：旧行 `lake_score_fallback_from` / `_score_source=open_pit` 仍可展示；新路径写官方立方后消费为 `day_cube`。
- 晚间日线物化后与盘中 `do_session_open_l2` 同口径读官方 open；成交记录保留下单时 ŷ。

---

## 9. 打分顺序（实现契约）

1. **官方开盘立方（会话日 T）存在** → `load_day_cube` + lake 路径；`lake_score_day=T`，无 `lake_score_fallback_from`。simulate 把同一 `day_cube` 显式传给 oo_rank / τc_rank。
2. **否则** → `materialize_required`（开盘钟带 `do_session_open_l2`）。**禁止**消费侧临时拼截面、禁止昨收回退。正式补齐由 `session_open` 物化写官方 open L2 后再重试；`open_pit_score.py` 只服务该物化写盘，不进调仓出分。

---

## 10. 验收标准

| # | 场景 | 期望 |
|---|------|------|
| 1 | 无官方立方 | `materialize_required`；可经 `do_session_open_l2` 落盘后重试；不读昨日立方成交 |
| 2 | 今开覆盖不足（物化时） | 物化失败 / 不下单；日志明确 |
| 3 | 官方立方已存在 | 用官方；不临时拼截面 |
| 4 | 会话日 open 物化 | 写入官方 `open` L2；消费侧只读 |
| 5 | 回测日 T | 仍读当日湖；未完成会话日仍被 clip，不靠昨日顶替 |
| 6 | tip | 历史 fallback / open_pit 标记仍可展示；新出分无 fallback |

---

## 11. 落地顺序（已完成）

已全部落地：消费只读会话日官方 L2 · tip 仍识别历史 fallback 标记 · 单测（覆盖 / 闸 / 官方立方优先 / 显式传 rank）· rank 鲁棒（simulate 传 `day_cube`）。正式补齐走 `do_session_open_l2` 落盘。

---

## 12. 关键代码索引

| 主题 | 位置 |
|------|------|
| 今开截面构建 | `core/paper/rebalance/open_pit_score.py` |
| 评分顺序 / 成交闸 / rank 传立方 | `watching_matrix._score_pool_from_lake` · `simulate_watching_matrix_preview` |
| 持仓补名次（无昨日回退） | `watching_matrix.stamp_live_ranks` · `_stamp_ranks_from_lake` |
| 当日名次快照 + 池指纹 | `save/load/ensure_live_rank_snapshot`（`universe_fp` + 覆盖闸）· `attach_and_persist_live_ranks` · `refresh_live_open_ranks`；成员变更 / 档位升降级 → `invalidate`；desk/stamp 缺快照可自愈 |
| 消费只读 / 调仓入口 | `core/research/materialize/consumer.py`；`services/paper_trades.py` · `rebalance` · `auto_worker.py` |
| 开盘 PIT 拼接 | `alpha_oo.inject_raw_aoo` / `session_pit.resolve_open_t`；`alpha_tc.inject_raw_atc`；风格裸分 `open_pit_score._open_style_scores` |
| 截面 z / DayCube | `feature_standardize.cross_section_zscore_matrix`；`day_cube._compute_mu_sigma` |
| 观察池键 / 行情批量 | `coverage.read_cube_codes`；`quote_api.StockAPI.batch_query` · `QUOTE_MEM_TTL_SECONDS=60` / `QUOTE_EMPTY_OPEN_TTL_SECONDS=5`；开盘截面前 `invalidate_codes` + 缺今开重拉 |
| 回测开盘日（对照）/ tip | `lake_replay.score_open_day_entries` · `clip_replay_score_dates`；`score_tooltip.js` · `formatEodAsOfHint` |

---

## 13. 案例数据摘要（600887 · 2026-10-28）

昨收/今开/收盘 **28.89 / 29.06 / 28.69**；早盘 09:32 买 1700 @ **29.0187**（现价 29.01 × 1.0003）；早盘 tip **+0.224%** ← 10-27 立方 z，晚盘 tip **−0.236%** ← 10-28 立方 z；10-28 立方 `n_codes=798`、当晚物化，gaps 伊利 ≈ 29.06/28.89−1。排查时已排除：行情今开 ≠ 日线开盘、复权改写伊利 10-27 z、用现价当开盘价进因子。

---

## 14. 状态与下一步

- 文档与方案：本文；消费侧只读会话日官方 L2；simulate 显式 `day_cube=` 传给 oo/τc rank；单测 `tests/test_open_pit_score.py` + `ConsumerCoverageTest` 闸断言。`open_pit_score.py` 仍供正式 `do_session_open_l2` 物化写盘使用，不在调仓线程临时拼截面。
- **2026-11-06**：持仓盯市空今开 60s 缓存导致昨持仓（风华/江海）被判缺今开、A 档名次池 598 vs 600；已用 open-pit 前 `invalidate_codes` 修掉（`TestOpenPitQuoteCacheBust`）。
- **2026-11-09**：持仓表 `oo_rank`/`τc_rank` 曾卡在旧截面（如 n=484）；根因是 `live_rank_snapshot` 未随观察池/A 档更新。已绑 `universe_fp`：成员变更作废；缺/失效快照由 `ensure` 自愈；快照只落核心名次（不含公式拆解）。
- **2026-11-09（续）**：`refresh_live_open_ranks` 曾回退昨收立方却仍按今日 `as_of` 落盘，持仓名次与手动开盘复现错位。已改为只认会话日官方立方。
- **2026-11-09（再续）**：消费路径取消 open-pit 临时补数。`_score_pool_from_lake` / `refresh_live_open_ranks` 缺会话日 L2 一律 `materialize_required`；与拟合、回测同口径只读物化层。
- **2026-11-09（严口径）**：手动执行 / 预演 / `score_session_from_lake` 缺覆盖只提示物化，禁止昨收回退掩盖 L1/L2 缺口。
- **2026-11-09（盘中补齐）**：live / 手动缺会话日 open L2 时，经 `do_session_open_l2` **正式物化落盘**（今开行情 + ≤T−1 日线 → 官方 open DayCube）；消费侧仍只读立方。自动调仓 worker、手动 09:30 复现、持仓补 ŷ 会触发补齐后重试。
- **2026-11-09（清理）**：删除消费侧 `_lake_score_day_candidates` / `_resolve_lake_score_day` / hole-fill / `refuse_day_fallback` 参数与 `_try_score_pool_from_open_pit` stub；出分只读会话日立方。
- **2026-11-09（深清）**：删除死 API `score_open_pit_pool`；物化中间行 `_score_source=session_open_build`；文档「现行」改为只读 L2 + `do_session_open_l2`。
- 下一步：盘中需开盘截面时，走会话日 open L2 物化补齐后再消费。
