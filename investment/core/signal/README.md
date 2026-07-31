# core/signal

短线因子打分与横截面排序（与 advise / backtest / paper 同一套 scorer）。

## 主要文件

| 文件 | 说明 |
|------|------|
| `scorer.py` | `score_bars` / `rank_candidates` |
| `score_stock.py` | 单票评分（Signal / Position Skill 共用） |
| `factor_registry.py` | 因子权重与注册 |
| `neutralize.py` | 横截面 sub_scores 中性化（P47） |
| `cross_section_batch.py` | 调仓日批量打分 + 中性化排序（P49） |
| `factor_panel.py` | 因子注册表 + 权重 + IC 面板（P48） |
| `cross_section.py` | watching 批量打分 + Top N |
| `threshold_suggest.py` | stance 阈值 OOS 建议 |

## 子目录

- [factors/](factors/README.md) — 动量、量价、波动、相对强弱等因子实现

## 配置

- `data/signal_config.json` — 因子权重与 stance 阈值（只读预览见 quant Skill `config_diff`）

## 相关文档

- [策略层 · 设计模板](../../docs/strategy-layer.md)
- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
