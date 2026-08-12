# 昨日复盘（Score Review）

[← 文档索引](README.md) · 全链路见 [predicted-score-chain.md](predicted-score-chain.md)

对账 **决策日 `as_of` 的 ŷ 方向** 与 **h 日实现收益**，解释错票（因子失效 / 个股特异 / 行业 / 分组）。不改权、不 promote。

## 口径

- `as_of`：打分决策日（站在哪一天预测）
- `r_h`：`(close[as_of+h] / close[as_of] - 1) * 100`（h 默认 3，可切 1）
- 方向错：`sign(ŷ) ≠ sign(r)`；`|ŷ| < 0.05%` → 无方向

## 数据

| 路径 | 内容 |
|------|------|
| `data/reports/score_ledger/YYYYMMDD.json` | 冻结 ŷ / top 因子分解 / sector / cluster |
| `data/reports/score_ledger/YYYYMMDD.outcomes.json` | realized / sign_hit |

写入触发：

1. **纸面日更** `run_paper_daily` → `run_score_ledger_daily`（冻结今日 + 回填到期决策日）
2. 集群书刷新、生成日报
3. UI「冻结今日打分」

## API

- `GET /api/quant/score-review?as_of=&horizon_days=&autofill=`
- `POST /api/quant/score-ledger/freeze`
- `POST /api/quant/score-outcomes/fill`
- `GET /api/quant/score-review/dates`

复盘响应含：`factor_blame` · `industry_blame` · `cluster_blame` · `refit_hint`。

## UI（研究枢纽）

- **昨日复盘**：选决策日 / horizon → 刷新 · 回填 · 冻结 · **建议重估**（滚动并触发「跑分组」）
- **ŷ 散点**：`scored_rows` → ŷ vs 实现收益（绿=方向对 / 红=错）
- **回测–纸面拟合**：`POST /api/ops/fit-gap` 常驻；启发式 hints + 同窗日 Diff

## ŷ 可视化（同轨）

| 面 | 内容 |
|----|------|
| 评分 tooltip | 因子 **贡献条**（∝\|β·z\|）+ 原拆解表 |
| `/watching` | 观察池 ŷ **直方图** + 买/持门槛竖线；点票看 **ŷ 时间线**（账本） |
| `/quant` 复盘 | ŷ–实现 **散点**；跨日 **命中率 sparkline** |
| `/quant` 分组 | 组头 **ŷ strip**（组内排序分布） |
| `/replay` IC | **零轴**参考线 |

### API

- `GET /api/quant/score-ledger/series?code=` — 单票 ŷ 跨日
- `GET /api/quant/score-review/hit-series?horizon_days=&limit=` — 命中率序列

