# 昨日复盘（Score Review）

[← 文档索引](README.md) · 全链路见 [predicted-score-chain.md](predicted-score-chain.md)

对账 **决策日 `as_of` 的 ŷ 方向** 与 **h 日实现收益**，解释错票（因子失效 / 个股特异 / 行业 / 分组）。不改权、不 promote。

**产品定位**：复盘是 **分池簿（`book`）的 OOS 体检**——检验当天入选截断（门槛 / TopN / 组内排序）是否合理；**不是**全池、也不是纸面持仓账的总复盘。

---

## 1. 口径

| 符号 | 含义 |
|------|------|
| `as_of` | 打分决策日（站在哪一天预测；须对齐因子截止，见下） |
| \(h\) | `horizon_days`（默认 3，UI 可切 1；应与 live / 分组同一 \(h\)） |
| \(r_h\) | \((\mathrm{close}[as\_of+h] / \mathrm{close}[as\_of] - 1) \times 100\) |
| 方向命中 | \(\mathrm{sign}(\hat y)=\mathrm{sign}(r_h)\)；\(\lvert\hat y\rvert < 0.05\%\) → **无方向**（不计入命中分母的「有方向样本」） |

实现：`core/score_ledger.py` · `_realized_from_bars` / `build_score_review`。

---

## 2. 覆盖范围（只覆盖簿）

| 集合 | 是否进默认账本 / 复盘 | 说明 |
|------|------------------------|------|
| 分池簿 `cluster_book_active.book` | **是** | `freeze_from_cluster_book` 只写 `book` |
| `scored_all`（打过分的更广集合） | **否** | 未过门槛 / 未进 Top 截断的票不进默认复盘 |
| 纸面 `paper.json` 持仓 | **否** | 属 Realization / 持有纪律，见纸面归因轨 |
| 观察池 watching | **否**（除非当日也在 `book`） | 看 ŷ 时间线可用 ledger series，不自动扩宇宙 |

冻结后账本行数 = 当日 `book` 只数（例：`min_score` + 全局排序后常远小于 `scored_all`）。

**为何不并入持仓**：簿复盘回答「选进来的对不对」；持仓里常有曾进簿后掉出、或手动留下的票。若与簿样本混算同一命中率，会稀释选股 IC、模糊截断好坏。持仓补分（调仓 `_supplement_holding_scores`）与账本冻结刻意不同源。

两类样本对照（与主轴文档一致）：

| 轨 | 数据 | 回答的问题 |
|----|------|------------|
| **A. 预测–实现账本** | `score_ledger` + outcomes（= **簿**） | ŷ / 入选簿准不准 |
| **B. 纸面决策轨迹** | 持仓、调仓、成本、净值 | 做得对不对、能否落地 |

---

## 3. 冻结决策日与日线前置

### 3.1 `resolve_freeze_as_of`

- 默认对齐本地日线多数末根（`infer_feature_as_of`）或上一交易日。
- 请求的 `as_of` **晚于**因子截止 → **下调**到因子截止；禁止「会话日标签 + 昨收因子」。
- 会话日账本在 UI chip 标 **未到期**（`immature`）；复盘默认不选未到期日。

### 3.2 日线何时才有「今天」

A 股日线主源 `stock_zh_a_hist`：**当日收盘价请在收盘后获取**。

| 拉取时机 | 可靠末根 | 能否冻成会话日 \(T\) |
|----------|----------|----------------------|
| \(T\) 日闭市前 | 通常 **\(T\!-\!1\)** | 否（会 remap 到 \(T\!-\!1\)） |
| \(T\) 日收盘后（实务常 16:30–17:00+） | 可含 **\(T\)** | 是（本地 `date_max≥T`） |

注意：缓存未过期且条数够时，`bars_warmup` / 普通 `get_bars` 可能**不补**「缺今天这根」的缺口；收盘后若要用 \(T\) 冻账本，需确认宇宙 `date_max` 已到 \(T\)（必要时对末根偏旧的票强制增量补拉）。

### 3.3 薄样本（`data_thin`）

回填需要本地日线同时有 `close[as_of]` 与 `close[as_of+h]`。

- 刚冻的 **会话日** 账本：即使 h=1，也要等 **下一交易日收盘** 才能对账 → UI：`薄样本 N · as_of+h 日线未到，请选更早决策日`。
- h>1 全部薄样本时，后端可自动降到 **h=1** 再试（`horizon_fallback_from`）；若 h=1 仍薄，只能选更早决策日或等日线。
- 「刷新日线」解决的是**已到期**决策日缺 bar；**不能**让未到期的 as_of+h 提前出现。

---

## 4. 数据与写入

| 路径 | 内容 |
|------|------|
| `data/reports/score_ledger/YYYYMMDD.json` | 冻结 ŷ / top 因子分解 / sector / cluster（来源多为 `cluster_book`） |
| `data/reports/score_ledger/YYYYMMDD.outcomes.json` | `realized_h` / `sign_hit` / 薄样本缺失计数 |

写入触发：

1. **纸面日更** `run_paper_daily` → `run_score_ledger_daily`（按因子截止冻结 + 回填到期决策日）
2. 集群书刷新、生成日报（经 `resolve_freeze_as_of`）
3. UI「冻结打分」→ `freeze_from_cluster_book`（**仅 `book`**）

可选：`freeze_from_daily_report` 可从日报 `book_top` / 横截面补行；仍非持仓并集。

---

## 5. API

- `GET /api/quant/score-review?as_of=&horizon_days=&autofill=`
- `POST /api/quant/score-ledger/freeze`
- `POST /api/quant/score-outcomes/fill`
- `GET /api/quant/score-review/dates`
- `GET /api/quant/score-ledger/series?code=` — 单票 ŷ 跨日
- `GET /api/quant/score-review/hit-series?horizon_days=&limit=` — 命中率序列

复盘响应含错票归因字段（`factor_blame` 等，API 仍返回）；UI 以 **复盘样本表** 展示主导因子 / 行业 / 分组，不再单独渲染聚合归因表。

---

## 6. UI（研究枢纽）

- **昨日复盘**：选决策日 / horizon → 刷新 · 回填 · 冻结 · **建议重估**（滚动并触发「跑分组」）
- **ŷ 散点**：`scored_rows` → ŷ vs 实现收益（绿=方向对 / 红=错）
- **回测–纸面拟合**：`POST /api/ops/fit-gap` 常驻；启发式 hints + 同窗日 Diff

### ŷ 可视化（同轨）

| 面 | 内容 |
|----|------|
| 评分 tooltip | 因子 **贡献条**（∝\|β·z\|）+ 原拆解表 |
| `/watching` | 观察池 ŷ **直方图** + 买/持门槛竖线；点票看 **ŷ 时间线**（账本） |
| `/quant` 复盘 | ŷ–实现 **散点**；跨日 **命中率 sparkline** |
| `/quant` 分组 | 组头 **ŷ strip**（组内排序分布） |
| `/replay` IC | **零轴**参考线 |

---

## 7. 运维速查

| 现象 | 含义 | 怎么做 |
|------|------|--------|
| 薄样本 = 账本只数，命中 — | as_of+h 日线未到（常为冻了今天） | 选更早决策日；或等下一交易日后再回填 |
| 冻今天被下调到昨天 | 本地无今日完整日线 | 收盘后刷日线再冻 |
| 账本只有几只 | 等于当日 `book`，不是全池 | 正常；看 `scored_all` 只数对比门槛 |
| 持仓票不在复盘表 | 设计如此 | 用纸面持仓 / 归因看 Realization |
