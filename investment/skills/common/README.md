# skills/common

跨 Skill 共享的数据层（行情与历史 K 线）。

## 主要文件

| 文件 | 说明 |
|------|------|
| `quote_api.py` | 腾讯财经 `StockAPI` |
| `history.py` | A/港/美日线拉取 + 本地 cache 回退 |

## 调用方

`quote`、`compare`、`signal`、`kline`、`backtest` 等 Skill。

## 相关文档

- [数据层说明](../../docs/data-layer.md)
- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
