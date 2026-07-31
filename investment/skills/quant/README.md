# skills/quant

Agent `quant` 工具的注册壳（P41 shim 已移除）。

## 内容

| 文件 | 说明 |
|------|------|
| `tool_config.json` | LLM function schema（task 枚举 + 参数） |
| `__init__.py` | 重导出 `QuantEngine` |

## 实现

- 引擎与 Handler：`quant/skill/`
- 注册：`quant.skill.handler.QuantHandler`

## 常用 task

`portfolio_backtest` · `portfolio_neutral_compare` · `daily_summary` · `interpret` · `health` · `package_info` · `config_diff` 等

## 关键参数（P68）

| 参数 | 适用 task | 说明 |
|------|-----------|------|
| `offline` | `interpret` | `true` 时走规则解读（无 LLM，golden/CI） |
| `use_saved` | `interpret` · `daily_summary` | 优先读 `quant_daily.json` |
| `include_portfolio_neutral_compare` | `daily_summary` | 是否含中性化 vs 绝对分专节 |
| `include_portfolio_backtest` | `daily_summary` | 是否含组合回测摘要 |

## 相关文档

- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
