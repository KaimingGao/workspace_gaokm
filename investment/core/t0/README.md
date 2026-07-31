# core/t0

底仓做 T **模拟**（A 股 T+1 语义）。

| 模块 | 说明 |
|------|------|
| `config.py` | 默认规则：`fill_mode=trigger`、`direction=auto`、ATR 阈值、`path_mode` |
| `rules.py` | 单日正/反 T · 振幅门禁 · 纸面预演 `dry_run` · 开盘 signal 选向 |
| `minute_path.py` | **5 分钟第一触达**路径（有分钟线时） |
| `backtest.py` | 日线 walk-forward · 可选分钟覆盖 · optimistic / 日线对照 |

**数据**：日线 OHLC + 可选东财 5m（约近 120 交易日）；缺分钟回退日线 `veto`。**不接实盘、不代客下单。**

## 相关

- CLI：`research/t0_backtest_run.py`
- API：`POST /api/quant/t0-backtest`（`use_minute=true` 默认）· `POST /api/paper/t0`
- Agent：`quant(task=t0_backtest)`
- [quant.md](../../docs/quant.md) · [架构索引](../../docs/architecture.md#子目录-readme-索引)
