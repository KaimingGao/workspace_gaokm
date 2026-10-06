# adapters

出站适配器层：外部数据源 I/O，经 `adapters.bind` 注入 `core.ports.registry`。

| 包 | 职责 |
|----|------|
| `market/` | 行情 · 日线 · 分钟线 · 指数 · 搜索 |
| `news/` | 资讯拉取 |
| `fundamentals/` | 基本面 / 估值 / 财务序列 |
| `macro/` | 宏观快照 |
| `sentiment/` | 市场情绪（涨停池等） |
| `announcement/` | 公告扫描 · 概念图谱 · IPO 虹吸 |
| `screen/` | A 股现货与条件选股 |
| `peer/` · `index/` · `kline/` · `signal/` | 同行 / 相对强弱 / K 线 / 观察池编排 |
| `bind.py` | 默认实现登记 |

Skills（`skills/*/`) 只保留 handler + 兼容 re-export；不再充当底层 fetch。
