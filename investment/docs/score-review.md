# 昨日复盘（Score Review）

[← 文档索引](README.md)

对账 **决策日 `as_of` 的 ŷ 方向** 与 **h 日实现收益**，解释错票（因子失效 / 个股特异 / 数据不足）。不改权、不 promote。

## 口径

- `as_of`：打分决策日（站在哪一天预测）
- `r_h`：`(close[as_of+h] / close[as_of] - 1) * 100`（h 默认 3，可切 1）
- 方向错：`sign(ŷ) ≠ sign(r)`；`|ŷ| < 0.05%` → 无方向

## 数据

| 路径 | 内容 |
|------|------|
| `data/reports/score_ledger/YYYYMMDD.json` | 冻结 ŷ / top 因子分解 |
| `data/reports/score_ledger/YYYYMMDD.outcomes.json` | realized / sign_hit |

写入触发：集群书刷新、生成日报、UI「冻结今日打分」。

## API

- `GET /api/quant/score-review?as_of=&horizon_days=&autofill=`
- `POST /api/quant/score-ledger/freeze`
- `POST /api/quant/score-outcomes/fill`
- `GET /api/quant/score-review/dates`

## UI

研究台 → **昨日复盘**：选决策日 / horizon → 刷新复盘 · 回填收益 · 冻结今日打分。
