# core/backtest

Walk-forward 回测引擎与组合模拟（live / paper / quant 共用）。

## 主要文件

| 文件 | 说明 |
|------|------|
| `engine.py` | 单票/策略回测主入口 |
| `topk_backtest.py` | 横截面 TopK 调仓回测（默认等权；可选 score_budget / risk_parity_lite） |
| `costs.py` | 佣金/滑点/冲击；**组合按换手计费**（`rebalance_cost_pct`） |
| `cost_port.py` | **CostPort**：simple_cn 费率唯一权威源（纸面/回测/因子同源） |
| `matching.py` | **MatchPort**：涨跌停 / T+1 / 滑点档（非费率） |

## 调用方

- `skills/backtest/` Agent 工具
- `quant/services/quant_service.py` TopK 组合回测
- `research/*_run.py` CLI

## 相关文档

- [topk-backtest-upgrade.md](../../docs/topk-backtest-upgrade.md)（T0–T4）
- [quant.md](../../docs/quant.md)
- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
