# quant/research

纯 Python 量化研究模块（因子 IC、TopK 回测摘要）。

## 主要模块

| 模块 | 说明 |
|------|------|
| `factor_report.py` | 横截面 IC/IR 报告 |
| `portfolio_data.py` | TopK 回测日线加载 + 日报摘要 + 基本面批量 |
| `t0_backtest.py` | 底仓做 T 回测封装 |
| `portfolio_neutral_compare.py` | 中性化 vs 绝对分对照（P52） |

## CLI 入口

实现逻辑在此；可执行脚本在 [`research/`](../research/README.md)。

## 相关文档

- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
