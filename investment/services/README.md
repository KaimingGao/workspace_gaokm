# services — Application Service 层

Web/CLI 与领域逻辑之间的**用例组装**边界（不是微服务、不是 Domain Facade）。

| 模块 | 职责 |
|------|------|
| `paper_service`（+ account / jobs / trades / helpers） | 纸面账户、买卖、调仓 Job |
| `watching_service` | 观察名单 |
| `chat_service` | 会话 |
| `daily_service` | 每日任务编排 |
| `eval_service` | Golden eval |
| `platform_service` | Job / Memory / Decision / Schedule 等平台能力 |

**向下**：经 **DS / SS / BS**（`core/*_service.py`，Domain Facade）进入 `core/`，不直碰 store / 外部源。

**并列**：研究台 **Application Service** 在 `quant/services/QuantService`（文档与架构图称「应用服务」，不叫领域门面）。

命名约定：[architecture · Service 命名约定](../docs/architecture.md#service-命名约定) · 目录结构见 [docs/architecture.md § 代码目录结构](../docs/architecture.md)
