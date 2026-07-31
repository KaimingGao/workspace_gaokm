# skills

Agent 工具 Skill：每个子目录 = `handler` + `engine` + `tool_config.json`。

## 结构

```
skills/<name>/
  handler.py       # BaseSkillHandler · execute()
  engine.py        # 领域逻辑
  tool_config.json # LLM function schema
```

## 注册

单一注册表：[`agent/registry.py`](../agent/registry.py) · `SKILL_SPECS`

## 共享数据层

- [common/](common/README.md) — 行情 API、日线 history

## 量化

- Agent 注册 `quant.skill.handler.QuantHandler`
- [quant/](quant/README.md) — 仅 tool_config + `QuantEngine` 重导出

## 相关文档

- [skills.md](../docs/skills.md)
- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
