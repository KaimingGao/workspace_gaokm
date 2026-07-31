# quant/skill

Agent `quant` 工具：将自然语言路由为 `task=` 并调用 `QuantService`。

## 主要文件

| 文件 | 说明 |
|------|------|
| `engine.py` | `QuantEngine` · `AVAILABLE_TASKS` |
| `handler.py` | `QuantHandler` · 注册于 `agent/registry.py` |

## 注册

```python
("quant", "quant.skill.handler.QuantHandler")
```

`skills/quant/` 仅保留 `tool_config.json` 与 `__init__.py` 重导出。

## 配置

- `skills/quant/tool_config.json` — LLM function schema

## 相关文档

- [架构总览 · 子目录索引](../../docs/architecture.md#子目录-readme-索引)
