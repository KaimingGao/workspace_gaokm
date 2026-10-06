# quant/services — QuantService（Application Service）

量化研究台的**用例组装**门面（Web `/api/quant/*` · daily · research CLI 共用）。

| 类型 | 说明 |
|------|------|
| **本目录** | Application Service — `QuantService` Mixin 组装模拟 / 回溯 / 联动 / 因子 / ops |
| **不是** | Domain Facade — 读数 / 打分 / 回测出口在 `core/data_service` · `signal_service` · `backtest_service`（DS · SS · BS） |

`QuantService` 向下调 DS/SS/BS 与 `PaperService`，不替代领域门面。

命名约定：[architecture · Service 命名约定](../../docs/architecture.md#service-命名约定)
