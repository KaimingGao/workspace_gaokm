# services

应用服务层：Web / CLI 与领域逻辑之间的 API 边界。

## 模块

| 模块 | 说明 |
|------|------|
| `chat_service.py` | 会话与 Agent 生命周期 |
| `paper_service.py` | **模拟账户**（内部名 paper）：CRUD / 调仓 / 做 T / 成本模型 |
| `watching_service.py` | **观察名单**：搜索 / 行情 / 舆情 / 增删 |
| `daily_service.py` | daily preset 编排（投顾 + 量化） |
| `eval_service.py` | Web evals API 与 checklist 封装 |
| `platform_service.py` | D1–D6：Job/Memory/Decision/Feedback/Schedule/Prefill |

## 约定

- 量化实现见 `quant/`（动作归属见 `quant/services/action_map.py`）；此处不含 quant 业务逻辑
- Web 路由在 `web/app.py` 调用这些 Service

## 相关文档

- [架构总览 · 子目录索引](../docs/architecture.md#子目录-readme-索引)
