# adapters/market

市场数据出站适配器。

| 模块 | 职责 |
|------|------|
| `quote_api.py` | 腾讯 / 东财现价 |
| `history.py` | 日线拉取与 normalize |
| `minute_history.py` | 分钟线编排（东财 → 新浪/腾讯 → BaoStock） |
| `index_bars.py` | 指数日线 |
| `stock_search.py` | 名称/代码搜索 |
| `ak_worker.py` | AkShare 进程池 |

AkShare 串行锁：`core.data.ak_lock`（进程内串行；不再在 adapters 层 re-export）。

绑定入口：`adapters.bind.bind_market_adapters`。
