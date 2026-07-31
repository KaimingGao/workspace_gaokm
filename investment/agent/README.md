# agent

Agent 编排层：LLM 对话、Skill 注册、路由与合规提示词。

> 原目录名 `advisor/` 已改名为 `agent/`；兼容转发包已删除，请只 `import agent.*`。

## 职责

- 多轮 Function Calling 循环（`agent.py`）
- 单一 Skill 注册表（`registry.py`）
- 问题路由与 quant task 推断（`routing.py`）
- 通义千问 Chat Completions 客户端（`llm_client.py`，支持联网搜索）
- 系统提示词与免责声明（`prompts.py`）

## 主要文件

| 文件 | 说明 |
|------|------|
| `agent.py` | `InvestmentAgent`：tool loop、会话、免责声明注入 |
| `registry.py` | `SKILL_SPECS` 与 Handler 延迟加载 |
| `routing.py` | golden case 路由、quant task 推断 |
| `contracts.py` | `SkillHandler` 协议与 `BaseSkillHandler` |

## 相关文档

- [architecture.md](../docs/architecture.md)
- [skills.md](../docs/skills.md)
- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
